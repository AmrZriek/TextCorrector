# TextCorrector — Agent Context

> **Purpose**: This file orients an AI agent to the codebase. Read this before touching any code. Do NOT share with end users — see `README.md` for user docs.

---

## Project in One Line

Windows system-tray app: user selects text → presses hotkey → T5 ONNX corrects it (fast, offline) → result pasted back. Chat uses LLM for conversations. Python 3.12 + PyQt5 + ONNX Runtime + llama.cpp.

---

## File Map

```
TextCorrector/
├── v2/
│   ├── text_corrector.py      ← Main application (v2.1), all logic lives here
│   ├── requirements.txt       ← pip deps: PyQt5, keyboard, pyperclip, requests, psutil, onnxruntime, optimum, transformers
│   ├── run.bat                ← Dev launcher
│   ├── build.ps1              ← Builds release via PyInstaller
│   └── TextCorrector_Release/ ← Distributable folder (ready for GitHub)
│       ├── TextCorrector.exe
│       ├── _internal/         ← Bundled Python runtime (PyInstaller)
│       ├── llama_cpp/         ← llama-server.exe + CUDA DLLs (for chat)
│       ├── onnx_models/
│       │   └── grammar_t5/    ← T5 model for fast autocorrect
│       │       ├── encoder_model.onnx
│       │       ├── decoder_model.onnx
│       │       └── tokenizer.json
│       ├── Qwen3.5-2B-UD-Q4_K_XL.gguf  ← LLM for chat
│       └── config.json
├── onnx_models/
│   └── grammar_t5/            ← Development copy of T5 model
├── llama_cpp/                 ← Development copy of llama-server
├── CONTEXT.md                 ← This file (agent context)
└── README.md                  ← User documentation
```

**Key gotcha**: `SCRIPT_DIR` is resolved via `sys.executable` when frozen (PyInstaller), or `__file__` in dev. All paths are derived from it. Never hardcode paths.

---

## Architecture (v2.1)

### T5-First Design

| Operation | Model | Why |
|-----------|-------|-----|
| **Autocorrect (hotkey)** | T5 ONNX | Fast (~100ms), offline, no GPU required |
| **Chat refinement** | LLM (Qwen3.5-2B) | Conversational, understands context |
| **Startup** | ONNX preloads | LLM only loads if chat is used |

### Model Loading Flow

```
App Start
  ├─ ONNX configured? → Load T5 → Tray shows "ONNX Ready"
  │                     └─ LLM NOT loaded (saves RAM)
  │
  └─ ONNX not configured? → Load LLM → Tray shows model name
```

### Chat Output Detection

The `_detect_chat_output_type()` method determines if chat output should be:
- **Pasted** (correction request): Similar length to input, no preamble
- **Displayed** (conversation): Contains explanations, answers, questions

---

## Configuration

### First-Time Setup

1. Run `v2/run.bat`
2. Open Settings (right-click tray icon)
3. **ONNX Model Directory**: Click Browse → Select `onnx_models/grammar_t5/`
4. **Save** and restart

### Settings Reference

| Setting | Default | Description |
|---------|---------|-------------|
| ONNX Model Directory | (empty) | Path to T5 model folder |
| Hotkey | Ctrl+Alt+C | Trigger autocorrect |
| System Prompt | (editable) | Instructions for LLM |

---

## Build for Release

```powershell
# In v2/ directory:
powershell -ExecutionPolicy Bypass -File build.ps1

# Output: v2/TextCorrector_Release/
# Ready to zip and upload to GitHub
```

---

## ONNX T5 Debugging Issue - 2026-03-25/28 (RESOLVED)

### Issue Description

The ONNX T5 model failed during inference with a `MatMul dimension mismatch` error on the second generated token. The app was falling back to the slow LLM (~5-10 seconds) instead of using the T5 ONNX (~100ms).

### Root Cause Analysis

The investigation uncovered **two layered bugs** in the KV-cache handling of the autoregressive decoder loop, caused by how the merged `optimum` ONNX model was exported:

1. **Bug 1: Encoder/Decoder KV-cache Swap (positional indexing mismatch)**
   - Initial code assumed `(enc_key, enc_value, dec_key, dec_value)` ordering for decoder outputs.
   - The actual outputs strictly follow: `decoder.key, decoder.value, encoder.key, encoder.value`.
   - Result: decoder KVs were being fed into encoder KV inputs, failing expectations.

2. **Bug 2: Encoder KV-cache Evaporation (`optimum::if` node behavior)**
   - The merged `decoder_model.onnx` has an internal `optimum::if` branch controlled by `use_cache_branch`.
   - **Step 0 (`use_cache_branch=False`)**: Computes encoder cross-attention KVs and outputs proper shape `(1, 8, seq_len, 64)`.
   - **Step 1+ (`use_cache_branch=True`)**: Sub-graph caches encoder KVs internally and outputs **empty tensors** `(0, 8, 1, 64)`.
   - Previous code blindly fed those empty tensors back to the next inference step, causing an immediate `Broadcast on dim 0` failure.

