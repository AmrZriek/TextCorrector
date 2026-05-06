# Cancellation & Editable Templates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement aggressive HTTP socket cancellation for background autocorrect to free up resources immediately, and migrate all templates to `config.json` with a full UI for editing and deleting them. Also update `.gitignore` for test artifacts.

**Architecture:** 
1. `InterruptibleSession`: Wrap `requests.post` inside `ModelManager._rewrite_sentence_chunk` with a session that can be aggressively closed (`session.close()`) when `_cancel_event` is set. Catch `ConnectionError`.
2. `ConfigManager`: Remove `CORE_TEMPLATES` constant. In `ConfigManager._load`, if `custom_templates` is empty, populate it with improved defaults.
3. `CorrectionWindow`: Update `_refresh_templates` to render each template with an "Edit" (✏️) button. Create `_edit_template` to show a dialog for Name and Prompt. Add a "Delete" button inside the dialog. Update `config.json` via `ConfigManager.save()`.

**Tech Stack:** Python, PyQt6, requests.

---

### Task 1: Update .gitignore for test artifacts

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Add temp test directories to gitignore**

Modify `.gitignore` to include the temporary directory created by the E2E test.

```gitignore
tests/temp_e2e_launch/
__pycache__/
.pytest_cache/
```

- [ ] **Step 2: Commit**

```bash
git add .gitignore
git commit -m "chore: ignore test artifacts"
```

### Task 2: Implement ConfigManager Default Templates

**Files:**
- Modify: `text_corrector.py`
- Create: `tests/test_templates.py`

- [ ] **Step 1: Define DEFAULT_TEMPLATES in test_templates.py and write tests**

```python
import pytest
from text_corrector import ConfigManager

def test_config_manager_populates_default_templates(monkeypatch, tmp_path):
    # Use a temporary config file
    config_file = tmp_path / "config.json"
    monkeypatch.setattr("text_corrector.CONFIG_FILE", config_file)
    
    mgr = ConfigManager()
    templates = mgr.config.get("custom_templates", [])
    
    assert len(templates) >= 5
    assert templates[0]["name"] == "📧 Email"
    assert "professional email" in templates[0]["prompt"]
    assert "Do not add greetings or closings if they are not already present." in templates[0]["prompt"]
    
    # Save and reload should preserve
    mgr.save()
    mgr2 = ConfigManager()
    assert len(mgr2.config.get("custom_templates", [])) == len(templates)
```

- [ ] **Step 2: Modify ConfigManager in text_corrector.py**

1. Remove the `CORE_TEMPLATES` constant block completely.
2. Add a `DEFAULT_TEMPLATES` constant with the new default dicts:

```python
DEFAULT_TEMPLATES = [
    {"name": "📧 Email", "prompt": "Polish this text for a professional email. Do not add greetings or closings if they are not already present. Preserve the user's core wording."},
    {"name": "💬 Social", "prompt": "Rewrite this as a social media post. Keep the casual tone, original capitalization style, and personality. Make it engaging but don't over-polish it."},
    {"name": "📝 Formal", "prompt": "Rewrite this in formal English. Expand contractions, use complete sentences, maintain professional tone."},
    {"name": "⚡ Tighten", "prompt": "Optimize this text. Make it tighter, straight to the point, without cutting details or meaning."},
    {"name": "📢 Headline", "prompt": "Rewrite this as a punchy, engaging headline or short tagline. Title case."},
]
```

3. Update `ConfigManager._load()` to populate defaults if `custom_templates` is empty.

```python
        # Populate default templates if empty
        if not cfg.get("custom_templates"):
            cfg["custom_templates"] = DEFAULT_TEMPLATES.copy()
```

- [ ] **Step 3: Run tests**

Run `python -m pytest tests/test_templates.py -v`. Expect PASS.

- [ ] **Step 4: Commit**

```bash
git add text_corrector.py tests/test_templates.py
git commit -m "feat: migrate templates to config with improved defaults"
```

### Task 3: Editable Templates UI in CorrectionWindow

