"""
build.py — TextCorrector release packager
==========================================
Produces a self-contained ZIP in dist/TextCorrector_<version>_<platform>.zip.

Supports Windows, macOS, Linux.  Must be run on the target platform
(PyInstaller bundles are not cross-platform).

Usage
-----
    python build.py                  # full release build
    python build.py --version 4.0.0  # override version tag
    python build.py --no-zip         # skip ZIP, just output the folder

Requirements
------------
    pip install pyinstaller
    All app Python dependencies must be installed in the active venv.

What it does
------------
1. Runs PyInstaller → single-folder dist/TextCorrector/
2. Copies the llama-server binary folder (resolved from config.json or auto-detected)
3. On Windows: copies CUDA 12 runtime DLLs alongside the server if found
4. Copies logo, LICENSE, README
5. Writes a clean release config.json (blank paths, sensible defaults)
6. Creates run.bat / run.sh launcher and download_model helper script
7. Zips the whole thing → dist/TextCorrector_<ver>_<platform>.zip

LOCKED ARCHITECTURE — DO NOT CHANGE WITHOUT USER APPROVAL
----------------------------------------------------------
- LLM backend: llama.cpp (llama-server binary) on port 8080 via HTTP
- Thinking mode disabled server-side via --reasoning off flag
- CUDA runtime DLLs must travel with the server binary on Windows
- ac_same_as_chat=True means one server handles both autocorrect and chat
- The app is single-file (text_corrector.py) — no Python package structure
"""

import sys, os, shutil, subprocess, zipfile, argparse, platform, json
from pathlib import Path
from datetime import datetime

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.resolve()
DIST = ROOT / "dist"
BUILD = ROOT / "build"

PLATFORM = {
    "win32": "Windows",
    "darwin": "macOS",
    "linux": "Linux",
}.get(sys.platform, sys.platform)

MAIN_SCRIPT = ROOT / "text_corrector.py"
ICON_ICO = ROOT / "logo.ico"
ICON_PNG = ROOT / "logo.png"
LICENSE_FILE = ROOT / "LICENSE"


# ── Version ───────────────────────────────────────────────────────────────────
def _get_version() -> str:
    import re
    try:
        text = MAIN_SCRIPT.read_text(encoding="utf-8")
        for line in text.splitlines()[:8]:
            m = re.search(r"v(\d+\.\d+(?:\.\d+)?)", line, re.I)
            if m:
                return m.group(1)
    except Exception:
        pass
    return datetime.now().strftime("%Y.%m.%d")


# ── Resolve llama-server directory ────────────────────────────────────────────
def _find_llama_dir() -> Path | None:
    """Find the llama-server binary folder.

    Priority:
    1. llama_server_path in config.json → parent directory
    2. Any folder in ROOT matching 'llama*' that contains llama-server[.exe]
    3. ROOT / llama_cpp  (legacy)
    """
    exe = "llama-server.exe" if PLATFORM == "Windows" else "llama-server"

    # 1. From config
    cfg_file = ROOT / "config.json"
    if cfg_file.exists():
        try:
            cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
            sp = cfg.get("llama_server_path", "")
            if sp:
                d = Path(sp).parent
                if d.exists() and (d / exe).exists():
                    return d
        except Exception:
            pass

    # 2. Auto-detect any sibling folder with the binary
    for candidate in sorted(ROOT.iterdir()):
        if candidate.is_dir() and "llama" in candidate.name.lower():
            if (candidate / exe).exists():
                return candidate

    # 3. Legacy location
    legacy = ROOT / "llama_cpp"
    if legacy.exists() and (legacy / exe).exists():
        return legacy

    return None


# ── CUDA runtime DLLs (Windows only) ─────────────────────────────────────────
CUDA_DLLS = ["cudart64_12.dll", "cublas64_12.dll", "cublasLt64_12.dll"]

