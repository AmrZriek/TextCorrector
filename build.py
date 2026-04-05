"""
build.py — TextCorrector release packager
==========================================
Produces a self-contained ZIP in dist/TextCorrector_<version>_<platform>.zip.

Supports Windows, macOS, Linux.

Usage
-----
    python build.py                  # full release build
    python build.py --version 3.1.0  # override version tag
    python build.py --no-zip         # skip ZIP (just copy files)

Requirements
------------
    pip install pyinstaller
    (All app dependencies must already be installed in the active Python env)

What it does
------------
1. Runs PyInstaller to create a single-folder dist/TextCorrector/
2. Copies llama_cpp/ binaries into the dist folder
3. Copies logo.ico, config.json (blank paths), LICENSE, README.md
4. Creates run.bat (Windows) / run.sh (macOS/Linux) launcher
5. Creates a download_model script pointing to Hugging Face
6. Zips the whole thing → dist/TextCorrector_<ver>_<platform>.zip
"""

import sys, os, shutil, subprocess, zipfile, argparse, platform
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
LLAMA_DIR = ROOT / "llama_cpp"
LICENSE_FILE = ROOT / "LICENSE"


# ── default version (read from script header) ────────────────────────────────
def _get_version() -> str:
    import re
    try:
        text = MAIN_SCRIPT.read_text(encoding="utf-8")
        for line in text.splitlines()[:8]:
            if re.search(r"v\d", line, re.I) or "version" in line.lower():
                m = re.search(r"v(\d+\.\d+(?:\.\d+)?)", line, re.I)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return datetime.now().strftime("%Y.%m.%d")


# ── Helpers ───────────────────────────────────────────────────────────────────
def run(cmd: list, **kw):
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    subprocess.run(cmd, check=True, **kw)


def banner(msg: str):
    print(f"\n{'─' * 60}")
    print(f"  {msg}")
    print(f"{'─' * 60}")


# ── PyInstaller spec ─────────────────────────────────────────────────────────
SPEC_TEMPLATE = """\
# -*- mode: python ; coding: utf-8 -*-
a = Analysis(
    ['{main}'],
    pathex=['{root}'],
    binaries=[],
    datas=[
        ('{icon_png}', '.'),
        ('{icon_ico}', '.'),
    ],
    hiddenimports=[
        'keyboard', 'pyperclip', 'requests',
        'PyQt6', 'PyQt6.QtWidgets', 'PyQt6.QtCore', 'PyQt6.QtGui',
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
    icon='{icon_ico}',
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=True, upx_exclude=[],
    name='TextCorrector',
)
"""


def write_spec(out_path: Path):
    content = SPEC_TEMPLATE.format(
        main=str(MAIN_SCRIPT).replace("\\", "/"),
        root=str(ROOT).replace("\\", "/"),
        icon_png=str(ICON_PNG).replace("\\", "/") if ICON_PNG.exists() else "",
        icon_ico=str(ICON_ICO).replace("\\", "/") if ICON_ICO.exists() else "",
    )
    out_path.write_text(content, encoding="utf-8")


