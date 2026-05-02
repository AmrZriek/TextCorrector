"""
update.py — TextCorrector app and dependency updater
=============================================
Updates all Python dependencies and optionally downloads the latest
TextCorrector release from GitHub.

Usage
-----
    python update.py             # update Python deps only (for dev)
    python update.py --app       # update TextCorrector app to latest release
    python update.py --all       # update everything

What it does
------------
1. Upgrades pip itself
2. Installs / upgrades all packages from requirements.txt
3. (Optional) Downloads the latest TextCorrector release zip for your OS
   and extracts it over the current installation (preserving user config/models).
"""

import sys, os, subprocess, platform, urllib.request, zipfile, tarfile, shutil, json, re
from pathlib import Path

ROOT      = Path(__file__).parent.resolve()
VENV_PY   = ROOT / "venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
REQ_FILE  = ROOT / "requirements.txt"
MAIN_SCRIPT = ROOT / "text_corrector.py"

GITHUB_API = "https://api.github.com/repos/AmrZriek/TextCorrector/releases/latest"

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


def get_local_version() -> str:
    try:
        text = MAIN_SCRIPT.read_text(encoding="utf-8")
        m = re.search(r'APP_VERSION\s*=\s*[\'"]([0-9\.]+)[\'"]', text)
        if m:
            return m.group(1)
    except Exception:
        pass
    return "0.0.0"


def _parse_version(v_str):
    v_str = re.sub(r'[^0-9\.]', '', v_str)
    parts = []
    for p in v_str.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


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


# ── App updater ─────────────────────────────────────────────────────────────
def update_app():
    banner("Updating TextCorrector app")
    print(f"  Fetching latest release info from GitHub…")

    try:
        req = urllib.request.Request(GITHUB_API,
            headers={"User-Agent": "TextCorrector-updater"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        print(f"  ERROR: Could not reach GitHub API: {e}")
        return

    tag = data.get("tag_name", "unknown")
    assets = data.get("assets", [])

    remote_ver = tag.lstrip("vV")
    local_ver = get_local_version()

    print(f"  Latest release : {tag}")
    print(f"  Local version  : {local_ver}")

    if _parse_version(remote_ver) <= _parse_version(local_ver):
        print("  You already have the latest version.")
        return

    # Find the main binary asset for the current OS
    os_kw = "windows" if sys.platform == "win32" else ("macos" if sys.platform == "darwin" else "linux")
    main_asset = None
    for asset in assets:
        name = asset["name"].lower()
        if name.endswith(".zip") and os_kw in name:
            main_asset = asset
            break
            
    if not main_asset and assets:
        for asset in assets:
            if asset["name"].lower().endswith(".zip"):
                main_asset = asset
                break

    if not main_asset:
        print(f"  No suitable ZIP asset found in release {tag}.")
        return

    url = main_asset["browser_download_url"]
    filename = main_asset["name"]
    tmp_path = ROOT / filename

    print(f"  Downloading {filename} …")
    try:
        urllib.request.urlretrieve(url, tmp_path, reporthook=_progress)
        print()
    except Exception as e:
        print(f"\n  ERROR downloading update: {e}")
        return

    staging_dir = ROOT / "_update_staging"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir()
    
    print(f"  Extracting …")
    with zipfile.ZipFile(tmp_path, 'r') as zip_ref:
        zip_ref.extractall(staging_dir)
        
    tmp_path.unlink()
    
    app_dir = None
    for child in staging_dir.iterdir():
        if child.is_dir() and (child / "TextCorrector.exe").exists():
            app_dir = child
            break
            
    if not app_dir:
        if (staging_dir / "TextCorrector.exe").exists():
            app_dir = staging_dir
        else:
            print("  ERROR: TextCorrector.exe not found in downloaded ZIP")
            shutil.rmtree(staging_dir)
            return

    print("  Applying update…")
    
    exclude_prefixes = ("llama_cpp", "llama-")
    exclude_suffixes = (".gguf", ".onnx")
    exclude_exact = ("config.json",)
    
    for src_path in app_dir.rglob("*"):
        if not src_path.is_file():
            continue
            
        rel_path = src_path.relative_to(app_dir)
        dest_path = ROOT / rel_path
        
        # Check excludes
        skip = False
        parts = rel_path.parts
        if parts[0].startswith(exclude_prefixes):
            skip = True
        elif rel_path.name.endswith(exclude_suffixes):
            skip = True
        elif rel_path.name in exclude_exact:
            skip = True
            
        if skip:
            continue
            
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src_path, dest_path)
            # print(f"    Updated: {rel_path}")
        except PermissionError:
            print(f"  ERROR: Permission denied replacing {rel_path}.")
            print("         Please ensure TextCorrector is completely closed before updating.")
            shutil.rmtree(staging_dir)
            return
            
    shutil.rmtree(staging_dir)
    print(f"  TextCorrector updated to {tag}.")


def _progress(block, block_size, total):
    downloaded = block * block_size
    pct = min(100, downloaded * 100 // total) if total > 0 else 0
    bar = "#" * (pct // 4)
    print(f"\r  [{bar:<25}] {pct:3d}%  {downloaded/1_048_576:.1f} MB", end="", flush=True)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="TextCorrector updater")
    p.add_argument("--app", action="store_true", help="Update TextCorrector app")
    p.add_argument("--all", action="store_true", help="Update everything (app + python deps)")
    args = p.parse_args()

    # Default to updating python deps if no args given (backward compat)
    if not args.app and not args.all:
        update_python_deps()
    
    if args.all:
        update_python_deps()

    if args.app or args.all:
        update_app()

    banner("Update complete!")
    print("  Restart TextCorrector to use the new versions.\n")