def _find_cuda_dir() -> Path | None:
    """Find a directory containing CUDA 12 runtime DLLs."""
    if PLATFORM != "Windows":
        return None

    search = [
        # Common CUDA Toolkit install paths
        Path(os.path.expandvars(r"%ProgramFiles%\NVIDIA GPU Computing Toolkit\CUDA\v12.4\bin")),
        Path(os.path.expandvars(r"%ProgramFiles%\NVIDIA GPU Computing Toolkit\CUDA\v12.6\bin")),
        Path(os.path.expandvars(r"%ProgramFiles%\NVIDIA GPU Computing Toolkit\CUDA\v12.0\bin")),
        # Ollama bundles CUDA 12 runtime
        Path("E:/AI/AnythingLLM/resources/ollama/lib/ollama/cuda_v12"),
        Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\lib\ollama\cuda_v12")),
        Path(os.path.expandvars(r"%APPDATA%\Ollama\lib\ollama\cuda_v12")),
    ]

    # Also search sibling folders of the llama dir
    llama = _find_llama_dir()
    if llama:
        for d in sorted(llama.parent.iterdir()):
            if d.is_dir() and "cuda" in d.name.lower():
                search.append(d)

    for d in search:
        if d.exists() and all((d / dll).exists() for dll in CUDA_DLLS):
            return d

    return None


# ── Helpers ───────────────────────────────────────────────────────────────────
def run(cmd: list, **kw):
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    subprocess.run(cmd, check=True, **kw)


def banner(msg: str):
    print(f"\n{'─' * 60}")
    print(f"  {msg}")
    print(f"{'─' * 60}")


# ── PyInstaller spec ─────────────────────────────────────────────────────────
def _build_spec(icon_png: Path, icon_ico: Path) -> str:
    datas = []
    if icon_png.exists():
        datas.append(f"('{str(icon_png).replace(chr(92), '/')}', '.')")
    if icon_ico.exists():
        datas.append(f"('{str(icon_ico).replace(chr(92), '/')}', '.')")

    datas_str = ",\n        ".join(datas) if datas else ""

    icon_arg = f"icon='{str(icon_ico).replace(chr(92), '/')}'" if icon_ico.exists() else ""

    return f"""\
# -*- mode: python ; coding: utf-8 -*-
a = Analysis(
    ['{str(MAIN_SCRIPT).replace(chr(92), '/')}'],
    pathex=['{str(ROOT).replace(chr(92), '/')}'],
    binaries=[],
    datas=[
        {datas_str}
    ],
    hiddenimports=[
        'keyboard', 'pyperclip', 'requests',
        'PyQt6', 'PyQt6.QtWidgets', 'PyQt6.QtCore', 'PyQt6.QtGui',
        'difflib', 'json', 'threading', 'subprocess',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=['torch', 'onnxruntime', 'transformers', 'gector'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='TextCorrector',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    {icon_arg}
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=True, upx_exclude=[],
    name='TextCorrector',
)
"""


# ── Release config (blank model paths, sensible defaults) ────────────────────
# LOCKED: Keep these keys in sync with ConfigManager.DEFAULTS in text_corrector.py.
# Do not remove keys — the app reads all of them at startup and may error on missing ones.
RELEASE_CONFIG = {
    "llama_server_path": "",
    "model_path": "",
    "ac_model_path": "",
    "ac_same_as_chat": True,
    "server_host": "127.0.0.1",
    "server_port": 8080,
    "context_size": 4096,
    "gpu_layers": 99,
    "temperature": 0.1,
    "top_k": 40,
    "top_p": 0.95,
    "min_p": 0.05,
    "frequency_penalty": 0.0,
    "presence_penalty": 0.0,
    "repeat_penalty": 1.0,
    "keep_model_loaded": True,
    "idle_timeout_seconds": 300,
    "hotkey": "ctrl+shift+space",
    "system_prompt": "",
    "correction_mode": 1,
    "correction_strength": 4,
    "custom_templates": [],
    "recent_models": [],
    "lt_enabled": False,
    "lt_language": "en-US",
    "lt_disabled_rules": "",
}


# ── Launcher scripts ──────────────────────────────────────────────────────────
RUN_BAT = r"""@echo off
cd /d "%~dp0"
TextCorrector.exe
"""