### The Fix

1. **Name-based Input Mapping**: Replaced fragile positional indexing with direct string matching (`present.X.decoder.key` → `past_key_values.X.decoder.key`).
2. **Encoder KV Preservation**: Captured the valid encoder KV tensors from `step 0`, and systematically injected them manually into all subsequent steps (while updating only decoder KVs per step).
3. **Consolidated Architecture**: Extracted a unified `_run_seq2seq()` method used by both `proofread()` and `chat()`.

### Current Status

- **ONNX Inference is FIXED**: The T5 model correctly decodes multiple tokens without crashing.
- **Model Quality Limitation**: While the code pipeline works perfectly, the specific `grammar_t5` parameters are lackluster. It is highly recommended to upgrade to a modern Edge AI / SLM (like Llama-3.2-1B, Qwen-2.5-0.5B, or a better fine-tuned Flan-T5) for Samsung-like professional grammar correction.

---

**Last Updated**: 2026-03-26 | **Version**: 2.1

## v2.1 (T5-First Architecture) - 2026-03-24

This update makes ONNX T5 the default model for autocorrect operations, with LLM (llama.cpp) reserved for chat conversations only.

### Architecture Changes

1.  **T5-First Model Loading**:
    -   ONNX model preloads at startup if `onnx_model_dir` is configured
    -   LLM (llama.cpp) is skipped at startup if ONNX is available
    -   LLM only loads when user initiates a chat conversation
    -   Tray tooltip shows "ONNX Ready" when model is loaded

2.  **Settings UI Changes**:
    -   New "ONNX Model Directory" section added to Settings dialog
    -   Located after "Model Path" section
    -   Users can browse and select the `onnx_models/grammar_t5/` folder
    -   Setting persists in `config.json` as `onnx_model_dir`

3.  **Chat Guardrails**:
    -   Chat now uses LLM with strict system prompt
    -   System prompt blocks meta-commentary ("Sure, here is...", "Here's the corrected text")
    -   `/no_think` directive added to prevent reasoning output
    -   New `_detect_chat_output_type()` method detects correction vs conversation output

### Bug Fixes Applied

| Fix | Description |
|-----|-------------|
| Question mark detection removed | Was triggering retry on any `?` in output |
| Quote stripping fixed | Now checks `original_text` first before stripping |
| Stop sequences added | Added to API payloads to prevent run-on generation |
| Retry prompt fixed | Now uses same few-shot format as main prompt |
| Meta detection tuned | Made less aggressive to reduce false positives |

### ONNX Integration Fixes

| Fix | Description |
|-----|-------------|
| Status emission | Added `status_changed.emit()` on ONNX errors (no more silent fallback) |
| Post-processing pipeline | Added `strip_thinking_tokens`, `strip_meta_commentary` to ONNX output |
| ONNX chat() method | Added dedicated chat method for conversation mode |
| Loading mutex | Added `threading.Lock` to prevent concurrent load attempts |
| CUDA fallback retry | Added retry logic for CUDA errors |
| is_loaded state | Fixed state management to prevent race conditions |

### Known Issues

| Issue | Status | Notes |
|-------|--------|-------|
| No UI indicator showing active model (T5 vs LLM) | PENDING | Tray tooltip shows "ONNX Ready" but main correction window doesn't show which model is being used for each correction |

### File Map Updates

```
TextCorrector/
├── v2/
│   ├── text_corrector.py      ← Main v2 application with ONNX support
│   └── TextCorrector_Release/ ← Distributable folder with ONNX models bundled
│       ├── TextCorrector.exe
│       ├── _internal/
│       ├── llama_cpp/
│       ├── onnx_models/       ← ONNX models copied for release (2026-03-24)
│       │   ├── grammar_t5/    ← T5 model for grammar correction
│       │   │   ├── encoder_model.onnx
│       │   │   ├── decoder_model.onnx
│       │   │   └── tokenizer.json
│       │   └── gpt2/
│       └── config.json
├── onnx_models/
│   ├── grammar_t5/            ← T5 encoder-decoder model for grammar correction
│   │   ├── encoder_model.onnx
│   │   ├── decoder_model.onnx
│   │   ├── tokenizer.json
│   │   └── special_tokens_map.json
│   └── gpt2/                  ← GPT-2 causal LM model (alternative)
│       └── model.onnx
```

### How to Configure

1.  Run `v2/run.bat` (NOT root `run.bat`)
2.  Open Settings → Navigate to "ONNX Model Directory" section
3.  Click Browse → Select `onnx_models/grammar_t5/` folder
4.  Save settings and restart application
5.  Tray tooltip should display "ONNX Ready" when loaded

---

**Last Updated**: 2026-03-24 | **Version**: 2.1
