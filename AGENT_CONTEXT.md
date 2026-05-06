# TextCorrector — Agent Context

**READ THIS ENTIRE FILE BEFORE TOUCHING ANY CODE.**

This is the authoritative design document for TextCorrector v3.2.
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

### 9. Sentence rewrite handles punctuation implicitly — no forced period
- `_SENTENCE_REWRITE_PROMPT` (line ~737) says "Fix typos, spelling, grammar, punctuation, and capitalization" — the model adds whichever mark fits the meaning because it rewrites the full sentence
- There is no explicit "add period / question mark / exclamation" instruction; the sentence-rewrite format lets the model see the full sentence and choose the correct terminal mark
- Do NOT add a rule "always append a period" — it causes the same bias that was removed in 2026-04-20
- Punctuation *correction* (fixing wrong marks) is fine; punctuation *insertion that changes meaning* (forcing `.` on a question) is not
- NOTE: The old `_PATCH_SYSTEM_PROMPT` and few-shot examples were deleted in 2026-04-24. The rules about punctuation are now enforced solely through `_SENTENCE_REWRITE_PROMPT`.

### 10. Custom system prompt is appended, never replaces
- When `system_prompt` config is set, it is appended to the base prompt as `"\n\nAdditional instructions:\n{custom_sys}"` — never replaces the base constraints
- The base prompt always includes `"OUTPUT ONLY the corrected text"` (full-text mode) or `"Output ONLY a JSON array"` (patch mode) — these must remain present regardless of custom instructions
- In patch mode (mode 1), custom instructions are also appended to the patch system prompt so they actually take effect
- Previously, `custom_sys or (default)` replaced the entire prompt, losing output-format constraints → model added conversational filler → triggered retry → 2x latency

### 11. Patch pipeline is a three-phase sentence-rewrite, not an indexed-JSON patch
- `correct_text_patch()` now runs: **Phase 0** deterministic dict pre-pass → **Phase 1** parallel sentence rewrite → **Phase 2** hallucination guard.
- **Phase 0** (`_dict_prepass`): ~150-entry `_COMMON_TYPOS_MAP` resolves obvious typos (teh→the, recieve→receive) case-preservingly with zero LLM cost. Fast path: if text ≤ 15 words AND dict made ≥1 fix AND result starts uppercase + ends with `.!?`, return immediately — no LLM call at all.
- **Phase 1**: `_chunk_text_by_sentences(pre_corrected, 40)` splits at sentence/paragraph boundaries into ~40-word units. `_rewrite_sentence_chunk()` issues one blocking `requests.post` per unit asking the model to rewrite the sentence between `<<<START>>>` / `<<<END>>>` markers. `ThreadPoolExecutor(max_workers=min(chunks, 4))` fires up to 4 in parallel.
- **Phase 2** (`_hallucination_ratio`): compares `difflib.SequenceMatcher` word-ratio between unit input and LLM output. If drift > `_HALLUCINATION_THRESHOLD_SMARTFIX` (0.6) or `_CONSERVATIVE` (0.4), reject the LLM output and keep the original sentence — per-unit granularity, not global.
- **Partial success is valid**: failed or rejected units keep their original text; other units proceed. The overall result is only marked "total failure" when dict_fixes == 0 AND no unit ever succeeded.
- **Server Parallelization:** The `llama-server` MUST be started with `--parallel 4` in the `cmd` list inside `load_model()`. With `--ctx-size 12800 --parallel 4`, per-slot context is ~3200 tokens (confirmed by `/props reports n_ctx=3328`). Sentence-sized units (~60 tokens in, ~60 tokens out) leave massive headroom so `finish_reason=length` is impossible in practice.
- Chunks are reassembled with their original inter-chunk whitespace/newlines preserved via the `sep` tuple element returned by `_chunk_text_by_sentences()`.
- `_apply_post_fixes(final, original=original_text)` runs after reassembly as the deterministic safety net (contractions, duplicate-word collapse, standalone-i→I, cap-after-period).

### 12. Patch mode is single-pass — no iterative refinement
- The multi-pass feedback loop (feed corrected text back up to 3 times) was REMOVED in 2026-04-24. It was the primary source of oscillation on short text ("curse curse", "lady lady" adjacent duplicates) — pass 2 on already-clean text pushed the model to "find something to change".
- Sentence-scale rewrite makes passes unnecessary: the model sees one full sentence and fixes all its errors at once, not fragmented patch indices that hide higher-order errors.
- The `_hallucination_ratio` guard in Phase 2 replaces the old `divergence_guard` / `suspicious` / `mostly_echo` heuristics. It's sharper because it measures *output drift* (what the user will see) rather than patch count.
- The method badge shows unit count when >1 (e.g. "Patch (Smart Fix, 5 units)"). The second element of `correct_text_patch()`'s return tuple is now `units` (sentence count sent to LLM), not `passes_run`. Fast path returns `units=0`.

### 13. Hotkey re-entrancy guard & notification throttle
- `TextCorrectorApp._hotkey_busy = threading.Lock()` with non-blocking `acquire(blocking=False)` — rapid repeats from holding the hotkey are dropped, not queued
- **The lock is shared between standard (F9) and silent (F10) correction flows** so both can't run simultaneously
- If the correction window is already visible, the hotkey raises/activates it instead of starting a new clipboard flow
- "No text selected" tray notifications are throttled to ≤ 1 per 3 s via `self._last_empty_notify_ts`
- Do NOT remove any of these guards — without them, holding the keys spawned overlapping threads, each firing its own notification in a feedback loop

### 14. Sampling params must flow through CLI AND every request payload
- `load_model()` passes `--temp`, `--top-k`, `--top-p`, `--min-p`, `--repeat-penalty`, `--frequency-penalty`, `--presence-penalty` (all from config)
- `_rewrite_sentence_chunk()` payload **hardcodes ALL sampling params** for full determinism: `temperature=0.0`, `top_k=1`, `top_p=0.95`, `min_p=0.05`, `repeat_penalty=1.0`, `frequency_penalty=0.0`, `presence_penalty=0.0`. None come from config — we want the same rewrite every time for identical input, regardless of user settings.
- `make_stream_worker()` payload includes all seven **from config values** (user-controlled sampling for chat + streaming fallback)
- If you add a new sampling setting, wire it through `load_model()` CLI and `make_stream_worker()`. Do NOT wire it into `_rewrite_sentence_chunk()` — that path must stay fully deterministic.

### 15. First-run setup dialog on blank model_path
- `TextCorrectorApp.__init__` schedules `_show_first_run()` via `QTimer.singleShot(800, ...)` when `model_path` is blank
- Dialog offers three paths: "Download recommended" (launches shipped `download_model.bat` / `.sh`), "Browse existing…", and "Skip"
- Do NOT remove — non-technical users who unzip a fresh release would otherwise see a silent tray icon and have no entry point