RUN_SH = """#!/usr/bin/env bash
cd "$(dirname "$0")"
./TextCorrector
"""

DOWNLOAD_SH = """#!/usr/bin/env bash
# Download a recommended LLM model for TextCorrector (Qwen 2.5 3B Instruct, Q4_K_M, ~2 GB)
# After download, open Settings and point 'Model Path' to this file.
MODEL_URL="https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf"
DEST="qwen2.5-3b-instruct-q4_k_m.gguf"
echo "Downloading $DEST ..."
if command -v curl &>/dev/null; then
    curl -L --progress-bar -o "$DEST" "$MODEL_URL"
elif command -v wget &>/dev/null; then
    wget -O "$DEST" "$MODEL_URL"
else
    echo "Error: neither curl nor wget found. Download manually from:"
    echo "$MODEL_URL"
    exit 1
fi
echo "Done. Open Settings in TextCorrector and set Model Path to: $(pwd)/$DEST"
"""

DOWNLOAD_BAT = r"""@echo off
set MODEL_URL=https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf
set DEST=qwen2.5-3b-instruct-q4_k_m.gguf
echo Downloading %DEST% ...
curl -L --progress-bar -o "%DEST%" "%MODEL_URL%"
if errorlevel 1 (
    echo Download failed. Install curl or download manually from:
    echo %MODEL_URL%
) else (
    echo Done. Open Settings in TextCorrector and set Model Path to: %CD%\%DEST%
)
pause
"""

SETUP_NOTES_WIN = """TextCorrector — Windows Setup
==============================

REQUIREMENTS
------------
1. NVIDIA GPU (recommended) — CUDA 12 runtime must be installed
   OR copy cudart64_12.dll / cublas64_12.dll / cublasLt64_12.dll
   next to llama-server.exe (they ship with Ollama if you have it)
2. A GGUF model file (~1-4 GB) — run download_model.bat to get one
3. A llama-server.exe binary — get the CUDA build from:
   https://github.com/ggml-org/llama.cpp/releases

FIRST RUN
---------
1. Place llama-server.exe (and its DLLs) in a folder next to TextCorrector.exe
2. Run download_model.bat to get a recommended model
3. Launch TextCorrector.exe
4. Open Settings → set Server Path and Model Path
5. Press Ctrl+Shift+Space anywhere to correct selected text
"""

SETUP_NOTES_UNIX = """TextCorrector — Setup
======================

REQUIREMENTS
------------
1. A GGUF model file (~1-4 GB) — run ./download_model.sh to get one
2. A llama-server binary — build from source or download from:
   https://github.com/ggml-org/llama.cpp/releases

FIRST RUN
---------
1. Place llama-server next to TextCorrector (or anywhere, then set path in Settings)
2. Run ./download_model.sh to get a recommended model
3. ./run.sh
4. Open Settings → set Server Path and Model Path
5. Press Ctrl+Shift+Space anywhere to correct selected text
"""


