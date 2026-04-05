# TextCorrector — Agent Context

**READ THIS BEFORE TOUCHING ANY CODE.**

This file is the authoritative design document for the TextCorrector project.
Every AI agent working on this codebase must follow the architecture and intent
described here. Do not deviate from the core philosophy below.

---

## Core Philosophy — Non-Negotiable

TextCorrector is designed to work **exactly like the Samsung keyboard AI autocorrect**:

- **Instant** — corrections appear as fast as the autocorrect model can respond.
- **Accurate** — catches real spelling/grammar errors without false positives.
- **Non-intrusive** — the popup appears, shows the fix, the user accepts with one key. Done.

The LLM (llama.cpp) exists in **two roles**:

1. **Autocorrect model** — a lightweight fine-tuned model loaded at boot. Handles
   instant grammar/spelling correction when the hotkey fires.
2. **Chat model** — a larger model loaded lazily on first chat use, unloaded after
   idle timeout. Handles complex editing tasks (rewrite, tone change, summarization).

Both models use the **same correction method** (`correct_text`) with the same prompt
structure. The only difference is which model file is loaded.

---

## Architecture

```
User selects text → presses hotkey
        │
        ▼
  Autocorrect LLM  ───────────────────────────────────→  Result shown in popup
  (lightweight,      grammar + spelling correction       User presses Ctrl+Enter → paste back
  loaded at boot)    (patch-based first, full-text fallback)
        │
        │  (only if user clicks "Ask AI" chat box)
        ▼
   Chat LLM        ←── User types in "Ask AI" chat box
  (larger, lazy)       e.g. "make it more formal", "shorten this", "fix only spelling"
  streaming SSE      LLM streams tokens live into chat display
```

### Correction Flow (autocorrect)

1. `_do_correction()` calls `ac_model.correct_text_patch()` first
2. LLM returns JSON array of patches: `[{"old": "wether", "new": "weather"}]`
3. `_apply_patches()` applies changes to original text locally
4. If patch fails (bad JSON, empty patches, parse error), falls back to `correct_text()` (full text)

This approach outputs only wrong words, not the entire corrected text — saving tokens and speed.

### What the autocorrect model handles:
- Spelling errors
- Grammar: subject-verb agreement, articles, tense
- Punctuation, capitalization
- Common typos

### What the chat model handles (chat-only, user-initiated):
- Rewrites, tone changes, summarization
- Style edits: make it formal / casual / shorter / longer
- Questions about the text
- Any edit the user explicitly requests

---

## File Structure

```
TextCorrector/
├── text_corrector.py      ← Single Python file. All app code lives here.
├── run.bat                ← Windows launcher (auto-elevates, activates venv)
├── requirements.txt       ← Python deps (PyQt6, keyboard, pyperclip, requests, psutil)
├── config.json            ← User settings (auto-created, do not commit personal paths)
├── build.py               ← PyInstaller cross-platform release builder
├── update.py              ← Updates Python deps and optionally llama-server binary
├── README.md              ← User-facing documentation (for GitHub)
├── AGENT_CONTEXT.md       ← THIS FILE — AI agent context (keep updated)
├── llama_cpp/             ← llama-server binary + DLLs (not committed, user-provided)
├── venv/                  ← Python virtual environment (not committed)
└── logo.png / logo.ico    ← App icons
```

No model files (*.gguf), no ONNX files, no PyTorch files, no GECToR files, no
LanguageTool JARs exist in this project.

---

## Project State

- **Single file**: `text_corrector.py` (~2088 lines)
- **GUI**: PyQt6, frameless dark-navy theme
- **Backend**: llama.cpp via `llama-server` HTTP API
- **Dual model**: autocorrect model (eager load) + chat model (lazy load)
- **No ONNX, no GECToR, no LanguageTool** — all removed

### Key Config
- Hotkey: `ctrl+shift+space`
- Correction strength: 0-4 (default 2 = Standard)
- `ac_same_as_chat`: true (use same GGUF for both roles)

---

## Key Classes (text_corrector.py)

### Helper Functions
- `strip_think(text)` — removes `<thinking>`, `
</think>

`, `<reasoning>` tags and their content
- `strip_preamble(text, original)` — strips LLM preamble ("Here is the corrected text:", markdown code fences, etc.)
- `friendly_name(path)` — converts GGUF filenames to readable display names
- `has_nvidia()` — detects NVIDIA GPU for GPU offload decision
- `log(msg)` — appends timestamped messages to `app_debug.log`

### `ModelManager(QObject)`
- Manages a single `llama-server` subprocess (llama.cpp OpenAI-compatible HTTP server)
- Two instances exist: `ac_model` (autocorrect) and `chat_model` (chat)
- `load_model()` — starts the server subprocess, polls /health, waits up to 180 s
- `unload_model()` — terminates the server subprocess
- `correct_text(text, system, examples)` — non-streaming correction; same method for both models
  - Caller provides complete system prompt and few-shot examples (strength-aware)
  - Uses `temperature: 0.0` for deterministic output
  - `max_tokens` calculated as `min(max(len(text.split()) * 2 + 30, 60), 512)`
  - Post-processes output through `strip_think()` then `strip_preamble()`