### 16. Release builds produce ONE artifact: the ZIP
- `build.py` deletes `dist/<release>/` and `build/` (PyInstaller scratch) after ZIP creation
- `--keep-folder` opts out for local debugging; `--no-zip` also preserves folders
- Reason: users saw `build/TextCorrector/TextCorrector.exe`, double-clicked it, and got "Failed to load Python DLL python313.dll" because that folder is PyInstaller's intermediate scratch (missing assets and python3*.dll). A single ZIP removes the footgun.

### 17. Tiny-model (<1B) safeguards — two layers remain
- **Load-time warning:** `ModelManager.model_warning` signal + tray popup when `_model_size_billions() < 1.0` (line ~1354). This is a heads-up only; correction still proceeds.
- **Output guards:** `_is_corrupt_output()` (rejects `[UNK_BYTE_...]`, control chars, ≥2 `▁` artifacts) and `_is_fewshot_echo()` (rejects verbatim few-shot example outputs) are called inside `_rewrite_sentence_chunk()`. On rejection the unit returns `None` and the caller keeps the original unit text.
- **Simplified prompt branch (`_is_tiny`) was removed in 2026-04-24** when the patch method was fully rewritten to sentence-rewrite. `_rewrite_sentence_chunk()` has no model-size check — it uses `_SENTENCE_REWRITE_PROMPT` / `_SENTENCE_REWRITE_PROMPT_CONSERVATIVE` unconditionally. Do NOT re-add a size branch without verifying it's still needed; the current prompts are already compact.
- **DO NOT re-add `response_format.json_schema` to `correct_text_patch()` payload** — grammar-constrained decoding in llama.cpp filters every sampled token, causing 3–10× slowdown. Removed 2026-04-17. The output guards are sufficient.
- Recommended model: Gemma 4 E2B Unsloth UD Q4_K_XL (bundled via `download_model.bat/.sh`, defined as `_RECOMMENDED_MODEL_URL` in `build.py`)

### 18. Context-window math uses config value
- The system uses the user's configured `context_size` (default 12800) for the `--ctx-size` CLI flag passed to llama-server.
- The `actual_ctx_size` from `/props` is logged for diagnostic purposes but ignored for chunking math because some GGUF metadata underreports capacity (e.g. Gemma 4 E2B reports 4096 but handles 12800 perfectly).
- Per-unit output budget in `_rewrite_sentence_chunk()`: `max_tokens = min(max(int(word_count * 1.6) + 32, 128), 512)` — scales with input words, floor 128, hard cap 512. The cap is 512 (not 2048) because sentence units are short and the model emits full sentences, not indexes.

### 20. Patch prompt must not modify numbers, dates, or intentional ALL CAPS
- The patch system prompt contains explicit rules: "NEVER change numbers, dates, URLs, code, or specific values" and "NEVER alter intentional styling: preserve ALL CAPS words, initialisms (NASA, USA), and Title Case exactly as the user wrote them"
- Root cause: without these rules the model interpreted `ALL CAPS` as incorrect capitalization and lowercased it, and treated a value like `0.0735` as needing to "match" context clues (user writing "3 decimals" → model rounds to `0.074`)
- Only fix capitalization that is clearly a typing mistake (lowercase `i` pronoun, lowercase first word of sentence)
- Do NOT remove these rules — they were added after confirmed user-visible bugs