# ── Default config (blank model paths for release) ───────────────────────────
RELEASE_CONFIG = {
    "llama_server_path": "",
    "model_path": "",
    "server_host": "127.0.0.1",
    "server_port": 8080,
    "context_size": 4096,
    "gpu_layers": 99,
    "temperature": 0.1,
    "top_k": 40,
    "top_p": 0.95,
    "min_p": 0.05,
    "keep_model_loaded": True,
    "idle_timeout_seconds": 300,
    "recent_models": [],
    "ac_model_path": "",
    "ac_same_as_chat": True,
    "hotkey": "ctrl+shift+space",
    "system_prompt": "",
    "correction_strength": 2,
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
# Download a recommended LLM model (Qwen 2.5 3B Instruct, Q4_K_M, ~2 GB)
MODEL_URL="https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf"
DEST="qwen2.5-3b-instruct-q4_k_m.gguf"
echo "Downloading $DEST ..."
if command -v curl &>/dev/null; then
    curl -L -o "$DEST" "$MODEL_URL"
elif command -v wget &>/dev/null; then
    wget -O "$DEST" "$MODEL_URL"
else
    echo "Error: neither curl nor wget found."
    exit 1
fi
echo "Done. Open Settings in TextCorrector and point the model path to: $DEST"
"""

DOWNLOAD_BAT = r"""@echo off
set MODEL_URL=https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf
set DEST=qwen2.5-3b-instruct-q4_k_m.gguf
echo Downloading %DEST% ...
curl -L -o "%DEST%" "%MODEL_URL%"
echo Done. Open Settings in TextCorrector and point the model path to: %DEST%
pause
"""


# ── Main build ────────────────────────────────────────────────────────────────
def build(version: str, make_zip: bool):
    release_name = f"TextCorrector_{version}_{PLATFORM}"
    out_dir = DIST / release_name
    spec_path = BUILD / "TextCorrector.spec"

    banner(f"TextCorrector build  v{version}  [{PLATFORM}]")

    # Clean previous build
    if out_dir.exists():
        print(f"Removing old {out_dir.name}…")
        shutil.rmtree(out_dir)
    DIST.mkdir(parents=True, exist_ok=True)
    BUILD.mkdir(parents=True, exist_ok=True)

    # ── 1. PyInstaller ────────────────────────────────────────────────────
    banner("Step 1 / 4 — PyInstaller")
    write_spec(spec_path)
    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--distpath",
            str(DIST),
            "--workpath",
            str(BUILD),
            "--noconfirm",
            str(spec_path),
        ]
    )

    pyinstaller_out = DIST / "TextCorrector"
    if not pyinstaller_out.exists():
        print("ERROR: PyInstaller output not found.")
        sys.exit(1)
    pyinstaller_out.rename(out_dir)

    # ── 2. Copy extras ────────────────────────────────────────────────────
    banner("Step 2 / 4 — Copy extras")

    # llama_cpp binaries
    if LLAMA_DIR.exists():
        dst_llama = out_dir / "llama_cpp"
        shutil.copytree(LLAMA_DIR, dst_llama, dirs_exist_ok=True)
        print(f"  Copied llama_cpp/ ({sum(1 for _ in dst_llama.rglob('*'))} files)")
    else:
        print("  WARNING: llama_cpp/ not found — user must supply binaries manually")

    # Release config
    import json

    (out_dir / "config.json").write_text(json.dumps(RELEASE_CONFIG, indent=2))

    # LICENSE
    if LICENSE_FILE.exists():
        shutil.copy(LICENSE_FILE, out_dir / "LICENSE")

    # README (use built file if present)
    readme = ROOT / "README.md"
    if readme.exists():
        shutil.copy(readme, out_dir / "README.md")

    # ── 3. Launcher scripts ───────────────────────────────────────────────
    banner("Step 3 / 4 — Launcher scripts")
    if sys.platform == "win32":
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
        with zipfile.ZipFile(
            zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6
        ) as zf:
            for f in sorted(out_dir.rglob("*")):
                if f.is_file():
                    zf.write(f, f.relative_to(DIST))
        size_mb = zip_path.stat().st_size / 1_048_576
        print(f"  Created: {zip_path.name}  ({size_mb:.1f} MB)")
    else:
        banner("Step 4 / 4 — Skipped ZIP (--no-zip)")
        print(f"  Output folder: {out_dir}")

    banner(f"Build complete!  →  dist/{release_name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build TextCorrector release")
    parser.add_argument("--version", default=_get_version(), help="Version tag")
    parser.add_argument("--no-zip", action="store_true", help="Skip ZIP creation")
    args = parser.parse_args()
    build(args.version, not args.no_zip)