# ── Main build ────────────────────────────────────────────────────────────────
def build(version: str, make_zip: bool):
    release_name = f"TextCorrector_{version}_{PLATFORM}"
    out_dir = DIST / release_name
    spec_path = BUILD / "TextCorrector.spec"

    banner(f"TextCorrector build  v{version}  [{PLATFORM}]")

    # ── Resolve paths ─────────────────────────────────────────────────────
    llama_dir = _find_llama_dir()
    if llama_dir:
        print(f"  llama-server dir : {llama_dir}")
    else:
        print("  WARNING: llama-server not found — user must supply it manually")

    cuda_dir = _find_cuda_dir()
    if cuda_dir:
        print(f"  CUDA runtime DLLs: {cuda_dir}")
    elif PLATFORM == "Windows":
        print("  WARNING: CUDA 12 runtime DLLs not found — GPU will fall back to CPU")

    # ── Clean previous build ──────────────────────────────────────────────
    if out_dir.exists():
        print(f"  Removing old {out_dir.name}…")
        shutil.rmtree(out_dir)
    DIST.mkdir(parents=True, exist_ok=True)
    BUILD.mkdir(parents=True, exist_ok=True)

    # ── 1. PyInstaller ────────────────────────────────────────────────────
    banner("Step 1 / 4 — PyInstaller")
    spec_path.write_text(_build_spec(ICON_PNG, ICON_ICO), encoding="utf-8")
    run([
        sys.executable, "-m", "PyInstaller",
        "--distpath", str(DIST),
        "--workpath", str(BUILD),
        "--noconfirm",
        str(spec_path),
    ])

    pyinstaller_out = DIST / "TextCorrector"
    if not pyinstaller_out.exists():
        print("ERROR: PyInstaller output not found.")
        sys.exit(1)
    pyinstaller_out.rename(out_dir)

    # ── 2. Copy extras ────────────────────────────────────────────────────
    banner("Step 2 / 4 — Copy extras")

    # llama-server binaries
    if llama_dir:
        dst_llama = out_dir / llama_dir.name
        shutil.copytree(llama_dir, dst_llama, dirs_exist_ok=True)
        n = sum(1 for _ in dst_llama.rglob("*"))
        print(f"  Copied {llama_dir.name}/ ({n} files)")

        # On Windows: also copy CUDA runtime DLLs into the server folder
        if cuda_dir:
            for dll in CUDA_DLLS:
                src = cuda_dir / dll
                if src.exists():
                    shutil.copy2(src, dst_llama / dll)
                    print(f"  Copied CUDA DLL: {dll}")
    else:
        (out_dir / "llama_cpp").mkdir()
        print("  Created empty llama_cpp/ placeholder")

    # Release config (blank model paths)
    (out_dir / "config.json").write_text(json.dumps(RELEASE_CONFIG, indent=2))
    print("  Written config.json (blank model paths)")

    # LICENSE
    if LICENSE_FILE.exists():
        shutil.copy(LICENSE_FILE, out_dir / "LICENSE")

    # README
    readme = ROOT / "README.md"
    if readme.exists():
        shutil.copy(readme, out_dir / "README.md")

    # ── 3. Launcher scripts ───────────────────────────────────────────────
    banner("Step 3 / 4 — Launcher scripts")
    if PLATFORM == "Windows":
        (out_dir / "run.bat").write_text(RUN_BAT)
        (out_dir / "download_model.bat").write_text(DOWNLOAD_BAT)
        print("  Created run.bat, download_model.bat")
    else:
        run_sh = out_dir / "run.sh"
        dl_sh = out_dir / "download_model.sh"
        run_sh.write_text(RUN_SH)
        run_sh.chmod(0o755)
        dl_sh.write_text(DOWNLOAD_SH)
        dl_sh.chmod(0o755)
        print("  Created run.sh, download_model.sh")

    # ── 4. ZIP ────────────────────────────────────────────────────────────
    if make_zip:
        banner("Step 4 / 4 — Packaging ZIP")
        zip_path = DIST / f"{release_name}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for f in sorted(out_dir.rglob("*")):
                if f.is_file():
                    zf.write(f, f.relative_to(DIST))
        size_mb = zip_path.stat().st_size / 1_048_576
        print(f"  Created: {zip_path.name}  ({size_mb:.1f} MB)")
    else:
        banner("Step 4 / 4 — Skipped ZIP (--no-zip)")
        print(f"  Output folder: {out_dir}")

    banner(f"Build complete!  →  dist/{release_name}")

    # Print post-build notes
    if PLATFORM == "Windows" and not cuda_dir:
        print()
        print("  NOTE: CUDA runtime DLLs not bundled. Users without CUDA Toolkit 12")
        print("  installed will run on CPU only. To fix: install CUDA 12 runtime or")
        print("  copy cudart64_12.dll, cublas64_12.dll, cublasLt64_12.dll next to")
        print("  llama-server.exe before building.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build TextCorrector release")
    parser.add_argument("--version", default=_get_version(), help="Version tag")
    parser.add_argument("--no-zip", action="store_true", help="Skip ZIP creation")
    args = parser.parse_args()
    build(args.version, not args.no_zip)
