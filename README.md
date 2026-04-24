# TextCorrector v3.1

**Instant AI-powered text correction — select text anywhere, press a hotkey, done.**

TextCorrector lives in the system tray. Select text in any app, press the hotkey, and a dark popup appears with corrections highlighted in blue. Accept to paste back, or chat with the LLM to rewrite, shorten, or change the tone.

---

## How it works

1. Select text in any application.
2. Press the hotkey (default `Ctrl + Shift + Space`).
3. The correction popup appears with grammar/spelling fixes highlighted in blue.
4. Press **Accept & Paste** (`Ctrl+Enter`) — corrected text is pasted back. Done.
5. Optionally, type in the **Ask AI** box to make bigger changes (rewrite, shorten, change tone).

---

## Architecture

| Layer | Technology | Role |
|---|---|---|
| **Autocorrect** | llama.cpp (GGUF model, local) | **Three-Phase Pipeline**: 1. Dict pre-pass, 2. Parallel sentence rewrite, 3. Hallucination guard. |
| **AI Chat** | Same llama.cpp server, reused | No second model load when `ac_same_as_chat = true`. Supports streaming. |
| GUI | PyQt6, dark navy frameless | Premium dark UI with diff highlighting. |
| Hotkey | `keyboard` library (global hook) | Instant capture with re-entrancy protection. |

**Design philosophy — Samsung AI keyboard style:**
- **Patch Pipeline**: Unlike simple "find-and-replace", TextCorrector uses a parallel sentence-rewrite pipeline. It splits text into sentence-sized units, rewrites them in parallel using multiple LLM slots (`--parallel 4`), and validates each rewrite with a hallucination ratio guard.
- **Deterministic Pre-pass**: A built-in dictionary handles ~150 common typos instantly with zero LLM latency.
- **Thinking Mode Suppression**: Uses `--reasoning-budget 0` to ensure models like Gemma 4 or Qwen 2.5/3.5 don't waste time on internal chain-of-thought during correction.
- **GPU Acceleration**: Native CUDA 12 support on Windows via DLL injection — corrections are typically sub-second for most paragraphs.

---

## Requirements

