# TextCorrector — Quick Start Guide

## What is this?

TextCorrector sits quietly in your system tray and lets you fix grammar and spelling in **any application** instantly — email, Word, browsers, chat apps. Everything is processed 100% locally; your text never leaves your computer.

---

## How to Run

1. **Double-click `TextCorrector.exe`**
2. A UAC (User Account Control) prompt may appear — click **Yes** *(required for the global hotkey to work in all apps)*
3. A small icon will appear in your **system tray** (bottom-right, near the clock)

That's it. The AI model loads automatically in the background.

---

## How to Use

1. **Select** any text in any application
2. Press **Ctrl + Shift + Space**
3. A window appears showing the corrected text with changes highlighted in green
4. Press **Enter** (or click **Accept & Paste**) to replace your original text

---

## First-Time Startup

The AI model loads into memory the first time you use it. This takes **10–60 seconds** depending on your hardware. After that, corrections are near-instant.

---

## Included AI Model

This release uses **[Qwen 3.5 2B (Q4_K_XL)](https://huggingface.co/unsloth/Qwen3.5-2B-GGUF?show_file_info=Qwen3.5-2B-UD-Q4_K_XL.gguf)** — a compact, fast, and high-quality model specifically suited for text correction tasks.

---

## System Requirements

| | Minimum | Recommended |
|---|---|---|
| **OS** | Windows 10 | Windows 11 |
| **RAM** | 8 GB | 16 GB |
| **GPU** | None (CPU mode) | NVIDIA with 4 GB+ VRAM |

> **No NVIDIA GPU?** The app still works — it automatically runs in CPU mode. Corrections will take 15–60 seconds instead of 1–3 seconds.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| Icon doesn't appear in tray | Wait up to 60 seconds on first launch |
| Hotkey doesn't work in some apps | Right-click tray icon → the app must be running as Admin |
| App crashes immediately | Install [Visual C++ Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe) and retry |
| Text not being replaced | Make sure text is **selected** before pressing the hotkey |
| Slow corrections | Expected on CPU-only machines; an NVIDIA GPU speeds this up significantly |

---

## Settings

Right-click the tray icon → **Settings** to change:
- Hotkey combination
- GPU layers (set to 0 to force CPU mode, 99 for full GPU)
- Model file (drop any `.gguf` file into this folder and it appears in the menu)
- System prompt (advanced: customize correction style)

---

*All processing is 100% local. No internet connection required after setup.*
