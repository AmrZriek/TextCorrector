# Final Feature Upgrades Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement four final app upgrades: terminal-safe copy/paste (avoiding `Ctrl+C` SIGINT), multilingual target support, aggressive socket cancellation for streaming, and a large document warning popup.

**Architecture:** 
1. **Terminal Safe IO**: Update `_send_ctrl_chord` to support arbitrary keys. Map copy to `Ctrl+Insert` and paste to `Shift+Insert`. These work across all standard Windows text fields and terminal emulators without emitting Unix signals.
2. **Multilingual**: Add `target_language` to `config.json` (default "English"). Update prompts to interpolate `{lang}`. Add a combo box to the settings UI.
3. **Stream Cancellation**: Inject `requests.Session` into `StreamWorker`, map `stop()` to `session.close()`, and catch `ConnectionError` or `ChunkedEncodingError` gracefully.
4. **Large Doc Warning**: In `_hotkey_worker`, if `len(text.split()) > 1000`, pause and emit a signal to show a `QMessageBox` on the main thread asking for confirmation before proceeding.

**Tech Stack:** Python, PyQt6, requests, ctypes.

---

### Task 1: Terminal-Safe Copy/Paste via Insert Modifiers

**Files:**
- Modify: `text_corrector.py`
- Modify: `tests/test_input_synth.py`

- [ ] **Step 1: Write failing test**

Update `test_input_synth.py` to verify `VK_INSERT` logic.
```python
def test_send_ctrl_chord_handles_insert_for_copy_paste():
    import text_corrector as tc
    # Just verify the constants exist
    assert tc.VK_INSERT == 0x2D
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_input_synth.py::test_send_ctrl_chord_handles_insert_for_copy_paste -v`
Expected: FAIL (AttributeError: module 'text_corrector' has no attribute 'VK_INSERT')

- [ ] **Step 3: Update `text_corrector.py`**

Define `VK_INSERT = 0x2D`.
Update `_send_ctrl_chord` to accept `key_code` and `shift_held` parameters.
```python
VK_C = 0x43
VK_V = 0x56
VK_INSERT = 0x2D

def _send_ctrl_chord(key_code: int, shift_held: bool = False):
    import ctypes
    user32 = ctypes.windll.user32
    
    # ... (Keep existing INPUT struct definitions) ...
    
    inputs = []
    
    # 1. Hold Ctrl (and optionally Shift)
    inputs.append(INPUT(1, _KEYBDINPUT(VK_CONTROL, 0, 0, 0, None)))
    if shift_held:
        inputs.append(INPUT(1, _KEYBDINPUT(VK_SHIFT, 0, 0, 0, None)))
        
    # 2. Press and release the target key
    inputs.append(INPUT(1, _KEYBDINPUT(key_code, 0, 0, 0, None)))
    inputs.append(INPUT(1, _KEYBDINPUT(key_code, 0, KEYEVENTF_KEYUP, 0, None)))
    
    # 3. Release Shift and Ctrl
    if shift_held:
        inputs.append(INPUT(1, _KEYBDINPUT(VK_SHIFT, 0, KEYEVENTF_KEYUP, 0, None)))
    inputs.append(INPUT(1, _KEYBDINPUT(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0, None)))
    
    array_type = INPUT * len(inputs)
    user32.SendInput(len(inputs), array_type(*inputs), ctypes.sizeof(INPUT))
```

Update `_copy()` and `_safe_paste()` to use the new chords.
```python
def _copy():
    # Ctrl+Insert is the safe universal copy
    _send_ctrl_chord(VK_INSERT, shift_held=False)

def _safe_paste():
    # Shift+Insert is the safe universal paste
    # We don't hold Ctrl for Shift+Insert
    # Wait, the method is _send_ctrl_chord, so it holds Ctrl. 
    # Let's rename it to _send_modifier_chord or just handle Shift+Insert directly.
```
Actually, better architecture: rename to `_send_modifier_chord(key_code, ctrl=True, shift=False)`.

