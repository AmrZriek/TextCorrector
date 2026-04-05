# TextCorrector v3.0

**Instant AI-powered text correction — select text anywhere, press a hotkey, done.**

TextCorrector lives in the system tray. Select text in any app, press the hotkey, and a dark popup appears with corrections highlighted in blue. Accept to paste back. Or chat with the LLM to rewrite, shorten, or change the tone.

---

## How it works

1. Select text in any application.
2. Press the hotkey (default `Ctrl + Shift + Space`).
3. The correction popup appears instantly with grammar/spelling fixes highlighted.
4. Press **Accept & Paste** (`Ctrl+Enter`) — corrected text is pasted back. Done.
5. Optionally, type in the **Ask AI** box to make bigger changes with the LLM.

---

## Architecture

| Layer | Technology | Role |
|---|---|---|
| **Autocorrect** | LanguageTool (local Java server) | Primary — always instant, 10–50 ms |
| **AI Chat** | llama.cpp (any GGUF model) | Secondary — user-initiated only |
| GUI | PyQt6, dark navy frameless | — |
| Hotkey | `keyboard` library (global hook) | — |

**Design philosophy — same as Samsung keyboard AI:**
- LanguageTool is the autocorrect engine. It handles spelling and grammar instantly, with no GPU required.
- The LLM is **never** run automatically. It is only activated when the user explicitly types in the Ask AI chat box.
- Keeping these roles separate ensures the hotkey always responds fast, regardless of whether an LLM model is loaded.

---

## Requirements

- **Python 3.11+**
- **Java 8+** (for LanguageTool — usually pre-installed; download from https://adoptium.net if not)
- A GGUF model file for LLM features (optional, but recommended for best accuracy)
- The `llama_cpp/` folder with the `llama-server` binary

---

## Installation

```bash
# 1. Clone or download
git clone https://github.com/your-repo/TextCorrector.git
cd TextCorrector

# 2. Create a venv and install dependencies
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux
pip install -r requirements.txt

# 3. Run
run.bat          # Windows (auto-elevates, uses venv)
# python text_corrector.py   # macOS/Linux
```

On first run, LanguageTool downloads its server JAR (~200 MB) to `~/.cache/language_tool_python/`. This is a one-time download.

---

## Setting up the LLM

For the best correction quality and the AI chat feature:

1. Download a GGUF model — recommended: **Qwen 2.5 3B Instruct Q4_K_M** (~2 GB).

2. Download the `llama-server` binary for your OS from:
   https://github.com/ggerganov/llama.cpp/releases
   Place the files in `llama_cpp/`.

3. Open TextCorrector → **Settings** (tray icon → Settings or ⚙ in the popup):
   - **Server binary**: point to `llama_cpp/llama-server.exe`
   - **Model file**: point to your `.gguf` file

4. The model is loaded on demand (first hotkey press or first chat message).
   Enable **Keep model loaded** in Settings for instant response every time.

---

## Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+Shift+Space` | Trigger correction (configurable) |
| `Ctrl+Enter` | Accept & Paste corrected text |
| `Escape` | Close popup |
| `Enter` (in chat box) | Send chat message |

---

## Updating dependencies

```bash
python update.py          # Update Python packages
python update.py --all    # Also update llama-server binary
```

---

## Building a release

```bash
pip install pyinstaller
python build.py
```

Produces `dist/TextCorrector_<version>_<platform>.zip` — self-contained, no Python required on target machine.

---

## Configuration reference

Settings are in `config.json` (same folder as the script). All values are editable via the Settings dialog.

| Key | Default | Description |
|---|---|---|
| `lt_enabled` | `true` | Enable LanguageTool grammar correction |
| `lt_language` | `en-US` | LanguageTool language code (e.g. `de-DE`, `fr-FR`) |
| `lt_disabled_rules` | `""` | Comma-separated rule IDs to suppress |
| `hotkey` | `ctrl+shift+space` | Global trigger hotkey |
| `model_path` | `""` | Path to GGUF model file |
| `server_port` | `8080` | llama-server HTTP port |
| `context_size` | `4096` | LLM context window (tokens) |
| `gpu_layers` | `99` | GPU offload layers (0 = CPU only) |
| `keep_model_loaded` | `true` | Keep LLM in memory between uses |
| `temperature` | `0.1` | LLM temperature |
| `top_k` | `40` | Top-K sampling |
| `top_p` | `0.95` | Top-P sampling |
| `min_p` | `0.05` | Min-P sampling |

---

## Troubleshooting

**Hotkey not working:**
- Windows: run `run.bat` as administrator.
- macOS: grant Accessibility in System Settings → Privacy → Accessibility.
- Linux: may require root or adding user to the `input` group.

**LLM chat shows 503 error:**
- The model server isn't running yet. Click the chat send button once more — the app will load the model and retry automatically.
- Make sure the server binary and model file paths are set correctly in Settings.

**LanguageTool makes a wrong correction (e.g. `exactl` → `exact` instead of `exactly`):**
- LT uses edit-distance spelling and may prefer shorter candidates. This is a known LanguageTool limitation.
- Use the **Ask AI** chat box to fix specific words after the initial correction (e.g. type "the word 'exact' should be 'exactly'").

**App crashes / disappears:**
- Check `app_debug.log` in the TextCorrector folder — all exceptions are now logged there.

---

## License

GPL v3 — see [LICENSE](LICENSE).
