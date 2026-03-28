# TextCorrector v2

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Python: 3.12](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![Platform: Windows](https://img.shields.io/badge/Platform-Windows-lightgrey.svg)]()

## What is this?

TextCorrector v2 sits quietly in your system tray and lets you fix grammar and spelling in **any application** instantly — email, Word, browsers, chat apps. Everything is processed 100% locally; your text never leaves your computer.

**v2 Features:**
- 🚀 **T5 ONNX Model** for instant autocorrect (~100ms)
- 💬 **LLM Chat** for conversational text refinement
- 🔒 **100% Offline** - no API calls, no data leaves your computer
- 🎯 **Smart Detection** - knows when to correct vs. when to chat

---

## Quick Start

1. **Run `TextCorrector.exe`**
2. A UAC prompt may appear — click **Yes** (required for global hotkey)
3. Look for the icon in your **system tray** (near the clock)
4. Open **Settings** → **ONNX Model Directory** → Browse to `onnx_models/grammar_t5/`
5. **Save** and restart

---

## How to Use

### Autocorrect (T5 - Fast)
1. **Select** any text in any application
2. Press **Ctrl + Alt + C** (or your configured hotkey)
3. A window appears showing the corrected text with changes highlighted in green
4. Press **Enter** (or click **Accept & Paste**) to replace the original text

### Chat Refinement (LLM - Conversational)
1. After autocorrect, click the **Chat** button in the correction window
2. Type your request (e.g., "Make this more formal", "Shorten this", "Explain the changes")
3. The AI responds with suggestions
4. If it's a correction, click **Paste**; if it's an answer, just read it

---

## First-Time Setup

### Step 1: Configure ONNX Model

1. Right-click tray icon → **Settings**
2. Scroll to **ONNX Model Directory**
3. Click **Browse...**
4. Select the `onnx_models/grammar_t5/` folder
5. Click **Save**

### Step 2: Verify Loading

After restart, the tray tooltip should show:
- **"ONNX Ready - T5 Grammar"** = T5 model loaded for autocorrect
- **"LLM Ready"** = LLM loaded for chat (only loads when you use chat)

---

## Included Models

| Model | Purpose | Speed | Size |
|-------|---------|-------|------|
| **T5 Grammar (ONNX)** | Autocorrect | ~100ms | ~300 MB |
| **Qwen3.5-2B (GGUF)** | Chat refinement | ~2-5s | ~1.5 GB |

---

## System Requirements

| | Minimum | Recommended |
|---|---|---|
| **OS** | Windows 10 | Windows 11 |
| **RAM** | 8 GB | 16 GB |
| **GPU** | None (CPU mode) | NVIDIA with 4 GB+ VRAM |

> **No NVIDIA GPU?** No problem! T5 runs on CPU at full speed. LLM chat will be slower but still functional.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| Tray icon doesn't appear | Wait up to 60 seconds on first launch |
| Hotkey doesn't work | Run as Administrator (right-click exe → Run as admin) |
| App crashes immediately | Install [Visual C++ Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe) |
| Text not being replaced | Make sure text is **selected** before pressing hotkey |
| "ONNX not loading" | Verify `onnx_models/grammar_t5/encoder_model.onnx` exists |
| Chat always uses LLM | This is correct! Chat always uses LLM for conversation |

---

## Settings Reference

| Setting | Default | Description |
|---|---|---|
| **ONNX Model Directory** | (empty) | Path to T5 model folder (`onnx_models/grammar_t5/`) |
| **Hotkey** | Ctrl+Alt+C | Trigger autocorrect |
| **System Prompt** | (editable) | Instructions for LLM chat |
| **GPU Layers** | 99 | How many LLM layers on GPU (0 = CPU only) |

---

## Architecture

### How It Works

```
User selects text → presses hotkey
         │
         ▼
┌─────────────────────┐
│   T5 ONNX Model     │ ← Fast autocorrect (~100ms)
│   (always loaded)   │
└─────────────────────┘
         │
         ▼
   Correction shown
         │
    ┌────┴────┐
    │         │
    ▼         ▼
  Paste    Chat
           │
           ▼
    ┌──────────────┐
    │  LLM Model   │ ← Conversational refinement
    │  (loads on   │
    │   demand)    │
    └──────────────┘
```

### Why Two Models?

- **T5 ONNX**: Specialized for grammar correction, extremely fast, runs on any hardware
- **LLM (Qwen3.5-2B)**: General-purpose, understands context, handles complex requests

---

## Privacy & Security

- ✅ **100% Offline** - No internet connection required
- ✅ **No telemetry** - Nothing is sent anywhere
- ✅ **Local processing** - Your text never leaves your computer
- ✅ **Open source** - Code is auditable (GPL v3)

---

## Building from Source

```powershell
# In v2/ directory:
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File build.ps1

# Output: v2/TextCorrector_Release/
```

---

## License

This project is licensed under the **GNU General Public License v3.0**. See the [LICENSE](LICENSE) file for details.

---

## Credits

- **T5 Model**: HuggingFace Transformers
- **ONNX Runtime**: Microsoft
- **LLM**: Qwen3.5-2B by Alibaba
- **UI**: PyQt5

---

*All processing is 100% local. No internet connection required.*