**Files:**
- Modify: `text_corrector.py`

- [ ] **Step 1: Update `_refresh_templates` to render Edit buttons**

Instead of just `_make_btn`, create a composite widget with the template button and an edit button.

```python
        def _make_template_widget(idx, template):
            w = QWidget()
            l = QHBoxLayout(w)
            l.setContentsMargins(0, 0, 0, 0)
            l.setSpacing(2)
            
            b = QPushButton(template.get("name", "Custom"))
            b.setObjectName("ghost")
            b.setStyleSheet(btn_style)
            b.clicked.connect(lambda _, p=template.get("prompt", ""): self._apply_template(p))
            
            e = QPushButton("✏️")
            e.setObjectName("ghost")
            e.setFixedSize(24, 24)
            e.setStyleSheet(
                "QPushButton{border-radius:12px;background:rgba(255,255,255,0.05);color:#cbd5e1;}"
                "QPushButton:hover{background:rgba(255,255,255,0.1);}"
            )
            e.clicked.connect(lambda _, i=idx: self._edit_template(i))
            
            l.addWidget(b)
            l.addWidget(e)
            return w

        custom_templates = self.cfg.get("custom_templates", [])
        for idx, ct in enumerate(custom_templates):
            self.tmp_lay.addWidget(_make_template_widget(idx, ct))
```

- [ ] **Step 2: Implement `_edit_template` method**

Add a new dialog to edit name and prompt, and a delete button.

```python
    def _edit_template(self, idx: int):
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QTextEdit, QPushButton, QMessageBox
        
        templates = self.cfg.get("custom_templates", [])
        if idx < 0 or idx >= len(templates):
            return
            
        tmpl = templates[idx]
        
        dlg = QDialog(self)
        dlg.setWindowTitle("Edit Template")
        dlg.resize(400, 300)
        dlg.setStyleSheet(self.styleSheet())
        
        lay = QVBoxLayout(dlg)
        
        lay.addWidget(QLabel("Template Name:"))
        name_edit = QLineEdit(tmpl.get("name", ""))
        lay.addWidget(name_edit)
        
        lay.addWidget(QLabel("Prompt / Instructions:"))
        prompt_edit = QTextEdit()
        prompt_edit.setPlainText(tmpl.get("prompt", ""))
        lay.addWidget(prompt_edit)
        
        btn_lay = QHBoxLayout()
        
        del_btn = QPushButton("Delete")
        del_btn.setObjectName("danger")
        del_btn.clicked.connect(lambda: dlg.done(2))  # custom code for delete
        btn_lay.addWidget(del_btn)
        
        btn_lay.addStretch()
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("ghost")
        cancel_btn.clicked.connect(dlg.reject)
        btn_lay.addWidget(cancel_btn)
        
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(dlg.accept)
        btn_lay.addWidget(save_btn)
        
        lay.addLayout(btn_lay)
        
        res = dlg.exec()
        if res == QDialog.DialogCode.Accepted:
            tmpl["name"] = name_edit.text().strip()
            tmpl["prompt"] = prompt_edit.toPlainText().strip()
            # Update config manager reference to ensure it saves correctly
            self.parent().config_mgr.save()
            self._refresh_templates()
        elif res == 2:
            reply = QMessageBox.question(self, "Confirm Delete", "Are you sure you want to delete this template?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                templates.pop(idx)
                self.parent().config_mgr.save()
                self._refresh_templates()
```

Also update `_add_custom_template` to save properly as a dictionary.

```python
    def _add_custom_template(self):
        from PyQt6.QtWidgets import QInputDialog
        name, ok1 = QInputDialog.getText(self, "New Template", "Template name (e.g. 🤓 Fun):")
        if not ok1 or not name.strip():
            return
        prompt, ok2 = QInputDialog.getText(self, "New Template", f"Prompt for {name}:")
        if not ok2 or not prompt.strip():
            return
            
        templates = self.cfg.get("custom_templates", [])
        templates.append({"name": name.strip(), "prompt": prompt.strip()})
        self.cfg["custom_templates"] = templates
        self.parent().config_mgr.save()
        self._refresh_templates()
```