- `correct_text_patch(text, system, examples)` — structured JSON patch correction (preferred, tried first)
  - Caller provides complete system prompt and few-shot examples (strength-aware)
  - Asks LLM for JSON array of `{old, new}` patches instead of full text
  - Dramatically fewer output tokens (only wrong words, not entire text)
  - Extracts patches via `_extract_patches_from_response()`, applies via `_apply_patches()`
  - Filters out no-op patches where `old == new` (model sometimes outputs every word as a patch with identical values)
  - If all patches are no-ops, returns original text unchanged (not `None`)
  - Returns `None` only on parsing errors so caller can fall back to `correct_text()`
- `make_stream_worker(messages)` — returns a `StreamWorker` QThread for SSE streaming
- `check_idle()` — called by QTimer every 60 s; unloads model if idle > timeout (chat only)

### `StreamWorker(QThread)`
- Streams SSE tokens from llama.cpp `/v1/chat/completions`
- Emits: `token(str)`, `done(str)`, `error(str)`
- Only used by chat path — never by autocorrect

### `CorrectionWindow(QWidget)`
- Main popup shown after hotkey fires
- `_do_correction()` — background thread, builds strength-aware prompts, tries patch then full-text
  - Builds complete system prompts and few-shot examples per strength level
  - First calls `ac_model.correct_text_patch(text, system, examples)` (structured JSON, minimal tokens)
  - If patch fails (bad JSON, empty patches), falls back to `ac_model.correct_text(text, system, examples)`
  - Uses `correction_strength` config (0-4) to select correction scope:
  - Level 0: typos/misspellings only
  - Level 1: + caps, punctuation, apostrophes, quotes (Samsung-like)
  - Level 2: + clear grammar fixes (default)
  - Level 3: + sentence structure and word choice
  - Level 4: full rewrite for maximum clarity
- `_send_chat()` — triggered by user pressing Send in Ask AI box, invokes `chat_model`
- `_do_stream()` — creates StreamWorker, starts SSE streaming into chat display
- `_render_diff(text)` — uses `difflib.SequenceMatcher` to highlight changed words in blue

### `TextCorrectorApp(QApplication)`
- System tray, hotkey registration, window lifecycle
- Creates two `ModelManager` instances: `ac_model` and `chat_model`
- `ac_model` loads at boot (eager)
- `chat_model` loads on first chat use (lazy)
- `_hotkey_fired()` — runs in keyboard library's background thread; uses signals only

---

## Thread Safety Rules

**NEVER access Qt objects directly from a non-main thread.** This causes silent
segfaults. The keyboard library's hotkey callbacks run in a background thread.

Safe patterns used in this codebase:
- Emit `pyqtSignal` from background thread → slot runs in main thread via queued connection
- Use `QThread` (not `threading.Thread`) for workers that emit signals
- `_hotkey_fired` uses `self._trigger.emit()` and `self._notify.emit()` — never touches Qt widgets directly

Do NOT:
- Call `tray.showMessage()` from `_hotkey_fired` (background thread)
- Call `widget.setText()` from any `threading.Thread` worker
- Use `ctypes.wintypes.MSG.from_address(int(msg))` in `nativeEvent` (segfaults on Windows)

---

## Configuration (config.json / DEFAULT_CONFIG)

| Key | Default | Notes |
|-----|---------|-------|
| `llama_server_path` | `llama_cpp/llama-server.exe` | Path to server binary |
| `model_path` | `""` | Path to chat model GGUF |
| `ac_model_path` | `""` | Path to autocorrect model GGUF |
| `ac_same_as_chat` | `true` | If true, use `model_path` for autocorrect too |
| `server_host` | `127.0.0.1` | llama-server host |
| `server_port` | `8080` | llama-server port |
| `context_size` | `4096` | LLM context window |
| `gpu_layers` | `99` | GPU offload layers (0 = CPU only) |
| `temperature` | `0.1` | LLM temperature |
| `top_k` | `40` | Top-K sampling |
| `top_p` | `0.95` | Top-P sampling |
| `min_p` | `0.05` | Min-P sampling |
| `keep_model_loaded` | `true` | Keep chat model running between uses |
| `idle_timeout_seconds` | `300` | Unload chat model after N seconds idle |
| `hotkey` | `ctrl+shift+space` | Global hotkey |
| `system_prompt` | `""` | Override LLM system prompt (blank = use built-in) |
| `correction_strength` | `2` | Autocorrect aggressiveness: 0=Minimal, 1=Light, 2=Standard, 3=Thorough, 4=Full Rewrite |
| `recent_models` | `[]` | Recently used model paths |

---

## Known Limitations

1. **Small model quality**: Lightweight autocorrect models (<1B params) may produce
   gibberish or random output if not properly fine-tuned for grammar correction.
   The user should use a model specifically trained for this task.

2. **Autocorrect model load time**: The autocorrect model loads at boot. First
   correction after boot takes as long as the model needs to load (typically 5-15s
   for small models). Subsequent corrections are near-instant.

