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
        return "win-cuda" if _has_nvidia() else "win-noavx" if not _has_avx2() else "win-avx2"
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

    # Find a matching asset
    match = None
    for asset in assets:
        name = asset["name"].lower()
        if kw in name and name.endswith((".zip", ".tar.gz")):
            match = asset
            break

    if not match:
        print(f"  No asset matched '{kw}'. Available assets:")
        for a in assets:
            print(f"    {a['name']}")
        print("  Please download manually from: https://github.com/ggerganov/llama.cpp/releases")
        return

    url       = match["browser_download_url"]
    filename  = match["name"]
    tmp_path  = ROOT / filename

    print(f"  Downloading {filename} …")
    urllib.request.urlretrieve(url, tmp_path, reporthook=_progress)
    print()

    # Extract
    LLAMA_DIR.mkdir(exist_ok=True)
    print(f"  Extracting to {LLAMA_DIR}/ …")

    if filename.endswith(".zip"):
        with zipfile.ZipFile(tmp_path) as zf:
            for member in zf.namelist():
                mname = Path(member).name
                if mname and (mname.startswith("llama-") or mname.endswith(".dll") or
                               mname.endswith(".so") or mname.endswith(".dylib")):
                    data_bytes = zf.read(member)
                    dest = LLAMA_DIR / mname
                    dest.write_bytes(data_bytes)
                    if sys.platform != "win32":
                        dest.chmod(0o755)
                    print(f"    Extracted: {mname}")
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
