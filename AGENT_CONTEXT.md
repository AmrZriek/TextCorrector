# TextCorrector — Agent Context

**READ THIS ENTIRE FILE BEFORE TOUCHING ANY CODE.**

This is the authoritative design document for TextCorrector v4.0.
All AI agents working on this codebase must follow the architecture and constraints here.

---

## Core Philosophy — Non-Negotiable

TextCorrector works **exactly like the Samsung keyboard AI autocorrect**:

- **Instant** — corrections appear as fast as the model can respond.
- **Accurate** — catches real spelling/grammar errors without false positives.
- **Non-intrusive** — popup appears, shows the fix, user accepts with one key. Done.

---

## LOCKED ARCHITECTURE — DO NOT CHANGE WITHOUT USER APPROVAL

These decisions are final. Do not change, "improve," or refactor them unless the user explicitly asks.

### 1. Backend: llama.cpp ONLY
- Single binary: `llama-server.exe` (HTTP API on port 8080, OpenAI-compatible)
- **No LanguageTool, no GECToR, no ONNX, no Java** — all were removed in v2.7+
- Model format: GGUF only, loaded via llama.cpp
- Do NOT re-add any of the removed backends

### 2. Thinking mode: ALWAYS disabled server-side
- Server launched with `--reasoning off` in the `cmd` array inside `load_model()`
- API payloads also send `"think": False` (defense-in-depth, but alone it's insufficient)
- **Do NOT revert to `--reasoning-budget 0`** — this was tried and failed for Gemma 4
- If corrections start returning empty output, check `app_debug.log` for `reasoning_content present`

### 3. ac_same_as_chat — one server for both roles
- `ac_same_as_chat = True` (default): ONE llama-server handles both autocorrect and chat
- `_send_chat()` checks this flag and routes to `self.ac_model` when AC is loaded — no second server
- Do NOT launch a second server when `ac_same_as_chat = True`
- Two servers on port 8080 caused `taskkill` to kill the AC server on every chat request

### 4. CUDA DLL injection on Windows
- `ggml-cuda.dll` (in the server folder) depends on `cudart64_12.dll`, `cublas64_12.dll`, `cublasLt64_12.dll`
- These are NOT in system PATH by default on most systems
- `load_model()` searches Ollama's bundled CUDA, CUDA Toolkit install paths, and adds them to the subprocess `env["PATH"]` before launching `llama-server`
- Without them, the server silently falls back to CPU with no error message

### 5. GUI: output boxes are READ-ONLY
- `corr_edit` (corrected text box): `setReadOnly(True)` — do not remove
- `orig_edit` (original text box): `setReadOnly(True)` — do not remove
- Users must not be able to edit either box directly

### 6. Line breaks in diff output
- `_render_diff()` uses a `\x00NL\x00` placeholder before word-splitting
- `\n` → `{NL}` → split → diff → `<br>` in final HTML
- Do NOT revert to plain `.split()` + `" ".join()` — it collapses all line breaks into one paragraph

### 7. No dynamic window resizing
- No `setFixedHeight()` on text boxes after content is set — causes abrupt window shrink
- No `adjustSize()` after correction renders — same reason
- `_fit_text_boxes()` was removed; do not re-add it
- Window size is set once at init (`min(740, 80% of screen)` × `min(860, 90% of screen)`)

### 8. Single-file deployment
- All app code lives in `text_corrector.py` — no sub-modules, no packages
- `build.py` uses PyInstaller → single-folder release + ZIP
- `build.py` auto-detects the llama-server folder from `config.json`, not hardcoded `llama_cpp/`

### 9. Patch mode: terminal punctuation uses best-fit mark, not always a period
- When a sentence is missing end-of-sentence punctuation, the patch prompt instructs the model to add whichever mark fits the meaning: `?` for questions, `!` for exclamations, `.` otherwise
- The phrase "like periods at the ends of sentences" was removed from the prompt — it biased the model to always append a period even on question/exclamation sentences
- Few-shot examples include a question-mark case (`"came late agian?"`) and an exclamation case (`"wait!"`) to give the model clear precedent
- Punctuation *correction* (fixing wrong punctuation) is fine; punctuation *insertion that changes meaning* (forcing `.` on a question) is not

### 10. Custom system prompt is appended, never replaces
- When `system_prompt` config is set, it is appended to the base prompt as `"\n\nAdditional instructions:\n{custom_sys}"` — never replaces the base constraints
- The base prompt always includes `"OUTPUT ONLY the corrected text"` (full-text mode) or `"Output ONLY a JSON array"` (patch mode) — these must remain present regardless of custom instructions
- In patch mode (mode 1), custom instructions are also appended to the patch system prompt so they actually take effect
- Previously, `custom_sys or (default)` replaced the entire prompt, losing output-format constraints → model added conversational filler → triggered retry → 2x latency

### 11. Long texts are chunked at sentence boundaries, never truncated
- `correct_text_patch()` splits long texts into sentence-aligned chunks when the input would exceed the context window's safe capacity
- Each chunk gets its own LLM request with a full output token budget (≥1024 tokens for patches)
- Chunks are reassembled with their original inter-chunk whitespace/newlines preserved via `_chunk_text_by_sentences()`
- Overhead is estimated from the actual system prompt + examples (not a fixed constant) — prevents underestimating and starving the output budget
- If a chunk fails in multi-chunk mode, its original text is kept and processing continues (partial success is better than total failure)
- Previously, a single-shot request for 3000+ words left only 256 output tokens → patches were truncated → end of text never got corrected
- The chunking threshold adapts to the user's `context_size` setting — larger contexts allow larger single-shot texts

### 12. Patch mode uses iterative multi-pass (max 3), not single-shot
- `_do_correction()` runs `correct_text_patch()` in a loop (up to `MAX_PATCH_PASSES = 3`), feeding corrected text back as input until no more changes are found
- This is necessary because small on-device LLMs miss subtle errors on the first pass when focused on obvious ones — the second pass catches stragglers (same principle as GECToR's iterative refinement)
- The loop terminates early when the result matches the input (converged) or when patch extraction fails (falls back to full-text)
- For already-correct text, only one pass runs (returns `[]` immediately) — no extra latency
- The method badge shows pass count when >1 (e.g. "Smart Fix (patch 2x)")
- Pass 2+ is skipped when pass 1 was a light edit (< 3 word-level changes) on a short text (≤ 100 words) — most typos fully resolve in pass 1, and additional passes on short text just burn latency

### 13. Hotkey re-entrancy guard & notification throttle
- `TextCorrectorApp._hotkey_busy = threading.Lock()` with non-blocking `acquire(blocking=False)` — rapid repeats from holding the hotkey are dropped, not queued
- If the correction window is already visible, the hotkey raises/activates it instead of starting a new clipboard flow
- "No text selected" tray notifications are throttled to ≤ 1 per 3 s via `self._last_empty_notify_ts`
- Do NOT remove any of these guards — without them, holding the keys spawned overlapping threads, each firing its own notification in a feedback loop

### 14. Sampling params must flow through CLI AND every request payload
- `load_model()` passes `--temp`, `--top-k`, `--top-p`, `--min-p`, `--repeat-penalty`, `--frequency-penalty`, `--presence-penalty`
- `correct_text_patch()` payload includes the same set (except `temperature`, which is forced to `0.0` for patch-mode determinism)
- `correct_text()` and `make_stream_worker()` payloads include all seven
- If you add a new sampling setting, wire it through all three paths — leaving any path unwired means the setting silently has no effect

### 15. First-run setup dialog on blank model_path
- `TextCorrectorApp.__init__` schedules `_show_first_run()` via `QTimer.singleShot(800, ...)` when `model_path` is blank
- Dialog offers three paths: "Download recommended" (launches shipped `download_model.bat` / `.sh`), "Browse existing…", and "Skip"
- Do NOT remove — non-technical users who unzip a fresh release would otherwise see a silent tray icon and have no entry point

### 16. Release builds produce ONE artifact: the ZIP
- `build.py` deletes `dist/<release>/` and `build/` (PyInstaller scratch) after ZIP creation
- `--keep-folder` opts out for local debugging; `--no-zip` also preserves folders
- Reason: users saw `build/TextCorrector/TextCorrector.exe`, double-clicked it, and got "Failed to load Python DLL python313.dll" because that folder is PyInstaller's intermediate scratch (missing assets and python3*.dll). A single ZIP removes the footgun.

### 17. Tiny-model (<1B) safeguards — three layers remain
- **Load-time warning:** `ModelManager.model_warning` signal + tray popup when `_model_size_billions() < 1.0`
- **Output guards:** `_is_corrupt_output()` (rejects `[UNK_BYTE_...]`, control chars, ≥2 `▁` artifacts) and `_is_fewshot_echo()` (rejects verbatim few-shot example outputs) apply in both patch and full-text paths
- **Simplified prompt branch:** when `_is_tiny` (size_b < 1.0), Smart Fix uses a minimal 3-line system prompt instead of the full rule list
- **DO NOT re-add `response_format.json_schema` to `correct_text_patch()` payload** — grammar-constrained decoding in llama.cpp filters every sampled token, causing 3–10× slowdown that made autocorrect hang on any text. Removed 2026-04-17. The existing output guards are sufficient.
- Recommended model: Gemma 4 E2B Unsloth UD Q4_K_XL (bundled via `download_model.bat/.sh`, defined as `_RECOMMENDED_MODEL_URL` in `build.py`)

### 18. Context-window math uses config value
- The system uses the user's configured `context_size` (default 12800) for all chunking math.
- The `actual_ctx_size` from `/props` is logged for diagnostic purposes but ignored for math because some GGUF metadata underreports capacity (e.g. Gemma 4 E2B reports 4096 but handles 12800 perfectly).
- Output budget is `clamp(estimated_input_words × 4, 256, 2048)` to prevent models from generating thousands of no-op patches and hitting `finish_reason=length`.

### 20. Patch prompt must not modify numbers, dates, or intentional ALL CAPS
- The patch system prompt contains explicit rules: "NEVER change numbers, dates, URLs, code, or specific values" and "NEVER alter intentional styling: preserve ALL CAPS words, initialisms (NASA, USA), and Title Case exactly as the user wrote them"
- Root cause: without these rules the model interpreted `ALL CAPS` as incorrect capitalization and lowercased it, and treated a value like `0.0735` as needing to "match" context clues (user writing "3 decimals" → model rounds to `0.074`)
- Only fix capitalization that is clearly a typing mistake (lowercase `i` pronoun, lowercase first word of sentence)
- Do NOT remove these rules — they were added after confirmed user-visible bugs

### 21. Hotkey registered with suppress=True and trigger_on_release=True
- `keyboard.add_hotkey(hk, self._hotkey_fired, suppress=True, trigger_on_release=True)`
- `suppress=True` consumes the key combination so it does NOT pass through to the focused app. Without it, a hotkey ending in a printable key (Space) typed that character into the user's text field before the callback ran — the selected text was replaced by a space
- `trigger_on_release=True` fires the callback once per intentional press-release cycle rather than on every auto-repeat tick while keys are held — complements the `_hotkey_busy` re-entrancy lock
- Do NOT revert to `suppress=False` — that is the direct cause of the "hotkey replaces text with a space" bug

### 22. Pass termination: skip further passes for short text with light edits
- In `correct_text_patch()`, the pass-termination condition is `if changes < 3 and (cw <= 150 or pass_num >= 2)`
- For short/medium texts (≤ 150 words), if pass 1 made fewer than 3 word-level changes, additional passes are skipped — they typically just add stylistic tweaks the user didn't request and cost 1–3 s each
- For longer texts, at least 2 passes run before the light-edit early-exit applies
- Do NOT revert to `if pass_num >= 2 and changes < 3` — that old check always ran pass 2 even for short, already-converged texts

### 23. Chat first turn embeds user text inline
- `_send_chat()` when `self.chat_history` is empty builds a single user message: `f"Task: {msg}\n\nText:\n{self.corrected}"`
- Do NOT revert to the old 3-message prefill (system + fake-user "Here is the text" + fake-assistant "Understood") — that caused Gemma 4 / Qwen to reply "Please provide the text" because the conversation history claimed the text was already acknowledged

---

## Architecture

```
User selects text → presses hotkey
        │
        ▼
  ac_model (ModelManager)
  ┌─────────────────────────────────────────────────────────────┐
  │ 1. correct_text_patch() — asks LLM for JSON patches         │
  │    [{"old": "wether", "new": "weather"}, ...]               │
  │ 2. If patch fails → correct_text() — full corrected text    │
  └─────────────────────────────────────────────────────────────┘
        │
        ▼
  CorrectionWindow shows diff (changed words highlighted blue)
  User presses Ctrl+Enter → paste back
        │
        │  (only if user types in "Ask AI" chat box)
        ▼
  chat_model = ac_model (same server, when ac_same_as_chat=True)
  StreamWorker streams SSE tokens live into chat display
```

---

## File Structure

```
TextCorrector/
├── text_corrector.py      ← Single Python file. ALL app code lives here (~2900 lines)
├── build.py               ← PyInstaller release builder (Windows/macOS/Linux ZIPs)
├── requirements.txt       ← Python deps: PyQt6, keyboard, pyperclip, requests
├── config.json            ← User settings (auto-created on first run, gitignored)
├── README.md              ← Public GitHub documentation
├── AGENT_CONTEXT.md       ← THIS FILE — AI agent context (gitignored, local only)
├── llama-<build>-*/       ← llama-server binary + DLLs (gitignored, user-provided)
├── venv/                  ← Python venv (gitignored)
└── logo.png / logo.ico    ← App icons
```

No model files (`*.gguf`), no ONNX, no GECToR, no LanguageTool JARs, no PyTorch in this project.

---

## Key Classes (text_corrector.py)

### Helper Functions
- `strip_thinking_tokens(text)` — removes `<think>`, `<thinking>`, `<reasoning>` tags and content
- `strip_meta_commentary(text)` — strips LLM preambles ("Here is the corrected text:", code fences)
- `contains_meta_commentary(text)` — detects if output contains conversational filler
- `_extract_content_from_response(resp)` — extracts `(content, finish_reason)` from API response; detects thinking models where `content` is empty and `reasoning_content` has output
- `_extract_patches_from_response(raw)` — extracts JSON patches; returns `None` on parse failure (triggers fallback), `[]` for valid empty (text already correct)
- `_apply_patches(original, patches)` — word-level regex patches with case-insensitive fallback + post-processing (contractions, capitalization, standalone 'i')
- `_chunk_text_by_sentences(text, max_words)` — splits text at sentence/paragraph boundaries into chunks of ≤ max_words; returns `(chunk_text, separator)` tuples for lossless reassembly; used by `correct_text_patch()` to avoid context window overflow on long texts
- `_checkbox_css()` — generates QSS for checkbox checkmark using SVG (Qt theme blocks native rendering)
- `has_nvidia()` — detects NVIDIA GPU via nvidia-smi
- `log(msg)` — timestamped append to `app_debug.log`
- `friendly_name(path)` — converts GGUF filenames to readable display names

### `ConfigManager`
- Wraps `config.json` with typed get/set and lazy save

### `ModelManager(QObject)`
- Manages one `llama-server` subprocess instance
- Two instances: `ac_model` (autocorrect, eager load) and `chat_model` (chat, lazy load)
- Key methods:
  - `load_model()` — kills orphaned servers, injects CUDA PATH, starts server, polls `/health` up to 180 s
  - `unload_model()` — terminates server process
  - `correct_text(text, system, examples)` — non-streaming correction
  - `correct_text_patch(text, system, examples)` — JSON patch correction (preferred, tried first)
  - `make_stream_worker(messages)` — returns `StreamWorker` for SSE streaming (chat only)
  - `check_idle()` — QTimer every 60 s; unloads if idle > timeout (skip if `keep_model_loaded`)
- All API payloads send `"think": False`
- Server launched with `--reasoning off` and `--no-warmup`

### `CorrectionWindow(QWidget)`
- Main popup; appears near cursor on hotkey press
- `_do_correction()` — background thread; tries patch → falls back to full-text
- `_send_chat()` — routes to `ac_model` when `ac_same_as_chat=True` and AC is loaded
- `_do_stream()` — picks correct ModelManager backend, creates StreamWorker
- `_render_diff(text)` — word-level diff with `\x00NL\x00` newline placeholder → `<br>` in HTML

### `TextCorrectorApp(QApplication)`
- System tray, hotkey registration, window lifecycle
- `ac_model` loads at boot (eager)
- `chat_model` loads on first chat use (lazy), reused if `ac_same_as_chat=True`

---

## Thread Safety Rules

**NEVER access Qt objects directly from a non-main thread.** The `keyboard` library's hotkey callbacks run in a background thread.

- Emit `pyqtSignal` from background thread → slot runs in main thread via queued connection
- Use `QThread` (not `threading.Thread`) for workers that emit signals
- `_hotkey_fired` uses `self._trigger.emit()` only — never touches Qt widgets directly

---

## Configuration (config.json)

| Key | Default | Notes |
|-----|---------|-------|
| `llama_server_path` | `""` | Path to `llama-server[.exe]` binary |
| `model_path` | `""` | Path to chat/autocorrect GGUF |
| `ac_model_path` | `""` | Path to separate autocorrect GGUF (if `ac_same_as_chat=false`) |
| `ac_same_as_chat` | `true` | Reuse chat model for autocorrect (one server) |
| `server_host` | `127.0.0.1` | llama-server host |
| `server_port` | `8080` | llama-server port |
| `context_size` | `4096` | LLM context window |
| `gpu_layers` | `99` | GPU offload layers (0 = CPU only) |
| `temperature` | `0.1` | LLM temperature |
| `top_k` | `40` | Top-K sampling |
| `top_p` | `0.95` | Top-P sampling |
| `min_p` | `0.05` | Min-P sampling |
| `keep_model_loaded` | `true` | Keep model in memory between uses |
| `idle_timeout_seconds` | `300` | Unload after N seconds idle (only if `keep_model_loaded=false`) |
| `hotkey` | `ctrl+shift+space` | Global hotkey |
| `system_prompt` | `""` | Override LLM system prompt (blank = use built-in) |
| `correction_mode` | `1` | 0 = conservative, 1 = Smart Fix (patch-based) |
| `correction_strength` | `4` | 0=Minimal, 1=Light, 2=Standard, 3=Thorough, 4=Full Rewrite |
| `custom_templates` | `[]` | User-defined chat/rewrite templates |

---

## Correction Strength Levels

| Level | Name | Behavior |
|-------|------|----------|
| 0 | Minimal | Clear typos and misspellings only |
| 1 | Light | + Capitalization, punctuation, apostrophes (Samsung-like) |
| 2 | Standard | + Clear grammar fixes (verb tense, agreement, articles) |
| 3 | Thorough | + Sentence structure and word choice improvements |
| 4 | Full Rewrite | Maximum clarity, restructure sentences as needed |

---

## Known Limitations

1. **Thinking models** — llama.cpp auto-activates thinking mode for Qwen 3.x / Gemma 4 (based on GGUF chat template `<think>` / `<start_of_thought>` tokens). Fixed with `--reasoning off` server flag. If corrections fail again, check `app_debug.log` for `reasoning_content present`.

2. **CUDA DLLs** — On Windows, `cudart64_12.dll` etc. are not in PATH by default. `load_model()` searches common locations (Ollama, CUDA Toolkit) and injects them into the subprocess PATH. If GPU is not being used, check `server_log.txt` for missing CUDA backend lines.

3. **First-run load time** — Model loads at boot. First correction takes 3–15 s while llama-server starts. Subsequent corrections are near-instant.

4. **Small model quality** — Models under 2B params may produce poor corrections. Gemma 2B or Qwen 2.5 3B Q4_K_M are the recommended minimum.

---

## What Was Deleted — DO NOT RE-ADD

- GECToR / ONNX Runtime integration
- T5 / CoEdit model integration
- PyTorch / Hugging Face Transformers
- LanguageTool / `language_tool_python`
- PyQt5 (migrated to PyQt6 in v2.7)
- `nativeEvent` override with ctypes MSG reading (caused segfaults)
- `_fit_text_boxes()` dynamic resize method (caused abrupt window shrink)
- `--reasoning-budget 0` server flag (replaced by `--reasoning off`)
- All test scripts (`test_*.py`, `check_*.py`, `inspect_*.py`)
- `build.ps1`, `download_models.bat`, `update_llama_cpp.bat`

---

## Session History

### 2026-04-20 (latest)
- **Fixed: hotkey replaces selected text with a space** — Root cause: `suppress=False` let Ctrl+Shift+Space pass through to the focused app, which typed a space before the callback could run. Fix: `suppress=True, trigger_on_release=True`. Added locked rule #21.
- **Fixed: terminal punctuation always forced to period** — Prompt said "missing punctuation (like periods at the ends of sentences)", biasing the model to append `.` even on questions and exclamations. Fix: prompt now says "add whichever fits the meaning: `?` for questions, `!` for exclamations, `.` otherwise". Added few-shot examples with `?` and `!`. Updated locked rule #9.
- **Fixed: model rounding numbers it shouldn't touch** — Model was "correcting" `0.0735` to `0.074` based on surrounding context clues ("3 decimals"). Fix: patch prompt now includes explicit rule "NEVER change numbers, dates, URLs, code, or specific values". Added few-shot p-value example that leaves numbers unchanged. Added locked rule #20.
- **Fixed: ALL CAPS text being lowercased** — Model interpreted intentional ALL CAPS as "incorrect capitalization". Fix: patch prompt now includes "NEVER alter intentional styling: preserve ALL CAPS words, initialisms (NASA, USA), and Title Case exactly". Added few-shot example showing ALL CAPS preserved with exclamation. Added locked rule #20.
- **Improved: patch multi-pass termination for short texts** — Pass 2/3 were always running on short texts even when pass 1 fully resolved all errors, adding 1–3 s per unnecessary pass. Fix: early-exit now applies after any pass (not just pass 2+) when text ≤ 150 words and changes < 3. Added locked rule #22.

### 2026-04-18
- **Fixed: patch algorithm performance & no-op floods** — Root cause: `cw * 20` output token budget was massive (e.g. 3400 tokens), and `actual_ctx_size` override forced chunk math to assume 4096 total context. The model would spend 40-50 seconds per pass generating thousands of `{"old":"Hey", "new":"Hey"}` patches until it hit `finish_reason=length`. Fix: Reduced patch output budget to `min(max(cw * 4, 256), max_output, 2048)` and added an early-exit check to break the multi-pass loop if >50% of generated patches are no-ops.
- **Fixed: missing trailing periods in patch mode** — Model was stripping terminal punctuation because the prompt said "Do NOT add new punctuation". Fix: added explicit instruction to "Preserve all existing punctuation including trailing periods" and added a post-processing step in `_apply_patches()` to explicitly restore trailing `.?!` if it was in the original text but stripped from the result.
- **Changed: dynamic context window math override removed** — `actual_ctx_size` from `/props` is no longer used for chunk math because GGUF metadata often underreports (Gemma 4 E2B reports 4096 but runs fine at 12800). Hardcoded `DEFAULT_CONFIG` default raised from 4096 to 12800. Updated locked rule #18.
- **Fixed: Smart Fix prompt didn't add missing periods or fix incorrect capitalization** — The prompt said "Do NOT add new punctuation" which prevented the model from inserting sentence-ending periods. Also, the model was never told to de-capitalize incorrectly capitalized mid-sentence words. Fix: Smart Fix prompt now instructs to add missing sentence-ending periods and fix incorrect mid-sentence capitalization. Added a 4th few-shot example demonstrating de-capitalization (`the Meeting was Great` → `The meeting was great`). Conservative mode remains unchanged (typos only, no punctuation changes).
- **Fixed: CorrectionWindow had no taskbar icon** — The popup window never called `setWindowIcon()`, so the Windows taskbar showed a blank/generic icon. Fix: `_position_window()` now sets the window icon from `logo.png`.

### 2026-04-17
- **Fixed: hotkey unreliable — slow to open, occasionally dead, "no text" spam** — Root cause trio: (a) no re-entrancy guard, so holding the hotkey spawned overlapping `_hotkey_fired` threads that each re-notified; (b) the flow always restarted the clipboard dance even when a window was already open; (c) sleeps were too long (0.1 s / 0.05 s / 20×50 ms polling). Fix: added `threading.Lock` with non-blocking acquire, early-exit raising an existing window, shortened sleeps to 0.03 s and polling to 10×30 ms, throttled "no text selected" notifications to 3 s intervals. Added locked rule #13.
- **Fixed: sampling params silently ignored** — `load_model()` only passed `--ctx-size` / `--n-gpu-layers`, and `correct_text_patch()` / `make_stream_worker()` payloads only included `top_k` / `top_p`. User changes to `min_p`, `repeat_penalty`, `frequency_penalty`, `presence_penalty` had no effect. Fix: wired all seven sampling params through the CLI plus every request payload. Added locked rule #14.
- **Fixed: built release output "I don' know if its gona work." for every input** — User had loaded a 270M grammar model that produced tokenizer garbage (`[UNK_BYTE_0xe29681▁released]`, DEL chars). Patch extraction failed → fell back to full-text → model echoed a few-shot example verbatim. Fix: `_is_corrupt_output()` (rejects tokenizer artifacts), `_is_fewshot_echo()` (rejects verbatim example echoes with Jaccard overlap check), both applied in patch and full-text paths. Also added `model_warning` signal that tray-notifies on load when `_model_size_billions() < 1.0`. Added locked rule #17.
- **Fixed: PyInstaller scratch folder confused users** — Double-clicking `build/TextCorrector/TextCorrector.exe` produced `Failed to load Python DLL 'python313.dll'`. That folder is PyInstaller's intermediate workpath, not a runnable build. Fix: `build.py` now deletes both `dist/<release>/` and `build/` after ZIP creation (with `--keep-folder` opt-out). Release produces exactly one artifact. Added locked rule #16.
- **Fixed: built app had no logo** — `datas=[(f, '.')]` in the PyInstaller spec routes files to `_internal/`, but code at `SCRIPT_DIR / "logo.png"` looks next to the EXE when frozen. Fix: `build.py` explicitly `shutil.copy2`'s `logo.png` / `logo.ico` / `_checkmark.svg` into `out_dir`.
- **Fixed: download_model script pointed at ancient Qwen 2.5 3B** — Replaced with Gemma 4 E2B (Unsloth UD Q4_K_XL, ~1.8 GB). Constants `_RECOMMENDED_MODEL_URL` / `_FILE` in `build.py` drive both `.bat` and `.sh` scripts.
- **Fixed: settings panel sometimes taller than screen** — Hardcoded `setMinimumSize(580, 680)` + `resize(680, 820)` ignored small/laptop screens. Fix: screen-relative clamping via `QApplication.primaryScreen().availableGeometry()` (min = min(hardcoded, 80/85% screen), resize = min(hardcoded, 90% screen), then re-center).
- **Fixed: chat replied "Please provide the text" despite being given text** — Old prefill used system + fake-user "Here is the text: …" + fake-assistant "Understood. Ready for your question." The model read the history as "text already delivered and acknowledged" and asked the user for new text. Fix: first turn is now a single real user message `"Task: {msg}\n\nText:\n{self.corrected}"`. Added locked rule #23.
- **Added: first-run setup dialog** — `TextCorrectorApp._show_first_run()` fires via `QTimer.singleShot(800, ...)` when `model_path` is blank. Offers "Download recommended" (launches bundled script in a visible terminal), "Browse existing…", or "Skip". Added locked rule #15.
- **Added: llama-server auto-detect** — `_find_shipped_llama_server()` scans `SCRIPT_DIR` for any `llama*/llama-server[.exe]`. Makes unzipped releases plug-and-play. `load_model()` persists the discovered path to config.json on first success.
- **Reverted: JSON-schema-constrained patch decoding** — Added then removed same session. Grammar-constrained decoding caused 3–10× slowdown (every token filtered against schema), making autocorrect hang on longer texts and become extremely slow on shorter ones. `response_format` removed from `correct_text_patch()` payload. Output guards (`_is_corrupt_output`, `_is_fewshot_echo`) remain as the protection layer instead.
- **Added: tiny-model simplified prompt branch** — When `_model_size_billions(ac_path) < 1.0`, Smart Fix uses a 3-line system prompt instead of the full rule list. Combined with the schema constraint, this lets small models (270M grammar, phi-mini) at least produce valid JSON.
- **Improved: conditional extra patch passes** — Previously ran up to 3 passes unconditionally. Now pass 2+ is skipped when pass 1 was a light edit (< 3 word-level changes) on a short text (≤ 100 words). Keeps multi-pass benefits on long/dense texts without burning latency on one-typo fixes.
- **Improved: context-window math** — Raised tok/word estimate from 1.3 to 1.6 (JSON examples tokenize denser than plain English). Output budget is now `clamp(estimated_input × 0.4, 256, 2048)` instead of hardcoded 1024. Added `/props` fetch after load so `actual_ctx_size` reflects the server's real n_ctx even when the GGUF caps it below the user's request. Added locked rule #18.

### 2026-04-12
- **Fixed: long texts skipping corrections at the end** — Root cause: single-shot patch requests for 3000+ word texts consumed the entire context window, leaving only 256 output tokens for patches. The LLM couldn't generate enough patches to cover the full text, silently dropping corrections at the end. Fix: `correct_text_patch()` now splits long texts into sentence-aligned chunks via `_chunk_text_by_sentences()`, each chunk gets its own LLM request with full output budget. Overhead is estimated from actual prompt content, not a fixed 300-token constant. Added locked rule #11.
- **Fixed: overcorrecting — LLM inserting periods/commas mid-sentence** — Root cause: patch system prompt said "Add missing periods, question marks, commas" and few-shot examples all appended periods to last words (`"phone"→"phone."`). The LLM generalized this and started adding punctuation everywhere. Fix: removed punctuation-insertion instructions, removed period-appending from examples, removed trailing-period post-fix. Added locked rule #9.
- **Fixed: custom system prompt caused full rewrite + 2x latency** — `custom_sys or (default)` replaced the entire system prompt, losing "OUTPUT ONLY" constraints. Model added conversational filler → triggered retry. Fix: custom prompt is now appended via `"\n\nAdditional instructions:\n"` to both patch and full-text base prompts. Added locked rule #10.
- **Improved: patch max_tokens now dynamic** — Was hardcoded at 512. Now scales as `word_count * 20` (floor 128), capped to `context_size - estimated_input_tokens`. Prevents JSON truncation for long texts with many errors.
- **Improved: expanded post-processing in `_apply_patches()`** — Added deterministic contraction fixes (28 patterns: dont→don't, doesnt→doesn't, etc. with case preservation), and capitalize-after-sentence-ending-punctuation. These catch common misses without needing a second LLM pass.
- **Improved: multi-pass patch correction** — `_do_correction()` now loops patch correction up to 3 times, feeding corrected text back as input. Small LLMs miss subtle errors when focused on obvious ones; the second pass catches stragglers. Converges early when no changes found. Added locked rule #12.

### 2026-04-11
- **Fixed: corrections returning same input** — Gemma 4 entered thinking mode despite `"think":false`. Root cause: `--reasoning-budget 0` didn't prevent it. Fix: changed to `--reasoning off` server flag in `load_model()`.
- **Fixed: GPU not used** — CUDA runtime DLLs missing from subprocess PATH. Added CUDA search in `load_model()` to inject Ollama/CUDA Toolkit paths before server launch. Removed `--log-disable` so `server_log.txt` now shows CUDA backend loading.
- **Fixed: output box editable** — Added `setReadOnly(True)` to `corr_edit`.
- **Fixed: window shrinks after correction** — Removed `_fit_text_boxes()` and its `QTimer.singleShot(50, ...)` trigger. Was calling `setFixedHeight()` + `adjustSize()` causing abrupt resize.
- **Fixed: line breaks lost in output** — `_render_diff()` now uses `\x00NL\x00` placeholder around `\n` before word-splitting; renders as `<br>` in HTML. Preserves email/paragraph formatting.
- **Fixed: chat "loading model" message every time** — `_send_chat()` now checks `ac_same_as_chat` and routes to `ac_model` directly, skipping chat model load.
- **build.py rewritten** — Auto-detects llama-server from config; bundles CUDA DLLs; complete `RELEASE_CONFIG`; platform checks use `PLATFORM` variable (not `sys.platform`) to avoid Pylance unreachable-code hints.
- **README.md rewritten** — Updated to v4.0, removed LanguageTool/Java references, documents CUDA DLL requirement.

### 2026-04-08
- **Fixed: thinking models broke all corrections** — Added `"think": false` to all API payloads; added `_extract_content_from_response()` helper to detect empty-content thinking responses.
- Fixed `_extract_patches_from_response()` return type: `None` for parse failure (triggers fallback) vs `[]` for valid empty array.
- Removed empty assistant prefill from `correct_text()`.

### 2026-04-06
- Fixed strength slider: removed hardcoded system prompt in `correct_text_patch()` that overrode per-level instructions.
- Redesigned strength levels: 0=typos, 1=Samsung-like, 2=+grammar, 3=+structure, 4=rewrite.

### 2026-04-05
- Implemented patch-based autocorrect: `correct_text_patch()`, `_apply_patches()`, `_extract_patches_from_response()`.
- `_do_correction()` tries patch first, falls back to full-text.
- Merged all context files into AGENT_CONTEXT.md.

---

## Version History

| Version | Summary |
|---------|---------|
| **v4.0** | CUDA DLL injection, `--reasoning off`, read-only output boxes, line break preservation in diff, single-server chat routing, build.py auto-detect |
| **v2.10** | Patch-based autocorrect (JSON `{old,new}` patches), fallback to full-text |
| **v2.9** | LanguageTool removed; LLM-only; dual model (AC eager + chat lazy); correction strength 0–4 |
| **v2.8** | Bug fixes: nativeEvent segfault, scroll wheel spinboxes, HTML escaping, global exception hook |
| **v2.7** | Full rewrite: GECToR/ONNX removed, PyQt5→PyQt6, dark navy theme, streaming LLM chat |
| **v2.3.x** | GECToR DeBERTa-Large, ONNX idle timers |
| **v2.1.x** | T5-first architecture, ONNX CoEdit-Large |
| **v2.0** | Initial PyQt5 system-tray app, llama.cpp backend |