3. **llama-server requires a GGUF model**: A compatible GGUF model file must be
   configured in Settings for any feature to work.

---

## What Was Deleted (Do Not Re-add)

The following were part of v1/v2/v3 and were fully removed. Do not re-add them:
- GECToR model integration (gector package, ONNX runtime)
- T5 / CoEdit model integration
- PyTorch / Hugging Face transformers
- PyQt5 (migrated to PyQt6)
- LanguageTool / language_tool_python integration
- All test scripts (test_*.py, check_*.py, inspect_*.py, etc.)
- `nativeEvent` override with ctypes MSG reading (caused segfaults)
- build.ps1, download_models.bat, update_llama_cpp.bat (replaced by build.py, update.py)

---

## Correction Strength Levels

The `correction_strength` config (0-4) controls how aggressively the autocorrect model modifies text:

| Level | Name | Behavior |
|-------|------|----------|
| 0 | Minimal | Clear typos and misspellings only. No grammar, caps, or punctuation changes. |
| 1 | Light | + Capitalization, punctuation, apostrophes, quotes. No grammar or rephrasing. (Samsung-like) |
| 2 | Standard | + Clear grammar fixes (verb tense, agreement, articles, prepositions). (default) |
| 3 | Thorough | + Sentence structure and word choice improvements. |
| 4 | Full Rewrite | Maximum clarity, restructure sentences as needed. |

Level 0 is the absolute minimum (only misspelled words). Each level adds progressively more correction scope. The system prompt and few-shot examples are built per-level so the model sees exactly what to fix at each strength.

---

## Session Log

### 2026-04-06
- Fixed strength slider bug: removed hardcoded system prompt in `correct_text_patch()` that overrode per-level instructions
- Redesigned strength levels: 0=typos only, 1=Samsung-like (caps/punctuation/apostrophes), 2=+grammar, 3=+structure, 4=rewrite
- Made both `correct_text()` and `correct_text_patch()` accept caller-provided system prompts and few-shot examples
- Built strength-adaptive few-shot examples so model sees exactly what to fix at each level

### 2026-04-05
- Merged CLAUDE.md + context.md + CHANGELOG.md into single AGENT_CONTEXT.md
- Implemented patch-based autocorrect: `correct_text_patch()` in ModelManager, `_apply_patches()` and `_extract_patches_from_response()` helpers
- Modified `_do_correction()` to try patch-based first, fall back to full-text
- Squashed 7 unpushed commits into single v2.10 commit

---

## Version History

- **v2.10** — Patch-based autocorrect: LLM outputs only JSON patches for wrong words
  instead of regenerating full text. Dramatically reduces token output and speeds up
  corrections. Falls back to full-text correction if patch parsing fails.

  **Design decisions and reasoning:**
  - `max_tokens` uses same formula as full-text (`len(words)*2+30`, cap 512). The initial
    implementation used `len//2+20` (cap 150) which caused `finish_reason: "length"` — the
    JSON array got truncated mid-stream and could not be parsed. Input tokens don't affect
    speed; only output tokens do. The model naturally outputs far fewer tokens because it
    only lists wrong words, so a high ceiling is fine.
  - No-op patches (`old == new`) are filtered out after extraction. The model (especially
    smaller ones like 2B) sometimes outputs every single word as a patch with identical
    old/new values. Without filtering, these would be wasted tokens and could cause
    unnecessary regex replacements.
  - When all patches are no-ops, returns original text with "Already correct" badge instead
    of falling back to full-text. This avoids a second LLM round-trip when the text is
    already perfect.
  - `_apply_patches()` uses regex with word boundaries (`\b`) and `count=1` per patch to
    safely replace only the first occurrence of each wrong word, preserving punctuation
    adjacency and preventing accidental replacements elsewhere.
  - `_extract_patches_from_response()` tries three strategies in order: direct JSON parse,
    regex search for `[...]` array, regex search for `{...}` object with `"patches"` key.
    This handles models that wrap JSON in markdown code fences or add explanatory text.
  - **Taskbar icon fix**: Added `Qt.WindowType.Window` flag to `_position_window()`.
    `FramelessWindowHint` alone tells Windows to hide the window from the taskbar. The
    `Window` flag overrides that while keeping the frameless appearance.
- **v2.9** — LanguageTool removed, LLM-only autocorrect, dual model support
  (autocorrect model loaded at boot, chat model lazy-loaded), same correction
  method for all models, correction strength slider (0-4)
- **v2.8** — Bug fixes: nativeEvent segfault, scroll wheel spinboxes, Enter key
  shortcut, LLM 503 on chat, LT-only autocorrect enforced, HTML escaping in
  chat display, global exception hook, venv cleaned
- **v2.7** — Full rewrite: GECToR/ONNX removed, LanguageTool added, PyQt5→PyQt6,
  dark navy theme, streaming LLM chat, build.py/update.py
- **v2.3.x** — GECToR DeBERTa-Large integration (later broken), ONNX model idle timers
- **v2.1.x** — T5-first architecture; LLM reserved for chat, ONNX CoEdit-Large migration
- **v2.0** — Initial PyQt5 system-tray app, llama.cpp backend