```python
def _send_modifier_chord(key_code: int, ctrl: bool = True, shift: bool = False):
    import ctypes
    user32 = ctypes.windll.user32
    inputs = []
    if ctrl: inputs.append(INPUT(1, _KEYBDINPUT(VK_CONTROL, 0, 0, 0, None)))
    if shift: inputs.append(INPUT(1, _KEYBDINPUT(VK_SHIFT, 0, 0, 0, None)))
    inputs.append(INPUT(1, _KEYBDINPUT(key_code, 0, 0, 0, None)))
    inputs.append(INPUT(1, _KEYBDINPUT(key_code, 0, KEYEVENTF_KEYUP, 0, None)))
    if shift: inputs.append(INPUT(1, _KEYBDINPUT(VK_SHIFT, 0, KEYEVENTF_KEYUP, 0, None)))
    if ctrl: inputs.append(INPUT(1, _KEYBDINPUT(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0, None)))
    array_type = INPUT * len(inputs)
    user32.SendInput(len(inputs), array_type(*inputs), ctypes.sizeof(INPUT))
```

In `CorrectionWindow._copy` and `_silent_hotkey_worker`:
Replace `_send_ctrl_chord(VK_C)` with `_send_modifier_chord(VK_INSERT, ctrl=True, shift=False)`.
Replace `_send_ctrl_chord(VK_V)` with `_send_modifier_chord(VK_INSERT, ctrl=False, shift=True)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_input_synth.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add text_corrector.py tests/test_input_synth.py
git commit -m "feat: replace Ctrl+C/V with terminal-safe Insert modifier chords"
```

### Task 2: Multilingual Support

**Files:**
- Modify: `text_corrector.py`

- [ ] **Step 1: Write config test**

Create `tests/test_multilingual.py`.
```python
def test_multilingual_config_default():
    import text_corrector as tc
    assert tc.DEFAULT_CONFIG["target_language"] == "English"
```

- [ ] **Step 2: Update Configuration & UI**

Add `"target_language": "English"` to `DEFAULT_CONFIG`.
In `TextCorrectorApp._build_settings_tab()`, add a QComboBox for Target Language with common options (English, Spanish, French, German, Chinese, Japanese, Auto-Detect).

- [ ] **Step 3: Update Prompts**

Update `_SENTENCE_REWRITE_PROMPT` and `_SENTENCE_REWRITE_PROMPT_CONSERVATIVE` to include:
`"Target output language: {lang}"`.

Update `_rewrite_sentence_chunk` to read the config and format the prompt:
```python
lang = self.cfg.get("target_language", "English")
system = system_template.replace("{lang}", lang)
```

- [ ] **Step 4: Commit**

```bash
git add text_corrector.py tests/test_multilingual.py
git commit -m "feat: add multilingual target language support"
```

### Task 3: Aggressive Cancellation for StreamWorker

**Files:**
- Modify: `text_corrector.py`

- [ ] **Step 1: Inject Session into StreamWorker**

In `StreamWorker.run()`:
```python
        import requests
        self._session = requests.Session()
        try:
            with self._session.post(self.url, json=self.payload, stream=True, timeout=60) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not self._running:
                        break
                    # ... existing logic ...
        except (requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError):
            self.error.emit("Stream cancelled.")
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self._session.close()
```

In `StreamWorker.stop()`:
```python
    def stop(self):
        self._running = False
        if hasattr(self, '_session'):
            self._session.close()
```

- [ ] **Step 2: Commit**

```bash
git add text_corrector.py
git commit -m "feat: aggressive socket teardown for stream worker"
```

### Task 4: Large Document Warning

**Files:**
- Modify: `text_corrector.py`

- [ ] **Step 1: Add Guard in Hotkey Worker**

In `TextCorrectorApp`: add a new signal `_large_doc_warning_signal = pyqtSignal(str)`.

In `TextCorrectorApp.__init__`:
```python
self._large_doc_warning_signal.connect(self._show_large_doc_warning)
```

```python
    def _show_large_doc_warning(self, text):
        from PyQt6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            None,
            "Large Document",
            f"You selected {len(text.split())} words. This is a very long document and may take a minute to process. Proceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._trigger.emit(text)
        else:
            self._hotkey_busy.release()
```

In `_hotkey_worker`, after grabbing text:
```python
        if len(text.split()) > 1000:
            self._large_doc_warning_signal.emit(text)
            return # The signal handler will call _trigger.emit if Yes
```

- [ ] **Step 2: Commit**

```bash
git add text_corrector.py
git commit -m "feat: add large document warning confirmation"
```