### 21. Hotkey uses `keyboard` library defaults + Win32 `SendInput` for chord injection
- Registration: `keyboard.add_hotkey(hk, self._hotkey_signal.emit)` — no extra args.
- Default hotkey is **`f9`** because the chord is **not suppressed** and any typeable trigger key (space, letter, digit) would replace the user's selection before the worker can copy it. F9 is non-typeable in every standard text field on Windows.
- The callback runs on the `keyboard` library's background thread and does **no work** — it only emits `_hotkey_signal`, a `pyqtSignal()` whose queued connection marshals execution to the Qt main thread (`_hotkey_fired`). The clipboard dance runs in `_hotkey_worker` (a daemon thread) so the main thread stays responsive.
- Re-entrancy is guarded by `self._hotkey_busy = threading.Lock()` with non-blocking `acquire(blocking=False)`. Held-key auto-repeat is naturally absorbed by the lock; no separate debounce window is needed.
- **Ctrl+C / Ctrl+V injection uses `_send_ctrl_chord(VK_C / VK_V)`** — a direct `ctypes` `SendInput` call that submits the four key events (Ctrl down, key down, key up, Ctrl up) atomically in a single OS call. `keyboard.send('ctrl+c')` is **not** used on Windows because its synthetic Ctrl-up was occasionally dropped under WH_KEYBOARD_LL re-entry, leaving Ctrl held system-wide. `keyboard.send` remains the non-Windows fallback inside the helper.
- The settings hotkey-recorder accepts standalone "leak-safe" trigger keys (F1–F12, Pause, Insert, Scroll Lock, Print Screen, Menu) — see `_STANDALONE_OK` near `_QT_KEYS`. Letter / digit / symbol keys still require a Ctrl/Shift/Alt modifier (otherwise the leaked keystroke would replace the user's selection).
- **DEAD ENDS — confirmed in repeated sessions. Do NOT reintroduce any of these:**
  - `suppress=True`: the `keyboard` library's `WH_KEYBOARD_LL` chord matcher arms itself by suppressing every component key (Ctrl, Shift, etc.) the moment any of them is pressed. While armed, every Ctrl press anywhere on the system is consumed. Symptom: "Ctrl is hollowed out while the app is open."
  - `trigger_on_release=True`: requires every key in the chord to be released cleanly together. Quick taps where one key releases marginally before another never resolve. Symptom: "short presses don't trigger."
  - Win32 `RegisterHotKey` via ctypes: chord components leak into focused apps before the message-pump callback runs. Worse than the `keyboard` library.
  - `keyboard.hook(suppress=True)` with selective return-False: blocks the entire keyboard at the hook level regardless of return value.
  - Manual `keyboard.release()` calls for modifier keys: desyncs the OS modifier-state machine; phantom-Ctrl returns after any restart.
  - `pynput.keyboard.Controller` for Ctrl+C/Ctrl+V injection alongside the `keyboard` hook: dual-library state tracking lets bugs slip between the two libraries.
  - `keyboard.send('ctrl+c')` on Windows: occasionally drops the synthetic Ctrl-up. Use `_send_ctrl_chord` instead.

### 29. Clipboard read/write goes through `_clipboard_read_text` / `_clipboard_write_text` on Windows
- These wrap Win32 `OpenClipboard` + `GetClipboardData(CF_UNICODETEXT)` / `SetClipboardData(CF_UNICODETEXT)` directly. They decode/encode as UTF-16-LE, so emoji and other astral-plane characters round-trip via surrogate pairs without truncation.
- `pyperclip` is the non-Windows fallback inside the helpers; on Windows it is bypassed entirely. The previous direct `pyperclip.paste()` / `pyperclip.copy()` calls in `_safe_paste` / `_safe_copy` failed silently on selections containing math symbols (Σ, Ω, π, etc.) and emoji — confirmed user-reported bug 2026-04-29.
- `_open_clipboard_retry()` retries `OpenClipboard` up to 10 times at 10ms intervals because the clipboard can be briefly held by another process; `CloseClipboard` is in a `finally` so we never leak the lock.

### 30. Silent Correction (F10 hotkey) — background correct-and-paste
- `_silent_hotkey_fired()` acquires the **same** `_hotkey_busy` lock (shared with standard hotkey), starts `_silent_hotkey_worker` on a daemon thread.
- Worker flow: capture selection via Ctrl+C → correct with `correct_text_patch()` → paste result via Ctrl+V → restore original clipboard. No CorrectionWindow popup.
- **Strength is independent**: `silent_strength` config key ("conservative" or "smart_fix") controls correction intensity separately from the main `streaming_strength`.
- OSD notification (`SilentCorrectionOSD`) provides user feedback with three visual states: `loading` (blue, stays visible), `success` (green, auto-dismiss), `warning` (amber, auto-dismiss). OSD is created on the main thread via `_silent_osd_signal` (queued connection).
- **Dead-end: `QTimer.singleShot()` from background thread.** The silent worker runs on a daemon thread. Calling `QTimer.singleShot(500, ...)` from a non-Qt thread violates Qt thread safety and crashes/freezes on Windows. Use `time.sleep(0.5)` + direct call instead. Confirmed crash 2026-04-30.

### 31. HotkeyEdit manual-edit mode
- The ✏ pencil button next to each hotkey field calls `enable_manual_edit()` which sets `setReadOnly(False)` and lets the user type a hotkey string directly (e.g. "ctrl+f10").
- Pressing Enter commits the typed value via `_commit_manual_edit()`. Pressing Escape cancels and reverts to the previous value.
- `shortcut_changed` signal fires on successful commit so callers can re-register.
- `_manual_editing` flag prevents the keyPressEvent recorder from interfering with normal typing.

### 33. Update checks — external updater helper, not batch self-update
- `AppUpdateChecker(QThread)` (replaces deleted `UpdateChecker`) polls `https://api.github.com/repos/AmrZriek/TextCorrector/releases/latest` on startup (5 s delay via `QTimer.singleShot`).
- Semantic version comparison: `APP_VERSION = "3.1.1"` (module-level constant) vs GitHub `tag_name`. Comparison uses tuple of ints: `(3, 1, 1)` — handles `v3.2.0`, `Release_v3.1.0`, `3.1` tag formats.
- When remote > local and a ZIP asset exists: tray menu item changes to `"TextCorrector vX.Y.Z available - install update"`. The action is re-wired to `_start_app_update(asset_url, tag)` via `triggered.disconnect() + connect()`.
- `_start_app_update()` only asks for confirmation, starts the external updater helper, and exits the GUI. It must not contain file-copy rules for models/llama/config, generated batch files, or shell-script update logic.
- **Updater architecture:** `build.py` compiles `update.py` as a separate one-file `TextCorrectorUpdater.exe` and includes it next to `TextCorrector.exe`. The GUI copies that helper to `%TEMP%\TextCorrectorUpdate\TextCorrectorUpdater.exe`, starts it with `--app --install-dir <SCRIPT_DIR> --wait-pid <gui_pid> --restart`, then quits. Running the temp copy means the installed updater executable is not locked while the release files are replaced.
- `update.py` owns the replacement logic: waits for the GUI PID to exit, fetches the latest GitHub release, downloads the matching OS ZIP to `%TEMP%`, safely extracts after path-traversal validation, atomically replaces files with `os.replace()`, preserves `config.json`, logs, and `.gguf` / `.onnx` model files, then restarts TextCorrector. Release-owned binaries, including the bundled llama-server folder, update together.
- **Do not reintroduce the old high-risk updater mechanics.** The packaged Windows GUI/updater must not write `_apply_update.bat`, create `_update_exclude.txt`, run `xcopy`, use `shell=True`, use `DETACHED_PROCESS`, or update `llama-server` independently.
- **NEVER update `llama-server` independently.** `llama.cpp` releases multiple times per day and frequently breaks CLI flags (`--reasoning-budget` → `--reasoning off`, `--parallel`, `--no-warmup`). Each TextCorrector release bundles a specific known-good build. Updating llama.cpp outside of a TextCorrector release will cause silent server startup failures or broken corrections with no error message.
- `update.py --app` remains the same updater logic for source/dev installs; the compiled release runs it through `TextCorrectorUpdater.exe`.
- `APP_VERSION` is the single source of truth. `build.py`'s `_get_version()` regex reads it: `r'APP_VERSION\s*=\s*[\'"]([0-9\.]+)[\'"]'`. `build.py` also writes a `VERSION` file into the release folder for fast external version reads.

### 32. DEFAULT_TEMPLATES is a module-level constant used for first-run
- `DEFAULT_TEMPLATES` (list of `{"name": str, "prompt": str}` dicts) is defined once at module scope. It is ONLY used to populate `custom_templates` in `config.json` if it's empty. Afterwards, the app reads and writes templates purely from the config, allowing the user to edit and delete all templates, including defaults.
- Do NOT define template lists inside class methods — it was duplicated before 2026-04-30.

### 22. Pass termination — DELETED (superseded by single-pass sentence rewrite, rule #12)
- The old multi-pass loop and its termination heuristics were removed 2026-04-24 along with the indexed-JSON format. Do NOT re-add any form of "feed corrected text back for another pass" logic — it's the root cause of short-text oscillation.

### 23. Chat first turn embeds user text inline
- `_send_chat()` when `self.chat_history` is empty builds a single user message: `f"Task: {msg}\n\nText:\n{self.corrected}"`
- Do NOT revert to the old 3-message prefill (system + fake-user "Here is the text" + fake-assistant "Understood") — that caused Gemma 4 / Qwen to reply "Please provide the text" because the conversation history claimed the text was already acknowledged

### 24. Reset button: cancel latch is a one-way flag per window
- `CorrectionWindow._reset()` sets `self._correction_cancelled = True` and `self._cancel_event.set()`. It MUST NOT reset these back to `False` / fresh `Event()` inside `_reset()`.
- Reason: any `_rewrite_sentence_chunk()` request's `requests.post(timeout=60)` call is blocking and can return up to 60s after the user presses Reset. If the flag were cleared immediately, the late response would slip through `_do_correction`'s post-call gate, triggering `_start_streaming_correction()` and producing the user-reported bug "I was typing in chat and it suddenly started streaming corrected text at me."
- Lifecycle: the flag is a latch for the window's lifetime. Retry = close + reopen popup (new window instance, new flag).
- `_do_correction()` MUST check `self._correction_cancelled` after `correct_text_patch()` returns, BEFORE invoking `_start_streaming_correction()` or emitting `_correction_ready`. `_start_streaming_correction()` itself has the same guard at its top as belt-and-braces.
- `correct_text_patch()` checks `cancel_event` between dispatched future completions so a mid-correction Reset aborts before firing additional units.

### 25. Patch unit size: 40 words max (sentence-scale)
- `_chunk_text_by_sentences(pre_corrected, 40)` in `correct_text_patch()`.
- Reason: llama-server is launched with `--parallel 4 --ctx-size 12800`, giving ~3200 tokens per slot (not 12800 — the ctx is divided across slots; confirmed by `/props reports n_ctx=3328`). Sentence units of ~40 words ≈ 60 input tokens + 60 output tokens fit easily with multiple kilotokens to spare.
- `max_tokens = min(max(int(word_count * 1.6) + 32, 128), 512)` — scales with input, caps at 512 to prevent runaway output if the model loses its way.
- **Dead-end:** Raising the cap to pre-2026-04-24 levels (180+ words per chunk) re-introduces `finish_reason=length` on dense-edit inputs because the old indexed-JSON format is gone and the model now emits full sentences (output scales with input, not with edit count).

### 26. Corrupt/echo output → keep original for that unit
- `_rewrite_sentence_chunk()` returns `None` when `_is_corrupt_output()`, `_is_fewshot_echo()`, or marker-extraction fails. The orchestrator then keeps the unit's ORIGINAL text and continues — partial success, not total failure.
- Do NOT convert unit-level failure into a global streaming fallback. That was the pre-2026-04-24 behavior and produced 24-second stalls on long inputs where a single chunk hit trouble.
- Only when dict_fixes == 0 AND zero units succeeded does `correct_text_patch()` return `None` (streaming fallback for the whole text).

### 27. Hallucination guard: per-unit edit-distance gate
- After a unit's LLM output returns, `_hallucination_ratio(orig_unit, corr_unit)` computes `1 - difflib.SequenceMatcher(None, orig_words, corr_words).ratio()`.
- If ratio > `_HALLUCINATION_THRESHOLD_SMARTFIX` (0.6) or `_HALLUCINATION_THRESHOLD_CONSERVATIVE` (0.4), the LLM output is REJECTED and the unit's original text is kept (logged as `hallucination rejected`).
- This replaces the deleted `divergence_guard`, `suspicious_rewrite`, `mostly_echo`, and consecutive-duplicate patch filters. All four measured the wrong thing (patch count, not output drift). Edit-distance measures what the user actually sees change, at sentence scope.
- `_DUP_WORD_PATTERN` in `_apply_post_fixes` STAYS — it's the final safety net for any surviving `\b(\w+)\s+\1\b` duplicates, preserving intentional "had had" / "that that is" by checking the original.

### 28. Dict pre-pass thresholds
- Fast-path skip-LLM condition: `dict_fixes > 0 AND total_words ≤ 15 AND candidate[0].isupper() AND candidate.rstrip()[-1] in ".!?"`. All four must hold.
- Reason: the dict's case-preservation is perfect for flat word-level typos, but it does NOT fix capitalization errors, missing sentence-ending punctuation, or grammar. The structural checks ensure we only skip the LLM when the dict-corrected text is already well-formed at the sentence level. Without them, "i beleive it" would skip the LLM and return "i believe it" — still wrong.
- Longer texts (>15 words) ALWAYS go through the LLM pass because (a) the dict alone won't catch grammar/cap errors scattered through the text, and (b) parallel sentence rewrite is fast enough that skipping is not worth the complexity risk.
- `_COMMON_TYPOS_MAP` is a frozen dict of ~150 entries kept in `text_corrector.py` — no external dep. Add entries conservatively: only unambiguous single-word typos whose correction is context-independent. Ambiguous ones ("wa" → "was"? "what"? "a"?) should NOT be in this dict — the LLM handles context.

---

## Instincts & Dead Ends (For AI Agents)

- **Dead-end:** Reintroducing indexed-JSON patches (`[{"i":N,"new":"..."}]`) or any structured-enumeration output format. It fails on 2B-class models under load — output scales with input, hits `finish_reason=length`, produces duplicate-word patches. Stick with sentence rewrite.
- **Dead-end:** Adding a multi-pass feedback loop (feed corrected text back in for another round). It oscillates on already-clean short text (pass-2 "finds" things to change that aren't wrong). Single pass only.
- **Dead-end:** Refactoring `_rewrite_sentence_chunk` return signatures without updating `correct_text_patch`'s future-collection logic. Keep return as `str | None`.
- **Dead-end:** Using `keyboard.release()` for hotkeys. It breaks Windows modifier state.
- **Dead-end:** Sequential patching. Latency for 1000+ words exceeds 60s. Use `ThreadPoolExecutor(max_workers=4)` + `--parallel 4` server flag.
- **Dead-end:** Global edit-distance gates spanning the whole text. Apply `_hallucination_ratio` PER UNIT — short single-sentence units legitimately have high edit ratios (1 typo in 3-word sentence = 33%) and global gates falsely reject them.
- **Dead-end:** Updating `llama-server` independently of the app. `llama.cpp` builds break CLI flag compatibility multiple times per week. Updating only the backend while keeping the old app code causes silent startup failures (wrong flags rejected → server refuses to start) or broken corrections. Always update the full TextCorrector release together. See Rule #33.
- **Dead-end:** Reintroducing a GUI-embedded updater that writes batch files, runs `xcopy`, uses `shell=True`, or relaunches with `DETACHED_PROCESS`. That pattern can look like malware to Defender. Use the external one-file `TextCorrectorUpdater.exe` helper only.
- **Instinct:** If corrections are slow on long text, verify the server log for `--parallel 4` and ensure `max_workers=4` is actually what the ThreadPoolExecutor is using. More workers just queues requests without speedup.
- **Instinct:** If a sentence comes back looking "mostly original but one word changed" when it should be more, check whether the hallucination guard rejected a legitimate rewrite. Tune `_HALLUCINATION_THRESHOLD_SMARTFIX` up (toward 0.8) for heavy-edit expected inputs; DO NOT disable the guard.


---

## Architecture

```
User selects text → presses hotkey
        │
        ├── F9 (standard): _hotkey_fired → _hotkey_worker
        │     │
        │     ▼
        │   CorrectionWindow._do_correction()
        │   ┌──────────────────────────────────────────────────────────┐
        │   │ correction_method == "patch" (default):                  │
        │   │   correct_text_patch() — sentence rewrite, up to 4      │
        │   │   parallel units; dict pre-pass → LLM → halluc. guard   │
        │   │   On total failure → falls back to streaming smart_fix   │
        │   │                                                          │
        │   │ correction_method == "stream":                           │
        │   │   _start_streaming_correction() — streams full corrected │
        │   │   text token-by-token; strength=conservative|smart_fix   │
        │   └──────────────────────────────────────────────────────────┘
        │         │
        │         ▼
        │   CorrectionWindow shows diff (changed words highlighted)
        │   User presses Ctrl+Enter → paste back
        │
        └── F10 (silent): _silent_hotkey_fired → _silent_hotkey_worker
              │
              ▼
            correct_text_patch() with silent_strength
              │
              ▼
            Auto-paste result + OSD notification
            (no CorrectionWindow popup)

        │  (only if user types in "Ask AI" chat box)
        ▼
  chat_model = ac_model (same server, when ac_same_as_chat=True)
  StreamWorker streams SSE tokens live into chat display
```

---

## File Structure

```
TextCorrector/
├── text_corrector.py      ← Single Python file. ALL app code lives here (~4200 lines)
├── build.py               ← Nuitka release builder (Windows self-contained EXE + ZIP)
├── update.py              ← Standalone app updater (python update.py --app)
├── release.ps1            ← Release automation (git tag + gh release create)
├── requirements.txt       ← Python deps: PyQt6, keyboard, pyperclip, requests
├── config.json            ← User settings (auto-created on first run, gitignored)
├── README.md              ← Public GitHub documentation
├── AGENT_CONTEXT.md       ← THIS FILE — AI agent context (gitignored, local only)
├── llama-<build>-*/       ← llama-server binary + DLLs (gitignored, user-provided)
├── venv/                  ← Python venv (gitignored)
├── logo.png / logo.ico    ← App icons
└── tests/                 ← pytest + pytest-qt test suite (71 tests)
    ├── conftest.py
    ├── test_chunking.py / test_chunking_edge_cases.py
    ├── test_clipboard.py
    ├── test_hotkey_edit.py / test_manual_edit.py
    ├── test_input_synth.py
    ├── test_silent_wiring.py
    ├── test_constants_and_osd.py
    ├── test_wiring.py
    └── test_update.py     ← NEW: 29 tests covering APP_VERSION, version parser, wiring, exclusions
```

No model files (`*.gguf`), no ONNX, no GECToR, no LanguageTool JARs, no PyTorch in this project.

---

## Key Classes (text_corrector.py)

### Helper Functions
- `strip_thinking_tokens(text)` — removes `<think>`, `<thinking>`, `<reasoning>` tags and content
- `strip_meta_commentary(text)` — strips LLM preambles ("Here is the corrected text:", code fences)
- `contains_meta_commentary(text)` — detects if output contains conversational filler
- `_extract_content_from_response(resp)` — extracts `(content, finish_reason)` from API response; detects thinking models where `content` is empty and `reasoning_content` has output
- `_tokenize_with_ws(text)` — splits text into `(leading_ws, [(word, trailing_ws), ...])` for lossless reassembly (still used by word counting helpers)
- `_dict_prepass(text)` — Phase 0 deterministic typo pre-pass; applies `_COMMON_TYPOS_MAP` with case preservation; returns `(fixed_text, n_fixes)`
- `_hallucination_ratio(orig, corr)` — Phase 2 edit-distance guard; returns `1 - difflib.SequenceMatcher(None, orig.split(), corr.split()).ratio()`; per-unit, not global
- `_extract_rewritten_sentence(raw)` — pulls sentence content from `<<<START>>>…<<<END>>>` markers; returns `None` on missing/ambiguous output so caller keeps the original unit
- `_apply_post_fixes(text, original)` — deterministic safety net: standalone `i`→`I`, first-letter cap, contractions, cap after `.?!`, restore trailing punct from original, collapse word duplicates (preserving "had had")
- `_chunk_text_by_sentences(text, max_words)` — splits text at sentence/paragraph boundaries into chunks of ≤ max_words; returns `(chunk_text, separator)` tuples for lossless reassembly; called with `max_words=40` by `correct_text_patch()` to produce sentence-scale units
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
  - `correct_text_patch(text, custom_sys, strength, cancel_event)` — three-phase sentence rewrite (dict pre-pass → parallel LLM rewrite → hallucination guard). Returns `(text, units)` where `units` is the count of sentence units sent to the LLM (0 on fast-path). Returns `(None, n)` for total failure → caller falls back to streaming
  - `_rewrite_sentence_chunk(chunk_text, custom_sys, unit_idx, total, strength)` — single sentence rewrite via `<<<START>>>…<<<END>>>` markers; `max_tokens = min(max(word_count*1.6+32, 128), 512)`; returns corrected string or `None`
  - `make_stream_worker(messages)` — returns `StreamWorker` for SSE streaming (chat and streaming correction)
  - `check_idle()` — QTimer every 60 s; unloads if idle > timeout (skip if `keep_model_loaded`)
- All API payloads send `"think": False`
- Server launched with `--reasoning off` and `--no-warmup`

### `CorrectionWindow(QWidget)`
- Main popup; appears near cursor on hotkey press
- `_do_correction()` — dispatches to `correct_text_patch()` or `_start_streaming_correction()` based on `correction_method` config; patch malformed output auto-falls back to streaming
- `_start_streaming_correction(text, custom_sys, strength)` — minimal system prompt + user message; no few-shot examples; streams into `corr_edit` live via `StreamWorker`
- `_send_chat()` — routes to `ac_model` when `ac_same_as_chat=True` and AC is loaded
- `_do_stream()` — picks correct ModelManager backend, creates StreamWorker
- `_render_diff(text)` — word-level diff with `\x00NL\x00` newline placeholder → `<br>` in HTML

### `TextCorrectorApp(QApplication)`
- System tray, hotkey registration, window lifecycle
- `ac_model` loads at boot (eager)
- `chat_model` loads on first chat use (lazy), reused if `ac_same_as_chat=True`
- **Silent correction**: `_silent_hotkey_fired()` / `_silent_hotkey_worker()` run the patch pipeline without a popup; `SilentCorrectionOSD` provides visual feedback
- **Signals**: `_hotkey_signal`, `_silent_hotkey_signal`, `_silent_osd_signal(str, str)`, `_notify(str, str)`

### `SilentCorrectionOSD(QWidget)`
- Frameless, always-on-top, pill-shaped notification at bottom-center of screen
- Three states: `loading` (blue ⟳), `success` (green ✓), `warning` (amber !)
- `show_animated(auto_dismiss)`: fade-in via `QPropertyAnimation`; loading stays visible, others auto-dismiss after 2.5 s

### `HotkeyEdit(QLineEdit)`
- Custom hotkey recorder supporting both keypress recording and manual text entry
- `enable_manual_edit()` / `_commit_manual_edit()` toggle between read-only recorder mode and editable text mode
- `shortcut_changed` signal emits the new hotkey string on commit

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
| `silent_hotkey` | `"f10"` | Silent correction hotkey (correct + auto-paste, no popup) |
| `system_prompt` | `""` | Override LLM system prompt (blank = use built-in) |
| `correction_method` | `"patch"` | `"patch"` = sentence rewrite; `"stream"` = full text streamed token-by-token |
| `streaming_strength` | `"smart_fix"` | `"conservative"` = typos only; `"smart_fix"` = full grammar/punct/caps (standard correction) |
| `silent_strength` | `"smart_fix"` | Same as above but for silent correction (F10); independent from `streaming_strength` |
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
- All test scripts (`test_*.py`, `check_*.py`, `inspect_*.py`) — **except the new `tests/` directory**, which was re-added in 2026-04-29 as a proper pytest suite with 42 tests
- `build.ps1`, `download_models.bat`, `update_llama_cpp.bat`
- Indexed-word JSON patch format (`[{"i":N,"new":"..."}]`) — replaced 2026-04-24 by sentence rewrite. The format exceeded what 2B-class models could reliably hold; output scaled with input not edit count; pass-2 oscillation produced duplicate words. Do not reintroduce indexed patches, few-shot JSON examples, `_parse_indexed_patches`, or any multi-pass feedback loop.
- `response_format.json_schema` grammar-constrained decoding — 3–10× slowdown from per-token schema filtering (removed 2026-04-17). The new sentence-rewrite format doesn't need it.
- `_patch_correct_chunk()` method signature and `_apply_indexed_patches()` helper — replaced by `_rewrite_sentence_chunk()` + plain string diff.

---

## Session History

### 2026-05-03 (latest)
- **Auto-installer reintroduced with lower-risk mechanics.** The tray update action now installs through `_start_app_update()` instead of opening the GitHub release page. The GUI confirms with the user, starts an external updater helper, then exits; it does not embed config/model/llama copy rules.
- **Packaged updater helper added.** `build.py` now compiles `update.py` as one-file `TextCorrectorUpdater.exe` and includes it in the release ZIP. The GUI runs a temp copy of this helper so the installed updater can also be replaced.
- **Updater safety hardening.** `update.py` now supports `--install-dir`, `--wait-pid`, and `--restart`; reads `VERSION` in built installs; waits for the GUI process to exit; downloads/extracts in `%TEMP%`; validates ZIP paths before extraction; atomically replaces files via `os.replace()`; preserves `config.json`, logs, and `.gguf` / `.onnx`; updates release-owned binaries together; and restarts TextCorrector.
- **Context correction.** Rule #33 no longer bans all automatic updates. It bans the old high-risk mechanics: generated batch files, `xcopy`, `shell=True`, `DETACHED_PROCESS`, and independent llama-server updates.

### 2026-05-02
- **GitHub release update checker (replaces llama.cpp update checker).** Removed `UpdateChecker` (checked `ggml-org/llama.cpp` releases), `_get_local_build_number()`, and `_check_llama_update()`. Replaced with `AppUpdateChecker(QThread)` that polls the `AmrZriek/TextCorrector` releases endpoint. This was later reconnected to the safer external updater helper on 2026-05-03.
- **`APP_VERSION = "3.1.1"` module-level constant** added as the single source of truth for version comparison. `build.py`'s `_get_version()` updated to read this constant instead of scanning the docstring.
- **Old compiled self-updater removed before deployment.** `_perform_update()` was replaced because it wrote batch files and used `xcopy` / `DETACHED_PROCESS`. The replacement was later changed from release-page handoff to the safer external `TextCorrectorUpdater.exe` helper on 2026-05-03.
- **Build hardening for Windows AV locks.** `build.py` now retries `shutil.rmtree()` and the Nuitka output folder promotion. If `Path.rename()` from `build/text_corrector.dist` to `dist/<release>` is blocked by Defender/indexing, it falls back to `copytree()` and later cleanup. Also adds Windows EXE resource metadata (`company`, `product`, `file/product version`, description).
- **`update.py` completely rewritten** as a standalone app updater (`python update.py --app`). Old `--llama` flag and llama binary download logic removed. Preserves `--all` for dev pip upgrades.
- **`build.py`**: `_get_version()` updated to match `APP_VERSION` constant; adds `VERSION` file to release folder.
- **`release.ps1`**: version bumped to `3.2.0`, release notes updated.
- **README updated to v3.2**: F9/F10 hotkey table, new "Automatic updates" section with llama.cpp warning, troubleshooting entry for "broken after manual llama update".
- **Test suite expanded: 42 → 72 tests.** Added/updated `test_update.py`: APP_VERSION validation, semver parser correctness, API URL wiring, release-page handoff, ban on compiled self-update artifacts, update.py flag coverage, build.py version reading.
- **Rule #33 added**: auto-updater design constraints and the llama.cpp independent-update dead-end.

### 2026-04-30 (previous)
- **Silent Correction (F10).** Added `_silent_hotkey_fired()` / `_silent_hotkey_worker()` / `SilentCorrectionOSD` for background correct-and-paste without popup. Shares `_hotkey_busy` lock with standard flow. Added `silent_hotkey` and `silent_strength` config keys. Added `_silent_osd_signal(str, str)` with three-state OSD (loading/success/warning). See Rule #30.
- **Fixed: `QTimer.singleShot()` from background thread crash.** The silent worker ran `QTimer.singleShot(500, ...)` from a daemon thread, violating Qt thread safety and causing freeze/crash on Windows. Replaced with `time.sleep(0.5)` + direct call. See Rule #30.
- **HotkeyEdit manual-edit mode.** Added ✏ pencil button with `enable_manual_edit()` / `_commit_manual_edit()` for typing hotkey strings directly. Enter commits, Escape cancels. See Rule #31.
- **CORE_TEMPLATES promoted to module-level constant.** Previously duplicated inside `CorrectionWindow._refresh_templates()`. Now shared at module scope. See Rule #32.
- **Fixed: `_chunk_text_by_sentences()` newline splitting.** Removed an aggressive early-exit optimization that bypassed regex splitting for short texts, preventing newline-based segmentation. Added `force_split` on newline separators so paragraph boundaries always create chunk boundaries regardless of word budget.
- **Fixed: Settings UI overflow.** Applied `QSizePolicy(Expanding, Fixed)` and zero-margin `QHBoxLayout` to compound browse-row widgets to prevent infinite horizontal expansion.
- **Bonus fixes:** removed unreachable `return True` in `_is_fewshot_echo()`, fixed corrupted startup log string, replaced `pyperclip.copy()` with `_clipboard_write_text()` in `CorrectionWindow._copy()`.
- **Test suite expanded: 16 → 42 tests.** Added: `test_chunking_edge_cases.py` (8 edge cases), `test_silent_wiring.py` (6 wiring assertions including QTimer ban), `test_manual_edit.py` (5 manual edit mode tests), `test_constants_and_osd.py` (7 constant/OSD instantiation tests).

### 2026-04-29
- **Hotkey subsystem rewritten to global standard pattern.** Removed `suppress=True` (root cause of system-wide Ctrl block) and `trigger_on_release=True` (root cause of missed short presses). Removed `pynput.keyboard.Controller` entirely; `keyboard` library is now the only hotkey library. Hotkey callback emits `pyqtSignal` (`_hotkey_signal`) whose queued connection marshals to Qt main thread. Removed the QTimer + queue + suppress_until debounce — the re-entrancy `threading.Lock` already prevents overlap. Default hotkey changed to `f9` because without `suppress=True` a typeable trigger key would replace the user's selection. See Rule #21.
- **Win32 `SendInput` for Ctrl+C / Ctrl+V (`_send_ctrl_chord`).** `keyboard.send('ctrl+c')` was dropping the synthetic Ctrl-up under WH_KEYBOARD_LL re-entry, leaving Ctrl held system-wide. Direct `ctypes` `SendInput` submits the four key events atomically. `keyboard.send` remains the non-Windows fallback. See Rule #21.
- **Win32 `CF_UNICODETEXT` clipboard read/write (`_clipboard_read_text` / `_clipboard_write_text`).** `pyperclip` was failing silently on selections containing math symbols (Σ, Ω, π, …) and emoji. Direct `OpenClipboard` + `GetClipboardData(CF_UNICODETEXT)` round-trip via UTF-16-LE handles every code point including surrogate pairs. `pyperclip` remains the non-Windows fallback. See Rule #29.
- **`HotkeyEdit` now accepts standalone "leak-safe" trigger keys** — F1-F12, Pause, Insert, Scroll Lock, Print Screen, Menu — via the new `_STANDALONE_OK` whitelist. Letter / digit / symbol keys still require a Ctrl/Shift/Alt modifier. Previously the recorder unconditionally rejected modifier-less keys, blocking the new F9 default.
- **Tests added under `tests/`** (pytest + pytest-qt): `test_hotkey_edit.py`, `test_input_synth.py`, `test_clipboard.py`, `test_wiring.py`. 14 tests, all green. The clipboard tests round-trip Σ/Ω/π and 😀/🚀/✨ on the real Windows clipboard.

### 2026-04-26
- **UNRESOLVED: Hotkey still broken after trigger_on_release=True fix.** The 2026-04-25 fix (`trigger_on_release=True`) resolved the phantom-Ctrl-held symptom but left two new problems: (1) short presses of the hotkey chord do not trigger at all, (2) Ctrl is still blocked system-wide while the app runs. Both symptoms persist. The user confirmed they cannot trigger the shortcut and Ctrl is "completely hollowed out" while app is open.
- **Dead-end: Win32 `RegisterHotKey` via ctypes** — Attempted to bypass the `keyboard` library entirely using Windows' native `RegisterHotKey` API (ctypes + dedicated `GetMessage` loop on a worker thread). Result: broke more things — keys from the chord leaked into other apps (browser F12 / DevTools opened on chord press), other keys required double-press, behavior was worse than baseline. **Do NOT attempt RegisterHotKey again.** The `keyboard` library's hook is the only sane cross-platform path; the problem is which API within it is used.
- **Dead-end: `keyboard.hook(on_key, suppress=True)` with selective suppression** — Attempted to use `keyboard.hook()` instead of `add_hotkey()`, with the callback returning `False` only for the trigger key (Space) when modifiers were held. Theory was that individual modifier keys would never be suppressed. Result: entire keyboard stopped working (every key became unresponsive). Root cause unknown — likely `keyboard.hook(suppress=True)` on Windows intercepts and can block all events at the hook level regardless of return value, or there is a conflict between having both `keyboard.hook` and `pynput.Controller` active simultaneously. **Do NOT attempt `keyboard.hook(suppress=True)`.**
- **Current state:** Reverted to commit `75b551b` — `keyboard.add_hotkey(hk, on_hotkey, suppress=True, trigger_on_release=True)`. Both original bugs (no trigger on short press, Ctrl blocked) remain open. All attempted fixes in this session made things worse.
- **What has NOT been tried yet:** The root symptom (Ctrl blocked) is caused by `suppress=True` in `add_hotkey` — the library's chord matcher eagerly suppresses ALL component keys (Ctrl, Shift, Space) before the chord is confirmed. `suppress=False` was previously rejected because Space leaks into the focused app. A potential middle path — `suppress=False` combined with a post-trigger `keyboard.block_key('space')` or absorbing the leaked space from clipboard — has not been evaluated.

### 2026-04-25
- **Fixed: Ctrl key permanently held down system-wide while app is running** — Root cause: `trigger_on_release=False` in `keyboard.add_hotkey(suppress=True)` caused the library's `WH_KEYBOARD_LL` low-level hook to hold its modifier-state machine in "match in progress" while the user still physically held Ctrl+Shift after the hotkey fired on PRESS. Under Windows timing quirks, the hook's replay logic leaked a phantom "Ctrl pressed" event into every other app's input queue for the app's lifetime. Closing the app uninstalled the hook and immediately resolved the symptom; rebooting reset OS input state which caused artificial-release workarounds (tried by prior agents) to regress. Fix: changed `trigger_on_release=False` → `trigger_on_release=True` at `text_corrector.py:3156`. Callback now fires after user releases all keys so the state machine is clean. Cost: ~100ms latency on hotkey press (acceptable trade).
- **Audited all 28 locked rules against current code.** Corrections: Rule 9 updated to reference `_SENTENCE_REWRITE_PROMPT` (old `_PATCH_SYSTEM_PROMPT` was deleted 2026-04-24). Rule 14 clarified that `_rewrite_sentence_chunk` hardcodes ALL sampling params for determinism (not just temp/top_k). Rule 17 updated to remove the `_is_tiny` simplified prompt branch which was deleted in the 2026-04-24 sentence-rewrite refactor. Rule 18 corrected max_tokens formula to match actual code (`min(max(word_count*1.6+32, 128), 512)`, cap is 512 not 2048). Rule 21 updated to reflect `trigger_on_release=True` with full explanation of why False is a permanent dead-end.

### 2026-04-24
- **Patch method full rewrite — indexed-JSON → parallel sentence rewrite.** The indexed-word patch format (`[{"i":3,"new":"..."}]`) was failing in both directions observed in `app_debug.log`: short texts produced no-op floods and adjacent-word duplicates (pass-2 oscillation: "curse curse", "lady lady"), long texts hit `finish_reason=length` on truncated JSON and fell back to 24-second streaming. Root cause: (a) per-slot ctx is only ~3200 tokens (`--ctx-size 12800 / --parallel 4`), not the assumed full ctx; (b) the structured-enumeration format exceeds what 2B-class instruct models can reliably hold; (c) multi-pass feedback amplifies errors on already-clean text.
- **New pipeline** (Phase 0 → Phase 1 → Phase 2): `_dict_prepass()` resolves ~150 common typos (teh→the, recieve→receive) case-preservingly with zero LLM cost and a fast-path skip for short well-formed texts. `_rewrite_sentence_chunk()` rewrites each sentence unit between `<<<START>>>`/`<<<END>>>` markers, with up to 4 units running concurrently through the existing `--parallel 4` llama-server slots. `_hallucination_ratio()` rejects any unit whose edit-distance drift exceeds threshold (0.4 conservative / 0.6 smart_fix), keeping the original text for that unit. Single pass only — no feedback loop.
- **Deleted**: `_parse_indexed_patches()`, `_apply_indexed_patches()`, `_PATCH_SYSTEM_PROMPT`, `_PATCH_SYSTEM_PROMPT_CONSERVATIVE`, `_PATCH_FEW_SHOT`, `_patch_correct_chunk()`, the multi-pass loop, `prev_changes`/`prev_text` divergence guard, `mostly_echo`/`suspicious` heuristics, consecutive-duplicate patch filter. Replaced by: `_COMMON_TYPOS_MAP`, `_dict_prepass()`, `_HALLUCINATION_THRESHOLD_*`, `_hallucination_ratio()`, `_SENTENCE_REWRITE_PROMPT`, `_SENTENCE_REWRITE_PROMPT_CONSERVATIVE`, `_REWRITE_MARKER_RE`, `_extract_rewritten_sentence()`, `_rewrite_sentence_chunk()`.
- **Locked rules updated**: #11 (three-phase pipeline), #12 (single-pass, no multi-pass), #14 (param-flow path renamed), #22 (explicitly deleted/superseded), #25 (40-word units not 180), #26 (unit-failure kept-original not global fallback), #27 (edit-distance hallucination guard replaces divergence guard), #28 (dict pre-pass fast-path conditions).
- **Return-tuple semantics**: `correct_text_patch()` second element is now `units` (sentence count) instead of `passes_run`. Fast path returns `units=0`. `_do_correction()` badge updated to show "Patch (Smart Fix, N units)".

### 2026-04-22

- **Correction engine complete rewrite** — Old patch format `{"old":"...","new":"..."}` allowed whole-sentence rewrites (model ignored "1-3 words" rule) and mandatory pass-2 verification → ~4× slower than streaming for same text. New indexed-word format: words numbered `[1] w [2] w`, model outputs `[{"i":N,"new":"...","span":M?}]`. Output tokens scale with edit count, not text length. Single-pass indexed patches are ~50 tokens for a 200-word text vs ~932 tokens for old 2-pass.
- **Two delivery modes in settings**: (1) Patch — indexed-word patches, up to 3 passes until converged; (2) Streaming Conservative / Streaming Smart Fix — full text streamed token-by-token with minimal system prompt only (no few-shot examples). Config keys changed from `correction_mode` (int) to `correction_method` ("patch"|"stream") + `streaming_strength` ("conservative"|"smart_fix"). One-time migration in `ConfigManager._load` handles old `correction_mode` keys.
- **Deleted**: `correct_text()` (full non-streaming correction), `_apply_patches()`, `_extract_patches_from_response()`. Replaced by `_tokenize_with_ws()`, `_parse_indexed_patches()`, `_apply_indexed_patches()`, `_apply_post_fixes()`.
- **Fixed: Hotkey modifier leakage and hook execution errors** — The previous migration to `pynput` caused modifier leakage (e.g. `Ctrl+Shift+Space` causing `Ctrl+Shift+C` to trigger the browser inspector) and space characters to leak into text. Reverted hotkey detection back to `keyboard.add_hotkey(hk, on_hotkey, suppress=True, trigger_on_release=True)` to honor Rule 21, but maintained `pynput.keyboard.Controller` for reliable `Ctrl+C` / `Ctrl+V` injection. Fixed the Graphify `BeforeTool` hook in `.gemini/settings.json` which failed on Windows due to bash syntax (`[ -f ... ]`); rewritten to use cross-platform `python -c`.
- **Refactored Hotkey System**: Replaced the `keyboard` module with `pynput` to resolve severe bugs where the app would hold modifier keys (Ctrl) at the OS level, causing erratic zooming and scrolling. Implemented a Qt-safe queue + `QTimer` polling architecture so that the pynput background listener does not emit Qt signals directly, preventing cross-thread crashes. The clipboard capture now correctly utilizes `pynput.keyboard.Controller` for simulated Ctrl+C/Ctrl+V presses.
- **Refactor Clean**: Ran dead code analysis (vulture) and surgically removed unused Qt imports (`QSlider`, `QPoint`, `QSize`), redundant OS variables, unreachable code, and an entirely unused `chat_with_model` non-streaming method.
- **Graphify Global Alias**: Fixed a global issue where the `graphify` command was not recognized on Windows. Added a global memory alias mapping `graphify` to `python -m graphify`.

### 2026-04-21
- **Indexed Codebase with Graphify**: Ran `python -m graphify update .` to generate a topology-based knowledge graph for the TextCorrector codebase. The resulting graph contains 181 nodes and 383 edges, exported to `graphify-out/`. This graph provides the AI with structured relationship context.

### 2026-04-20
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
- **README.md rewritten** — Updated to v3.1, removed LanguageTool/Java references, documents CUDA DLL requirement.

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
| **v3.2** | Full app auto-updater (GitHub releases, one-click install), APP_VERSION constant, 71-test suite, README auto-update section, llama.cpp independent-update warning |
| **v3.1.1** | Silent correction (F10), manual hotkey editing, OSD notifications, independent silent strength, CORE_TEMPLATES refactor, chunking newline fix, 42-test suite |
| **v3.1** | CUDA DLL injection, `--reasoning off`, read-only output boxes, line break preservation in diff, single-server chat routing, build.py auto-detect |
| **v2.10** | Patch-based autocorrect (JSON `{old,new}` patches), fallback to full-text |
| **v2.9** | LanguageTool removed; LLM-only; dual model (AC eager + chat lazy); correction strength 0–4 |
| **v2.8** | Bug fixes: nativeEvent segfault, scroll wheel spinboxes, HTML escaping, global exception hook |
| **v2.7** | Full rewrite: GECToR/ONNX removed, PyQt5→PyQt6, dark navy theme, streaming LLM chat |
| **v2.3.x** | GECToR DeBERTa-Large, ONNX idle timers |
| **v2.1.x** | T5-first architecture, ONNX CoEdit-Large |
| **v2.0** | Initial PyQt5 system-tray app, llama.cpp backend |
 
 # # #   L e a r n e d   Q u i r k s   &   S o l u t i o n s  
 -   * * T e r m i n a l - S a f e   H o t k e y s : * *   T o   s y n t h e s i z e   k e y b o a r d   c o p y / p a s t e   u n i v e r s a l l y   a c r o s s   W i n d o w s   w i t h o u t   t r i g g e r i n g   \ S I G I N T \   ( C t r l + C )   i n   t e r m i n a l s ,   u s e   \ V K _ I N S E R T \   c o m b i n e d   w i t h   m o d i f i e r s   ( \ C t r l + I n s e r t \   f o r   c o p y ,   \ S h i f t + I n s e r t \   f o r   p a s t e ) .  
 -   * * A g g r e s s i v e   H T T P   T e a r d o w n : * *   P y t h o n ' s   b l o c k i n g   \  e q u e s t s . p o s t \   a n d   s t r e a m i n g   l o o p s   d o n ' t   a b o r t   i m m e d i a t e l y   o n   t h r e a d   c a n c e l l a t i o n .   T o   a g g r e s s i v e l y   i n t e r r u p t   a n   a c t i v e   s o c k e t / g e n e r a t i o n ,   w r a p   t h e   r e q u e s t   i n   a   \  e q u e s t s . S e s s i o n ( ) \   a n d   i n v o k e   \ s e s s i o n . c l o s e ( ) \   f r o m   a   s e p a r a t e   t h r e a d / e v e n t   w a t c h e r .   T h i s   f o r c e s   a   \ C o n n e c t i o n E r r o r \   a n d   k i l l s   t h e   s o c k e t   i n s t a n t l y .  
 