- [ ] **Step 3: Run app to manually verify UI (or write a quick UI test)**

Ensure the app starts without errors.

- [ ] **Step 4: Commit**

```bash
git add text_corrector.py
git commit -m "feat: add template editing and deletion UI"
```

### Task 4: Aggressive HTTP Cancellation for Autocorrect

**Files:**
- Modify: `text_corrector.py`
- Create: `tests/test_cancellation.py`

- [ ] **Step 1: Write test for cancellation behavior**

```python
import pytest
import threading
import time
from text_corrector import ModelManager

class MockConfig:
    def get(self, key, default=None): return default

def test_rewrite_chunk_cancellation(monkeypatch):
    mgr = ModelManager(MockConfig())
    mgr._chat_url = lambda: "http://fake"
    
    cancel_event = threading.Event()
    
    # Mock requests.Session.post to simulate a long-running request that gets cancelled
    class MockSession:
        def __init__(self):
            pass
        def post(self, url, json, timeout):
            # Wait a bit, checking if cancelled
            start = time.time()
            while time.time() - start < 1.0:
                if cancel_event.is_set():
                    import requests
                    raise requests.exceptions.ConnectionError("Cancelled via session.close()")
                time.sleep(0.01)
            class R:
                ok = True
                status_code = 200
                def json(self): return {"choices": [{"message": {"content": "<<<START>>>test<<<END>>>"}}]}
                def raise_for_status(self): pass
            return R()
        def close(self):
            cancel_event.set() # Simulate the close triggering the connection error
            
    monkeypatch.setattr("requests.Session", MockSession)
    
    # In a separate thread, cancel after 0.1s
    def cancel_later():
        time.sleep(0.1)
        # Note: the real implementation will cancel via the session watcher
        cancel_event.set()
        
    threading.Thread(target=cancel_later).start()
    
    # This should return None because it was cancelled (ConnectionError caught)
    res = mgr._rewrite_sentence_chunk("test", None, 1, 1, "smart_fix", cancel_event=cancel_event)
    assert res is None
```

- [ ] **Step 2: Implement interruptible session in `_rewrite_sentence_chunk`**

Modify `_rewrite_sentence_chunk` signature to accept `cancel_event: threading.Event = None`.

Inside `_rewrite_sentence_chunk`:
```python
        import requests
        session = requests.Session()
        
        # Start a watcher thread to close the session if cancel_event is set
        watcher_running = True
        def watcher():
            while watcher_running:
                if cancel_event and cancel_event.is_set():
                    session.close()
                    break
                time.sleep(0.05)
                
        watcher_thread = threading.Thread(target=watcher, daemon=True)
        if cancel_event:
            watcher_thread.start()

        try:
            r = session.post(
                self._chat_url(),
                json=payload,
                timeout=60,
            )
            r.raise_for_status()
            raw = r.json()["choices"][0]["message"]["content"]
        except requests.exceptions.ConnectionError:
            log(f"[{self.label}] chunk {unit_idx} connection closed (likely cancelled)")
            return None
        except Exception as e:
            log(f"[{self.label}] rewrite_sentence_chunk failed: {e}")
            return None
        finally:
            watcher_running = False
            session.close()
```

- [ ] **Step 3: Update `correct_text_patch` callers**

In `correct_text_patch`, ensure `cancel_event` is passed to `_rewrite_sentence_chunk`.

```python
            future = executor.submit(
                self._rewrite_sentence_chunk,
                chunk_text,
                custom_sys,
                idx,
                total_chunks,
                strength,
                cancel_event, # Pass the cancel_event here
            )
```

- [ ] **Step 4: Run tests**

Run `python -m pytest tests/test_cancellation.py -v`. Expect PASS.

- [ ] **Step 5: Commit**

```bash
git add text_corrector.py tests/test_cancellation.py
git commit -m "feat: implement aggressive socket cancellation for patch requests"
```
