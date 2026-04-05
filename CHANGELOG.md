# Changelog

## v3.1.0  (2026-04-03)

### Bug fixes
- **Crash on hotkey / Test hotkey**: Removed `nativeEvent` override that used
  `ctypes.wintypes.MSG.from_address()` — a C-level segfault that killed the
  process silently with no Python traceback.
- **Hotkey thread crash**: Replaced direct `tray.showMessage()` call in the
  keyboard background thread with a `_notify` signal (queued to main thread).
- **LLM chat 503**: Fixed `_send_chat` to detect when the model isn't loaded,
  show a loading indicator, and wait before starting the stream worker.
- **Scroll wheel on spinboxes**: All Settings spinboxes now ignore mouse-wheel
  events (prevents accidental value changes while scrolling the dialog).
- **Enter key shortcut**: Added `Ctrl+Enter` QShortcut that works even when
  the corrected-text area has focus. Plain `Enter` still works when the text
  box isn't focused.

### Improvements
- **Dual-pass correction**: LanguageTool shows an instant result (Pass 1);
  if the LLM is already loaded it silently refines the correction in the
  background and updates the result (Pass 2). This fixes cases where LT
  picks the wrong spelling (e.g. `exactl` → `exact` instead of `exactly`).
- **Missing Settings fields restored**: Top-K, Top-P, Min-P spinboxes now
  visible and saved correctly.
- **Global exception hook**: All unhandled exceptions (main thread and
  background threads) are now logged to `app_debug.log` with full traceback.
- **Venv cleaned**: Removed legacy packages (torch, onnxruntime, gector, onnx,
  PyQt5, safetensors, numpy, pandas, ~15 other unused dependencies).
- Updated README with accurate architecture, keyboard shortcuts, and
  troubleshooting section.

---

## v3.0.0  (2026-04-03)

### Breaking changes
- Dropped GECToR, ONNX, and all PyTorch dependencies — replaced by LanguageTool.
- Migrated from PyQt5 → **PyQt6**.
- `config.json` keys for `gector_*`, `onnx_*`, `active_corrector` removed.

### New features
- **LanguageTool** local grammar engine: ~10–50 ms corrections after warmup,
  no GPU required, covers spelling + grammar + style.
- **Streaming LLM output**: chat responses appear token-by-token in real time.
- **Cross-platform build** (`build.py`): produces a self-contained ZIP for
  Windows, macOS, or Linux in one command.
- **Dependency updater** (`update.py`): upgrades Python packages and optionally
  downloads the latest llama-server binary from GitHub.
- New **dark navy theme** — deeper blues, sharper contrast, premium look.
- Diff highlighting now uses blue tones instead of green for changed words.

### Improvements
- Codebase reduced from ~3,700 lines to ~850 lines (single file, no dead code).
- Removed 15+ redundant test/debug/inspection scripts.
- Cleaner settings dialog with scrollable form and labeled sections.
- Hotkey recorder now works correctly on PyQt6 scoped enums.
- Native resize borders on Windows (frameless window drag + resize).
- LLM system prompt override via Settings (leave blank for built-in prompt).
- Single-instance lock via socket (prevents double-launch).

### Bug fixes
- Fixed GECToR always falling back to LLM (the root issue prompting this rewrite).
- Fixed window occasionally appearing off-screen on multi-monitor setups.
- Fixed clipboard not being restored when no text was selected.

---

## v2.3.x  (2026-03)

- GECToR DeBERTa-Large integration (later broken, replaced in v3).
- ONNX model idle timers.
- Keep-loaded toggles for grammar and LLM.

## v2.1.x  (2026-03-24)

- T5-first architecture; LLM reserved for chat.
- ONNX CoEdit-Large migration with beam search.

## v2.0.0  (2026-03)

- Initial PyQt5 system-tray app.
- llama.cpp OpenAI-compatible server integration.
- Diff view with green highlights.