- **Python 3.11+** (only needed when running from source; prebuilt releases include Python)
- **NVIDIA GPU** recommended (4 GB+ VRAM) — CPU fallback works but is slow
- A GGUF model file (~1–4 GB) — see [Setting up the model](#setting-up-the-model) below
- `llama-server.exe` binary (CUDA 12 build) from the [llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases)
- On Windows: CUDA 12 runtime DLLs alongside `llama-server.exe`
  (`cudart64_12.dll`, `cublas64_12.dll`, `cublasLt64_12.dll`)

No Java, no LanguageTool, no internet connection required.

---

## Installation

### Windows — Prebuilt release

1. Download `TextCorrector_<version>_Windows.zip` from [Releases](https://github.com/AmrZriek/TextCorrector/releases).
2. Extract anywhere. Run `download_model.bat` to get the recommended model (~1.8 GB download).
3. Double-click `run.bat` — the app appears in the system tray.
4. Open Settings → set **Server binary** and **Model file** if not auto-detected.

### macOS / Linux — Run from source

Prebuilt releases are Windows-only. macOS and Linux users run directly from source (no build step needed — Python works fine) Download the respective llama.cpp backend from https://github.com/ggml-org/llama.cpp:

```bash
# 1. Clone
git clone https://github.com/AmrZriek/TextCorrector.git
cd TextCorrector

# 2. Create a venv and install dependencies
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Run
python text_corrector.py
```

**macOS extra step:** Grant Accessibility permissions when prompted — System Settings → Privacy & Security → Accessibility.

**Linux extra step:** If the hotkey doesn't register, add your user to the `input` group and re-login:
```bash
sudo usermod -aG input $USER
```

---

## Setting up the model

1. **Download a GGUF model** — recommended: **Gemma 4 E2B Q4_K_XL** (~1.8 GB).
   Run `download_model.bat` (Windows) or `./download_model.sh` (macOS/Linux) for an automated download.

2. **Download `llama-server`** from the [llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases).
   - **Windows**: grab the `cuda-12.x-x64` build. Copy CUDA 12 runtime DLLs next to `llama-server.exe` if not already present (they ship with Ollama at `%LOCALAPPDATA%\Programs\Ollama\lib\ollama\cuda_v12\`).
   - **macOS**: grab the `macos-arm64` build (Apple Silicon) or `macos-x86_64` (Intel). Mark executable: `chmod +x llama-server`.
   - **Linux**: grab the `ubuntu-x64` build. Mark executable: `chmod +x llama-server`.
   - Place the extracted folder anywhere (e.g. next to `text_corrector.py`).

3. Open TextCorrector → **Settings** (tray icon or ⚙ in the popup):
   - **Server binary**: path to `llama-server` / `llama-server.exe`
   - **Model file**: path to your `.gguf` file

4. The model loads on first hotkey press. Enable **Keep model loaded** in Settings for instant response every time.

---

## Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+Shift+Space` | Trigger correction (configurable in Settings) |
| `Ctrl+Enter` | Accept & paste corrected text |
| `Escape` | Close popup |
| `Enter` (in chat box) | Send chat message |

---

## Building a release

```bash
pip install nuitka
python build.py
```

Produces `dist/TextCorrector_<version>_<platform>.zip` — self-contained, no Python required.
Uses Nuitka (compiles Python → C → native binary) instead of PyInstaller to avoid Windows Defender false-positive trojan warnings.
On Windows, `build.py` automatically detects and bundles CUDA 12 runtime DLLs if found.

---

## Configuration reference

All settings are editable via the Settings dialog. `config.json` is created in the app folder on first run.

| Key | Default | Description |
|---|---|---|
| `llama_server_path` | `""` | Path to `llama-server[.exe]` binary |
| `model_path` | `""` | Path to GGUF model file (chat/rewrite) |
| `ac_model_path` | `""` | Path to GGUF model file (autocorrect) |
| `ac_same_as_chat` | `true` | Reuse the chat model for autocorrect (one server) |
| `correction_method` | `"patch"` | `"patch"` (parallel rewrite) or `"stream"` (tokens stream into pane) |
| `streaming_strength` | `"smart_fix"` | `"conservative"` (typos only) or `"smart_fix"` (grammar/style) |
| `gpu_layers` | `99` | GPU offload layers (0 = CPU only) |
| `context_size` | `12800` | LLM context window (tokens) |
| `keep_model_loaded` | `true` | Keep LLM in memory between uses |
| `hotkey` | `ctrl+shift+space` | Global trigger hotkey |
| `temperature` | `0.1` | LLM temperature |
| `server_port` | `8080` | llama-server HTTP port |

---

## Troubleshooting

**Hotkey not working:**
- Windows: try running as administrator if global hotkeys are blocked.
- macOS: grant Accessibility in System Settings → Privacy → Accessibility.
- Linux: may require adding user to the `input` group.

**GPU not being used / slow corrections:**
- Check `app_debug.log` for `[AC] GPU detection: has_nvidia()=True` and `Using gpu_layers=99`.
- If CUDA DLLs are missing, the server silently falls back to CPU. Copy `cudart64_12.dll`, `cublas64_12.dll`, and `cublasLt64_12.dll` next to `llama-server.exe`.

**Corrections return unchanged text / empty result:**
- Check `app_debug.log` for `reasoning_content present` — this means the model entered thinking mode. The server flag `--reasoning-budget 0` should prevent this.
- If drift is too high, the **Hallucination Guard** will reject the LLM output and keep the original text to prevent corruption. Try a larger or higher-quality model.

**Chat shows "loading model" every time:**
- Enable `ac_same_as_chat = true` in Settings so the chat reuses the already-loaded autocorrect server.

**App crashes / disappears:**
- Check `app_debug.log` in the TextCorrector folder — all errors are logged there.
- Check `server_log.txt` for llama-server startup errors (CUDA failures, model not found, etc.).

---

## License

GPL v3 — see [LICENSE](LICENSE).
