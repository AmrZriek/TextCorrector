"""
update.py — TextCorrector dependency updater
=============================================
Updates all Python dependencies and optionally downloads the latest
llama-server binaries for the current platform.

Usage
-----
    python update.py             # update Python deps only
    python update.py --llama     # also update llama-server binary
    python update.py --all       # update everything

What it does
------------
1. Upgrades pip itself
2. Installs / upgrades all packages from requirements.txt
3. (Optional) Downloads the latest llama.cpp release binary for your OS/arch
"""

import sys, os, subprocess, platform, urllib.request, zipfile, tarfile, shutil, json
from pathlib import Path

ROOT      = Path(__file__).parent.resolve()
VENV_PY   = ROOT / "venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
REQ_FILE  = ROOT / "requirements.txt"
LLAMA_DIR = ROOT / "llama_cpp"

GITHUB_API = "https://api.github.com/repos/ggerganov/llama.cpp/releases/latest"

# ── Helpers ───────────────────────────────────────────────────────────────────
def banner(msg: str):
    print(f"\n{'─'*60}\n  {msg}\n{'─'*60}")


def run(cmd: list, **kw):
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    subprocess.run(cmd, check=True, **kw)


def pip_path() -> str:
    """Return the right pip executable (venv > system)."""
    if VENV_PY.exists():
        return str(VENV_PY)
    return sys.executable


# ── Python dependencies ───────────────────────────────────────────────────────
def update_python_deps():
    banner("Updating Python dependencies")
    py = pip_path()

    # Upgrade pip first
    run([py, "-m", "pip", "install", "--upgrade", "pip"])

    if not REQ_FILE.exists():
        print(f"  requirements.txt not found at {REQ_FILE}")
        return

    # Upgrade all packages listed in requirements.txt
    run([py, "-m", "pip", "install", "--upgrade", "-r", str(REQ_FILE)])
    print("  All packages up to date.")


# ── llama.cpp binary ─────────────────────────────────────────────────────────
def _detect_asset_keyword() -> str:
    """Pick the right release asset keyword for this platform/arch."""
    arch = platform.machine().lower()
    is_arm = arch in ("arm64", "aarch64")

    if sys.platform == "win32":
        # Prefer CUDA 12.x for broadest driver compatibility.
        # Falls back to cpu-x64 if no NVIDIA GPU is present.
        return "win-cuda-12" if _has_nvidia() else "win-cpu-x64"
    elif sys.platform == "darwin":
        return "macos-arm64" if is_arm else "macos-x86_64"
    else:  # Linux
        return "ubuntu-x64" if not is_arm else "ubuntu-arm64"


def _has_nvidia() -> bool:
    try:
        r = subprocess.run(["nvidia-smi"], capture_output=True, timeout=4)
        return r.returncode == 0
    except Exception: return False


def _has_avx2() -> bool:
    try:
        import cpuinfo  # type: ignore
        return "avx2" in cpuinfo.get_cpu_info().get("flags", [])
    except Exception: return True  # assume modern CPU


def _extract_zip_to(zip_path: Path, dest_dir: Path):
    """Extract llama binaries + all DLLs from a zip into dest_dir."""
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            mname = Path(member).name
            if not mname:
                continue
            is_binary = mname.startswith("llama-") or mname.startswith("rpc-")
            is_lib    = mname.endswith((".dll", ".so", ".dylib"))
            if is_binary or is_lib:
                data_bytes = zf.read(member)
                dest = dest_dir / mname
                dest.write_bytes(data_bytes)
                if sys.platform != "win32":
                    dest.chmod(0o755)
                print(f"    Extracted: {mname}")


def update_llama():
    banner("Updating llama.cpp server binary")
    print(f"  Fetching latest release info from GitHub…")

    try:
        req = urllib.request.Request(GITHUB_API,
            headers={"User-Agent": "TextCorrector-updater"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        print(f"  ERROR: Could not reach GitHub API: {e}")
        return

    tag     = data.get("tag_name", "unknown")
    assets  = data.get("assets", [])
    kw      = _detect_asset_keyword()

    print(f"  Latest release : {tag}")
    print(f"  Platform hint  : {kw}")

    # Find the main binary asset
    main_asset = None
    for asset in assets:
        name = asset["name"].lower()
        # Must start with "llama-b" (not "cudart-") and match the keyword
        if name.startswith("llama-b") and kw in name and name.endswith((".zip", ".tar.gz")):
            main_asset = asset
            break

    if not main_asset:
        print(f"  No asset matched '{kw}'. Available assets:")
        for a in assets:
            print(f"    {a['name']}")
        print("  Please download manually from: https://github.com/ggerganov/llama.cpp/releases")
        return

    LLAMA_DIR.mkdir(exist_ok=True)
    downloads: list[tuple] = [(main_asset["browser_download_url"], main_asset["name"])]

    # For CUDA Windows builds, also grab the cudart package (CUDA runtime DLLs).
    # These are distributed separately since llama-bXXXX-bin-win-cuda-*.zip does NOT
    # bundle cudart64_*.dll / cublas64_*.dll — without them ggml-cuda.dll won't load.
    if sys.platform == "win32" and "cuda" in kw:
        # Extract the CUDA version from the matched asset name, e.g. "cuda-12.4"
        import re
        m = re.search(r"cuda-(\d+\.\d+)", main_asset["name"].lower())
        cuda_ver = m.group(1) if m else None
        cudart_asset = None
        for asset in assets:
            aname = asset["name"].lower()
            if aname.startswith("cudart-") and aname.endswith(".zip"):
                if cuda_ver and cuda_ver in aname:
                    cudart_asset = asset
                    break
        if cudart_asset:
            downloads.append((cudart_asset["browser_download_url"], cudart_asset["name"]))
            print(f"  Also downloading CUDA runtime package: {cudart_asset['name']}")
        else:
            print("  WARNING: cudart package not found — CUDA runtime DLLs will be missing.")

    for url, filename in downloads:
        tmp_path = ROOT / filename
        print(f"  Downloading {filename} …")
        urllib.request.urlretrieve(url, tmp_path, reporthook=_progress)
        print()
        print(f"  Extracting to {LLAMA_DIR}/ …")
        if filename.endswith(".zip"):
            _extract_zip_to(tmp_path, LLAMA_DIR)
        elif filename.endswith(".tar.gz"):
            with tarfile.open(tmp_path) as tf:
                for member in tf.getmembers():
                    mname = Path(member.name).name
                    if mname and (mname.startswith("llama-") or mname.endswith(".so") or
                                   mname.endswith(".dylib")):
                        f = tf.extractfile(member)
                        if f:
                            dest = LLAMA_DIR / mname
                            dest.write_bytes(f.read())
                            dest.chmod(0o755)
                            print(f"    Extracted: {mname}")
        tmp_path.unlink(missing_ok=True)

    print(f"  llama.cpp updated to {tag}.")


def _progress(block, block_size, total):
    downloaded = block * block_size
    pct = min(100, downloaded * 100 // total) if total > 0 else 0
    bar = "#" * (pct // 4)
    print(f"\r  [{bar:<25}] {pct:3d}%  {downloaded/1_048_576:.1f} MB", end="", flush=True)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="TextCorrector updater")
    p.add_argument("--llama", action="store_true", help="Also update llama-server binary")
    p.add_argument("--all",   action="store_true", help="Update everything")
    args = p.parse_args()

    update_python_deps()

    if args.llama or args.all:
        update_llama()

    banner("Update complete!")
    print("  Restart TextCorrector to use the new versions.\n")
