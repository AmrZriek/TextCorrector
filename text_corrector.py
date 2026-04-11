"""
TextCorrector v4.0
==================
Instant AI-powered text correction with a premium dark UI.

Architecture
------------
- Autocorrect      : lightweight LLM via llama.cpp (loaded at boot, instant)
- Chat / rewrite   : separate LLM via llama.cpp (lazy-load, unloads after idle)
- GUI              : PyQt6, frameless dark-navy theme
- Hotkey           : global keyboard hook → clipboard copy → correction popup

Cross-platform: Windows / macOS / Linux.
Single-file deployment (plus llama_cpp/ binary folder and LLM model .gguf).
"""

# ── stdlib ─────────────────────────────────────────────────────────────────
import sys, re, os, threading, time, subprocess, json, socket
from datetime import datetime
from pathlib import Path

# ── Qt HiDPI env vars must be set before importing PyQt6 ───────────────────
os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")

# ── third-party ─────────────────────────────────────────────────────────────
import keyboard
import pyperclip
import requests

from PyQt6.QtWidgets import (
    QApplication,
    QSystemTrayIcon,
    QMenu,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTextEdit,
    QPushButton,
    QLabel,
    QLineEdit,
    QFileDialog,
    QCheckBox,
    QDialog,
    QComboBox,
    QFrame,
    QSizeGrip,
    QScrollArea,
    QSpinBox,
    QDoubleSpinBox,
    QSlider,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject, QThread, QPoint, QSize
from PyQt6.QtGui import QIcon, QPixmap, QColor, QPainter, QCursor, QAction

# ── Platform detection ───────────────────────────────────────────────────────
WINDOWS = sys.platform == "win32"
MACOS = sys.platform == "darwin"
if WINDOWS:
    import winreg

# ── Portable base directory ──────────────────────────────────────────────────
if getattr(sys, "frozen", False):
    SCRIPT_DIR = Path(sys.executable).parent.resolve()
else:
    SCRIPT_DIR = Path(__file__).parent.resolve()

CONFIG_FILE = SCRIPT_DIR / "config.json"
LLAMA_CPP_DIR = SCRIPT_DIR / "llama_cpp"
LOG_FILE = SCRIPT_DIR / "server_log.txt"
DEBUG_LOG = SCRIPT_DIR / "app_debug.log"

SERVER_EXE = "llama-server.exe" if WINDOWS else "llama-server"

GITHUB_RELEASES_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"


def _get_local_build_number() -> int:
    """Return the local llama-server build number (e.g. 8196), or 0 on failure."""
    try:
        exe = LLAMA_CPP_DIR / SERVER_EXE
        if not exe.exists():
            return 0
        r = subprocess.run(
            [str(exe), "--version"],
            capture_output=True, text=True, timeout=8,
            **{"creationflags": 0x08000000} if WINDOWS else {},
        )
        # Output: "version: 8196 (c99909dd0)"
        m = re.search(r"version:\s*(\d+)", r.stderr + r.stdout)
        return int(m.group(1)) if m else 0
    except Exception:
        return 0


class UpdateChecker(QThread):
    """Background thread — checks GitHub for a newer llama.cpp release."""
    update_available = pyqtSignal(str, int)   # (new_tag, new_build_number)
    check_done = pyqtSignal()

    def run(self):
        try:
            import urllib.request
            req = urllib.request.Request(
                GITHUB_RELEASES_API,
                headers={"User-Agent": "TextCorrector-update-checker"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            tag = data.get("tag_name", "")           # e.g. "b8690"
            m = re.search(r"b(\d+)", tag)
            if not m:
                return
            remote_build = int(m.group(1))
            local_build = _get_local_build_number()
            log(f"[Update] local build={local_build}  remote build={remote_build}  tag={tag}")
            if remote_build > local_build:
                self.update_available.emit(tag, remote_build)
        except Exception as e:
            log(f"[Update] Check failed: {e}")
        finally:
            self.check_done.emit()



# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


def log(msg: str):
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def has_nvidia() -> bool:
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
            **({"creationflags": 0x08000000} if WINDOWS else {}),
        )
        return r.returncode == 0 and bool(r.stdout.strip())
    except Exception:
        return False


def friendly_name(path: str) -> str:
    n = os.path.basename(path).replace(".gguf", "")
    for old, new in [
        ("-it-", " IT "),
        ("-F16", " F16"),
        ("-BF16", " BF16"),
        ("-Q4_K_M", " Q4_K_M"),
        ("-Q8_0", " Q8"),
        ("-Q4_K_XL", " Q4_K_XL"),
        ("-IQ4_NL", " IQ4"),
        ("-GGUF", ""),
        ("-gguf", ""),
    ]:
        n = n.replace(old, new)
    return n


def strip_thinking_tokens(text: str) -> str:
    """Strip thinking/reasoning blocks from model output.

    Handles various formats:
    - <think>...</think> (Qwen3, DeepSeek)
    - <thinking>...</thinking> (some models)
    - <reasoning>...</reasoning> (alternative format)
    """
    if not text:
        return text

    # Remove various thinking block formats (including multiline content)
    thinking_patterns = [
        (r"<think>.*?</think>", re.DOTALL),  # Qwen3, DeepSeek
        (r"<thinking>.*?</thinking>", re.DOTALL),  # Alternative format
        (r"<reasoning>.*?</reasoning>", re.DOTALL),  # Alternative format
    ]

    cleaned = text
    for pattern, flags in thinking_patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=flags)

    # Also handle unclosed thinking tags (model may not close them)
    unclosed_patterns = [
        r"<think>.*",
        r"<thinking>.*",
        r"<reasoning>.*",
    ]
    for pattern in unclosed_patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.DOTALL)

    return cleaned.strip()


def strip_meta_commentary(text: str, original: str = "") -> str:
    """Strip common meta-commentary prefixes that models add."""
    if not text:
        return text
    # Comprehensive list of preamble patterns models add
    preamble_patterns = [
        r"^(?:Here(?:\'s| is) the corrected (?:text|version)[:\.]?\s*\n?)",
        r"^(?:Sure[,!]? [Hh]ere(?:\'s| is) the corrected (?:text|version)[:\.]?\s*\n?)",
        r"^(?:Corrected (?:text|version)[:\.]?\s*\n?)",
        r"^(?:The corrected (?:text|version)[:\.]?\s*\n?)",
        r"^(?:I(?:\'ve| have) corrected the (?:text|text for you)[:\.]?\s*\n?)",
        r"^(?:Below is the corrected (?:text|version)[:\.]?\s*\n?)",
        r"^(?:This is the corrected (?:text|version)[:\.]?\s*\n?)",
        r"^(?:I\'ve proofread and refined the text[:\.]?\s*\n?)",
        r"^(?:I\'ve made the following corrections[:\.]?\s*\n?)",
        r"^\*\*Corrected(?: text)?\*\*[:\.]?\s*\n?",  # Markdown bold
        r"^#+\s*Corrected(?: text)?[:\.]?\s*\n?",  # Markdown headers
        r"^[-*]{3,}\s*\n?",  # Separator lines
        r"^(?:Here are the corrections?[:\.]?\s*\n?)",
        r"^(?:The refined (?:text|version)[:\.]?\s*\n?)",
        r"^(?:I\'ve reviewed and corrected[:\.]?\s*\n?)",
        r"^(?:I\'ve proofread (?:and refined )?your text[:\.]?\s*\n?)",
        r"^(?:Here is the refined (?:text|version)[:\.]?\s*\n?)",
        r"^(?:The text has been corrected[:\.]?\s*\n?)",
        r"^(?:Your text,? corrected[:\.]?\s*\n?)",
    ]
    cleaned = text
    for pattern in preamble_patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    # Strip wrapping quotes if the entire output is quoted
    cleaned = cleaned.strip()
    if len(cleaned) > 2 and cleaned[0] == '"' and cleaned[-1] == '"':
        if not (original.startswith('"') and original.endswith('"')):
            cleaned = cleaned[1:-1]
    if len(cleaned) > 2 and cleaned[0] == "'" and cleaned[-1] == "'":
        if not (original.startswith("'") and original.endswith("'")):
            cleaned = cleaned[1:-1]
    # Strip markdown code blocks if wrapping the entire output
    if cleaned.startswith("```") and cleaned.endswith("```"):
        lines = cleaned.split("\n")
        if len(lines) >= 2:
            # Remove first and last lines (the ``` markers)
            cleaned = "\n".join(lines[1:-1])
    return cleaned.strip()


def contains_meta_commentary(text: str) -> bool:
    """Check if text still contains meta-commentary after stripping."""
    if not text:
        return False

    # Patterns that indicate the model is being conversational
    conversational_patterns = [
        r"^\s*(?:Here|Sure|Okay|Alright|So|Well|Now)[,\s]+",
        r"^\s*(?:I\s+(?:think|believe|feel|would say)|In my (?:opinion|view))",
        r"^\s*(?:The\s+(?:corrected|refined)\s+(?:text|version))",
        r"^\s*(?:I\s+(?:have|ve)\s+(?:corrected|fixed|updated))",
        r"\n\n(?:Let me know|I hope|Feel free|If you need)",
        r"\*\*\s*(?:Note|Important|Warning)",
        r"^\s*[:\-]+\s*",  # Lines starting with just punctuation
    ]

    for pattern in conversational_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True

    # Check for question marks (models asking for clarification)
    if text.count("?") > 0:
        return True

    # Check for multiple sentences that look like explanations
    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
    if len(sentences) > 3:
        # If there are many short sentences, might be commentary
        short_sentences = sum(1 for s in sentences if len(s.split()) < 5)
        if short_sentences > len(sentences) / 2:
            return True

    return False


# Keep old function names as aliases for backward compatibility
strip_think = strip_thinking_tokens
strip_preamble = strip_meta_commentary


def _apply_patches(original: str, patches: list[dict]) -> str:
    """Apply a list of word-level patches to the original text.

    Each patch is a dict with keys:
      - "old": the wrong word/phrase to find
      - "new": the corrected replacement

    Uses regex with negative lookbehind/lookahead for safe replacement.
    Handles punctuation adjacency (e.g., "Hello,world") and multiple occurrences
    by replacing only the first match per patch.
    """
    result = original
    patches_applied = 0

    for patch in patches:
        old_word = patch.get("old", "").strip()
        new_word = patch.get("new", "").strip()
        if not old_word or not new_word:
            continue

        escaped = re.escape(old_word)
        # Use negative lookbehind/lookahead instead of \b to handle punctuation adjacency
        # Matches word not preceded/followed by letter (allows punctuation boundaries)
        # CASE SENSITIVE: model was instructed to return old EXACTLY as it appears in text
        pattern = re.compile(rf"(?<![a-zA-Z]){escaped}(?![a-zA-Z])")

        match = pattern.search(result)
        if match:
            result = pattern.sub(new_word, result, count=1)
            patches_applied += 1
        else:
            # Also try case-insensitive as fallback, but log it
            pattern_ci = re.compile(
                rf"(?<![a-zA-Z]){escaped}(?![a-zA-Z])", re.IGNORECASE
            )
            match_ci = pattern_ci.search(result)
            if match_ci:
                log(
                    f"[PATCH] Case mismatch: model returned '{old_word}' but found '{match_ci.group()}' - applying anyway"
                )
                result = pattern_ci.sub(new_word, result, count=1)
                patches_applied += 1
            else:
                log(
                    f"[PATCH] No match found for: '{old_word}' → '{new_word}' (skipping)"
                )

    if patches_applied == 0:
        log(f"[PATCH] No patches were applied successfully")
        return original

    log(f"[PATCH] Applied {patches_applied}/{len(patches)} patches successfully")
    return result


def _extract_content_from_response(resp: dict) -> tuple[str, str]:
    """Extract usable text content from an llama.cpp API response.

    Handles thinking models where content is empty and reasoning_content
    has the output (llama.cpp auto-activates thinking mode for models whose
    GGUF chat template includes <think> tokens).

    Returns:
        (content, finish_reason) — content may be empty if thinking consumed
        all tokens.
    """
    choice = resp["choices"][0]
    finish_reason = choice.get("finish_reason", "")
    message = choice["message"]
    content = (message.get("content") or "").strip()
    reasoning = (message.get("reasoning_content") or "").strip()

    if content:
        return content, finish_reason

    if reasoning:
        log(
            "[API] Thinking model detected: content is empty, reasoning_content present. "
            "The model spent all tokens on reasoning and never produced output. "
            "Ensure 'think: false' is in the API payload to disable this."
        )

    return "", finish_reason


def _extract_patches_from_response(raw: str) -> list[dict] | None:
    """Extract JSON patch array from LLM response.

    Returns:
        list[dict]: Parsed patches (may be empty [] if model says text is correct).
        None: Parsing failed — raw had content but no valid JSON found.

    Handles cases where the model wraps JSON in markdown code fences
    or adds explanatory text around it.
    """
    if not raw or not raw.strip():
        return []  # Empty input = no corrections needed

    cleaned = strip_think(raw)
    cleaned = strip_preamble(cleaned)

    # Strip markdown code fences
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    # Try direct JSON array parse
    try:
        data = json.loads(cleaned)
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, ValueError):
        pass

    # Try to find JSON array in the text
    array_match = re.search(r"\[[\s\S]*\]", cleaned)
    if array_match:
        try:
            data = json.loads(array_match.group(0))
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, ValueError):
            # Try fixing trailing comma (common with small models)
            fixed = re.sub(r",\s*\]", "]", array_match.group(0))
            try:
                data = json.loads(fixed)
                if isinstance(data, list):
                    return data
            except (json.JSONDecodeError, ValueError):
                pass

    # Try to find JSON object with "patches" key
    obj_match = re.search(r"\{[\s\S]*\}", cleaned)
    if obj_match:
        try:
            data = json.loads(obj_match.group(0))
            if isinstance(data, dict) and "patches" in data:
                return data["patches"]
        except (json.JSONDecodeError, ValueError):
            pass

    # Parsing failed — raw had content but no valid JSON
    return None


def _checkbox_css() -> str:
    """Return QSS for checkboxes with a visible checkmark icon.

    Writes a small SVG to disk once (Qt QSS cannot embed data URIs for images).
    """
    svg_path = SCRIPT_DIR / "_checkmark.svg"
    try:
        if not svg_path.exists():
            svg_path.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 12 12">'
                '<path d="M2 6L5 9L10 3" stroke="white" stroke-width="2.2" '
                'fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>',
                encoding="utf-8",
            )
        p = str(svg_path).replace("\\", "/")
        return (
            "QCheckBox { color: #94a3b8; spacing: 8px; }"
            "QCheckBox:checked { color: #e2e8f0; }"
            "QCheckBox::indicator {"
            " width: 16px; height: 16px;"
            " border: 1.5px solid rgba(59,130,246,0.35);"
            " border-radius: 4px; background: rgba(4,10,28,0.8); }"
            "QCheckBox::indicator:hover { border: 1.5px solid rgba(96,165,250,0.65); }"
            f'QCheckBox::indicator:checked {{ background: #3b82f6;'
            f' border: 1.5px solid #60a5fa; image: url("{p}"); }}'
        )
    except Exception:
        return ""


# ── Scroll-wheel ignore helper ────────────────────────────────────────────────
class _IgnoreWheelFilter(QObject):
    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent

        if event.type() == QEvent.Type.Wheel:
            event.ignore()
            return True
        return super().eventFilter(obj, event)


_IGNORE_WHEEL = _IgnoreWheelFilter()


def no_scroll(widget):
    widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    widget.installEventFilter(_IGNORE_WHEEL)
    return widget


# ═══════════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════════

DEFAULT_CONFIG: dict = {
    # llama.cpp
    "llama_server_path": str(LLAMA_CPP_DIR / SERVER_EXE),
    "model_path": "",
    "server_host": "127.0.0.1",
    "server_port": 8080,
    "context_size": 4096,
    "gpu_layers": 99,
    "temperature": 0.1,
    "top_k": 40,
    "top_p": 0.95,
    "min_p": 0.05,
    "keep_model_loaded": True,
    "idle_timeout_seconds": 300,
    "recent_models": [],
    # Autocorrect model
    "ac_model_path": "",
    "ac_same_as_chat": True,
    # Hotkey
    "hotkey": "ctrl+shift+space",
    # Misc
    "system_prompt": "",
    # Correction mode: 0=Conservative (typos only), 1=Smart Fix (aggressive patch)
    "correction_mode": 0,
    # Custom templates: list of {"name": str, "prompt": str}
    "custom_templates": [],
}


class ConfigManager:
    def __init__(self):
        self.config = self._load()
        self._auto_detect()

    def _load(self) -> dict:
        cfg = DEFAULT_CONFIG.copy()
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE) as f:
                    saved = json.load(f)
                cfg.update(saved)
            except Exception as e:
                log(f"Config load error: {e}")
        return cfg

    def save(self):
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            log(f"Config save error: {e}")

    def _auto_detect(self):
        path = self.config.get("model_path", "")
        if not path or not Path(path).exists():
            gguf = sorted(SCRIPT_DIR.glob("*.gguf"))
            if gguf:
                self.config["model_path"] = str(gguf[0])
                self.config["recent_models"] = [str(p) for p in gguf]
                self.save()
        if self.config.get("ac_same_as_chat", True):
            self.config["ac_model_path"] = self.config.get("model_path", "")

    def get(self, key, default=None):
        return self.config.get(key, default)

    def set(self, key, value):
        self.config[key] = value
        self.save()

    def add_recent(self, path: str):
        r = self.config.get("recent_models", [])
        if path in r:
            r.remove(path)
        r.insert(0, path)
        self.config["recent_models"] = r[:10]
        self.save()


# ═══════════════════════════════════════════════════════════════════════════════
# Hotkey recorder widget
# ═══════════════════════════════════════════════════════════════════════════════

_QT_KEYS = {
    Qt.Key.Key_Space: "space",
    Qt.Key.Key_Return: "enter",
    Qt.Key.Key_Enter: "enter",
    Qt.Key.Key_Tab: "tab",
    Qt.Key.Key_Backspace: "backspace",
    Qt.Key.Key_Delete: "delete",
    Qt.Key.Key_Escape: "escape",
    Qt.Key.Key_Home: "home",
    Qt.Key.Key_End: "end",
    Qt.Key.Key_PageUp: "page up",
    Qt.Key.Key_PageDown: "page down",
    Qt.Key.Key_Left: "left",
    Qt.Key.Key_Right: "right",
    Qt.Key.Key_Up: "up",
    Qt.Key.Key_Down: "down",
    Qt.Key.Key_F1: "f1",
    Qt.Key.Key_F2: "f2",
    Qt.Key.Key_F3: "f3",
    Qt.Key.Key_F4: "f4",
    Qt.Key.Key_F5: "f5",
    Qt.Key.Key_F6: "f6",
    Qt.Key.Key_F7: "f7",
    Qt.Key.Key_F8: "f8",
    Qt.Key.Key_F9: "f9",
    Qt.Key.Key_F10: "f10",
    Qt.Key.Key_F11: "f11",
    Qt.Key.Key_F12: "f12",
}
_MOD_KEYS = {
    Qt.Key.Key_Control,
    Qt.Key.Key_Shift,
    Qt.Key.Key_Alt,
    Qt.Key.Key_Meta,
    Qt.Key.Key_AltGr,
}


class HotkeyEdit(QLineEdit):
    shortcut_changed = pyqtSignal(str)

    _IDLE = """
        QLineEdit { background: rgba(5,14,40,0.7); border: 1px solid rgba(59,130,246,0.25);
                    border-radius: 8px; padding: 8px 14px; color: #e2e8f0; font-size: 13px; }
        QLineEdit:hover { border: 1px solid rgba(59,130,246,0.5); }
    """
    _REC = """
        QLineEdit { background: rgba(37,99,235,0.15); border: 2px solid rgba(96,165,250,0.8);
                    border-radius: 8px; padding: 8px 14px; color: #93c5fd; font-size: 13px; }
    """

    def __init__(self, parent=None, re_register_cb=None):
        super().__init__(parent)
        self._combo = ""
        self._recording = False
        self._re_register_cb = re_register_cb
        self.setReadOnly(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(self._IDLE)
        self._refresh()

    def text(self) -> str:
        return self._combo

    def setText(self, val: str):
        self._combo = val.lower().strip()
        self._recording = False
        self.setStyleSheet(self._IDLE)
        self._refresh()

    def _refresh(self):
        display = (
            " + ".join(p.capitalize() for p in self._combo.split("+"))
            if self._combo
            else "Click to record"
        )
        super().setText(display)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and not self._recording:
            self._recording = True
            self.setStyleSheet(self._REC)
            super().setText("Press keys…")
            try:
                keyboard.unhook_all_hotkeys()
            except Exception:
                pass

    def focusOutEvent(self, e):
        if self._recording:
            self._recording = False
            self.setStyleSheet(self._IDLE)
            self._refresh()
            if self._re_register_cb:
                try:
                    self._re_register_cb()
                except Exception:
                    pass
        super().focusOutEvent(e)

    def keyPressEvent(self, e):
        if not self._recording:
            return
        key = e.key()
        mods = e.modifiers()
        if key == Qt.Key.Key_Escape:
            self._recording = False
            self.setStyleSheet(self._IDLE)
            self._refresh()
            if self._re_register_cb:
                try:
                    self._re_register_cb()
                except Exception:
                    pass
            return
        if key in _MOD_KEYS:
            return
        parts = []
        if mods & Qt.KeyboardModifier.ControlModifier:
            parts.append("ctrl")
        if mods & Qt.KeyboardModifier.ShiftModifier:
            parts.append("shift")
        if mods & Qt.KeyboardModifier.AltModifier:
            parts.append("alt")
        if not parts:
            super().setText("Add Ctrl / Shift / Alt…")
            return
        kn = _QT_KEYS.get(key) or (e.text().lower() or None)
        if not kn:
            return
        parts.append(kn)
        combo = "+".join(parts)
        self._recording = False
        self._combo = combo
        self.setStyleSheet(self._IDLE)
        self._refresh()
        self.shortcut_changed.emit(combo)
        if self._re_register_cb:
            try:
                self._re_register_cb()
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════════════════
# Streaming worker  (llama.cpp SSE)
# ═══════════════════════════════════════════════════════════════════════════════


class StreamWorker(QThread):
    token = pyqtSignal(str)
    done = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, url: str, payload: dict):
        super().__init__()
        self.url = url
        self.payload = {**payload, "stream": True}
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        full = ""
        try:
            with requests.post(
                self.url, json=self.payload, stream=True, timeout=120
            ) as r:
                r.raise_for_status()
                for raw in r.iter_lines():
                    if self._stop:
                        break
                    if not raw:
                        continue
                    line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        t = chunk["choices"][0]["delta"].get("content", "")
                        if t:
                            full += t
                            self.token.emit(t)
                    except Exception:
                        pass
            self.done.emit(full)
        except Exception as e:
            self.error.emit(str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# llama.cpp ModelManager — handles one llama-server instance
# ═══════════════════════════════════════════════════════════════════════════════


class ModelManager(QObject):
    status_changed = pyqtSignal(str)
    model_loaded = pyqtSignal()
    model_unloaded = pyqtSignal()

    def __init__(
        self,
        cfg: ConfigManager,
        model_path_key: str = "model_path",
        label: str = "LLM",
        keep_loaded_key: str = "keep_model_loaded",
        idle_timeout_key: str = "idle_timeout_seconds",
    ):
        super().__init__()
        self.cfg = cfg
        self.model_path_key = model_path_key
        self.label = label
        self.keep_loaded_key = keep_loaded_key
        self.idle_timeout_key = idle_timeout_key
        self.server_process = None
        self.log_file = None
        self.last_used = None
        self.loading = False
        self._lock = threading.Lock()

    # ── internal helpers ──────────────────────────────────────────────────
    def _base_url(self) -> str:
        h = self.cfg.get("server_host", "127.0.0.1")
        p = self.cfg.get("server_port", 8080)
        return f"http://{h}:{p}"

    def _health_url(self) -> str:
        return self._base_url() + "/health"

    def _chat_url(self) -> str:
        return self._base_url() + "/v1/chat/completions"

    def is_loaded(self) -> bool:
        return self.server_process is not None and self.server_process.poll() is None

    # ── load ──────────────────────────────────────────────────────────────
    def load_model(self) -> bool:
        with self._lock:
            if self.loading:
                return False
            self.loading = True

        model_path = self.cfg.get(self.model_path_key, "")
        if not model_path or not Path(model_path).exists():
            self.loading = False
            self.status_changed.emit("No model file configured")
            return False

        self.status_changed.emit("Starting server…")
        log(f"[{self.label}] Loading model: {model_path}")

        server_path = self.cfg.get("llama_server_path", str(LLAMA_CPP_DIR / SERVER_EXE))
        if not Path(server_path).exists():
            for name in [SERVER_EXE, "llama-server"]:
                candidate = LLAMA_CPP_DIR / name
                if candidate.exists():
                    server_path = str(candidate)
                    break
            else:
                self.loading = False
                self.status_changed.emit("llama-server not found")
                return False

        gpu_detected = has_nvidia()
        log(f"[{self.label}] GPU detection: has_nvidia()={gpu_detected}")
        gpu_layers = self.cfg.get("gpu_layers", 99)
        if not gpu_detected and gpu_layers > 0:
            log(f"[{self.label}] nvidia-smi not found but gpu_layers={gpu_layers} from config — attempting GPU (error recovery will retry CPU on failure)")
        log(f"[{self.label}] Using gpu_layers={gpu_layers}")
        ctx = self.cfg.get("context_size", 4096)
        host = self.cfg.get("server_host", "127.0.0.1")
        port = self.cfg.get("server_port", 8080)

        cmd = [
            server_path,
            "--model",
            model_path,
            "--ctx-size",
            str(ctx),
            "--n-gpu-layers",
            str(gpu_layers),
            "--host",
            host,
            "--port",
            str(port),
            "--reasoning", "off",
            "--no-warmup",
        ]

        try:
            kwargs: dict = {}
            if WINDOWS:
                kwargs["creationflags"] = 0x08000000

            # Ensure CUDA runtime DLLs are on PATH for GPU acceleration
            if WINDOWS and gpu_layers > 0:
                env = os.environ.copy()
                server_dir = str(Path(server_path).parent)
                cuda_search = [
                    server_dir,
                    os.path.expandvars(r"%ProgramFiles%\NVIDIA GPU Computing Toolkit\CUDA\v12.4\bin"),
                    os.path.expandvars(r"%ProgramFiles%\NVIDIA GPU Computing Toolkit\CUDA\v12.6\bin"),
                ]
                # Search for cudart64_12.dll in common locations
                for d in Path(server_dir).parent.iterdir():
                    if d.is_dir() and "cuda" in d.name.lower():
                        cuda_search.append(str(d))
                # Also check Ollama bundled CUDA
                ollama_cuda = Path(os.path.expandvars(r"%LOCALAPPDATA%")) / "Programs" / "Ollama" / "lib" / "ollama" / "cuda_v12"
                if ollama_cuda.exists():
                    cuda_search.append(str(ollama_cuda))
                # Search broader Ollama locations
                for p in [Path("E:/AI/AnythingLLM/resources/ollama/lib/ollama/cuda_v12")]:
                    if p.exists():
                        cuda_search.append(str(p))
                extra = [d for d in cuda_search if Path(d).exists() and d not in env.get("PATH", "")]
                if extra:
                    env["PATH"] = ";".join(extra) + ";" + env.get("PATH", "")
                    log(f"[{self.label}] Added CUDA paths to PATH: {extra}")
                kwargs["env"] = env

            # Clear any orphaned llama-server from a previous session
            try:
                if WINDOWS:
                    subprocess.run(
                        ["taskkill", "/F", "/IM", SERVER_EXE],
                        capture_output=True,
                        creationflags=0x08000000,
                        timeout=5,
                    )
            except Exception:
                pass

            self.log_file = open(LOG_FILE, "w", encoding="utf-8")
            self.server_process = subprocess.Popen(
                cmd, stdout=self.log_file, stderr=self.log_file, **kwargs
            )

            for i in range(180):
                if self.server_process.poll() is not None:
                    # Dump server log into app_debug.log for easier diagnosis
                    try:
                        tail = LOG_FILE.read_text(encoding="utf-8", errors="replace")[-2000:]
                        log(f"[{self.label}] server_log.txt tail:\n{tail}")
                    except Exception:
                        pass
                    raise RuntimeError("Server exited immediately — see server_log.txt")
                try:
                    if requests.get(self._health_url(), timeout=1).status_code == 200:
                        break
                except requests.RequestException:
                    pass
                if i and i % 15 == 0:
                    self.status_changed.emit(f"Loading… ({i}s)")
                time.sleep(1)
            else:
                raise RuntimeError("Server did not start within 180 s")

            self.last_used = datetime.now()
            self.loading = False
            name = friendly_name(model_path)
            self.status_changed.emit(f"Ready — {name}")
            self.model_loaded.emit()
            log(f"[{self.label}] Model ready: {name}")
            return True

        except Exception as e:
            log(f"[{self.label}] load_model failed: {e}")
            self.loading = False
            self.unload_model()
            if gpu_layers > 0 and any(
                kw in str(e).lower() for kw in ("cuda", "oom", "gpu")
            ):
                log(f"[{self.label}] CUDA error — retrying CPU-only")
                self.status_changed.emit("GPU error — retrying CPU…")
                orig = self.cfg.get("gpu_layers", 99)
                self.cfg.config["gpu_layers"] = 0
                result = self.load_model()
                self.cfg.config["gpu_layers"] = orig
                return result
            self.status_changed.emit(f"Load error: {str(e)[:70]}")
            return False

    def unload_model(self):
        with self._lock:
            if self.server_process:
                try:
                    self.server_process.terminate()
                    self.server_process.wait(timeout=5)
                except Exception:
                    try:
                        self.server_process.kill()
                    except Exception:
                        pass
                self.server_process = None
            if self.log_file:
                try:
                    self.log_file.close()
                except Exception:
                    pass
                self.log_file = None
            self.last_used = None
        self.status_changed.emit("Model unloaded")
        self.model_unloaded.emit()

    # ── correction (non-streaming, single method for all models) ──────────
    def correct_text(
        self,
        text: str,
        system: str | None = None,
        examples: list[dict] | None = None,
    ) -> str | None:
        """Correct text using the model via chat completions API.
        
        Uses comprehensive stripping and retry logic to handle models that
        add meta-commentary or thinking preambles.
        """
        if not self.is_loaded():
            if not self.load_model():
                return None
        self.last_used = datetime.now()
        self.status_changed.emit("Correcting…")

        # Get custom instruction or use default
        if not system:
            system = self.cfg.get("system_prompt", "").strip() or (
                "You are a text correction engine. Your task is to proofread and refine text.\n"
                "CRITICAL RULES - VIOLATING THESE IS AN ERROR:\n"
                "1. Output ONLY the corrected text - no explanations, no labels, no greetings\n"
                "2. NEVER start with phrases like 'Here is', 'Sure', 'Corrected', 'The corrected'\n"
                "3. NEVER wrap output in quotes or markdown\n"
                "4. If text is perfect, return it unchanged\n"
                "5. Fix spelling, grammar, punctuation while preserving meaning and tone\n"
                "6. PRESERVE ALL LINE BREAKS AND PARAGRAPH SPACING - do not remove blank lines\n"
                "7. Maintain original formatting exactly, including multiple line breaks"
            )

        messages = [{"role": "system", "content": system}]
        if examples:
            messages.extend(examples)
        messages.append({"role": "user", "content": text})

        # Give enough room for thinking overhead + corrections
        max_tokens = min(len(text.split()) * 3 + 500, 4096)

        payload = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": self.cfg.get("temperature", 0.1),
            "top_k": self.cfg.get("top_k", 40),
            "top_p": self.cfg.get("top_p", 0.95),
            "min_p": self.cfg.get("min_p", 0.05),
            "frequency_penalty": self.cfg.get("frequency_penalty", 0.0),
            "presence_penalty": self.cfg.get("presence_penalty", 0.0),
            "repeat_penalty": self.cfg.get("repeat_penalty", 1.0),
            "stream": False,
            "think": False,
        }

        url = self._chat_url()
        log(f"[{self.label}] POST {url} payload={json.dumps(payload)[:300]}")

        try:
            r = requests.post(url, json=payload, timeout=120)
            
            # Handle context too long error
            if r.status_code == 400:
                error_body = r.text.lower()
                if "context" in error_body or "too long" in error_body:
                    self.status_changed.emit("Error: text too long")
                    return "[Error] Text exceeds the model's context limit."
                r.raise_for_status()
            
            log(f"[{self.label}] Response received. Status: {r.status_code}")
            r.raise_for_status()
            
            result = r.json()
            log(f"[{self.label}] JSON parsed successfully")

            raw, finish_reason = _extract_content_from_response(result)
            # Thinking model consumed all tokens — no usable output
            if not raw and finish_reason == "length":
                log(f"[{self.label}] Thinking model used all tokens, no content produced")
                self.status_changed.emit("Error: model used all tokens on reasoning")
                return None

            # Strip thinking tokens (Qwen3 thinking mode)
            raw = strip_thinking_tokens(raw)
            # Strip meta-commentary ("Here is the corrected text:")
            raw = strip_meta_commentary(raw)

            # Check if output still contains conversational elements and retry if needed
            if contains_meta_commentary(raw):
                log(f"[{self.label}] Detected conversational output, retrying with stronger prompt...")
                self.status_changed.emit("Retrying...")

                # Retry with ultra-strict prompt
                retry_messages = [
                    {
                        "role": "system",
                        "content": (
                            "OUTPUT FORMAT: PLAIN TEXT ONLY. NO PREAMBLE. NO LABELS. NO EXPLANATIONS. "
                            "Task: Fix spelling, grammar, and punctuation in the following text. "
                            "PRESERVE ALL LINE BREAKS AND PARAGRAPH SPACING. "
                            "Output the corrected text and NOTHING else."
                        ),
                    },
                    {"role": "user", "content": f"Text to correct:\n{text}"},
                ]

                retry_payload = {
                    "messages": retry_messages,
                    "max_tokens": max_tokens,
                    "temperature": 0.0,  # Force deterministic
                    "top_k": 1,
                    "top_p": 0.1,  # Very focused sampling
                    "min_p": 0.05,
                    "frequency_penalty": 0.0,
                    "presence_penalty": 0.0,
                    "repeat_penalty": 1.0,
                    "stream": False,
                    "think": False,
                }

                retry_response = requests.post(url, json=retry_payload, timeout=120)
                retry_response.raise_for_status()

                retry_result = retry_response.json()
                raw, _ = _extract_content_from_response(retry_result)

                # Strip again
                raw = strip_thinking_tokens(raw)
                raw = strip_meta_commentary(raw)
                log(f"[{self.label}] Retry correction length: {len(raw)}")

            log(f"[{self.label}] Correction length (after strip): {len(raw)}")

            self.last_used = datetime.now()
            self.status_changed.emit("Ready")
            return raw if raw else text
            
        except requests.exceptions.ConnectionError:
            log(f"[{self.label}] Connection error in correct_text")
            self.status_changed.emit("Error: server unreachable")
            return "[Error] Cannot reach inference server. Make sure the model is loaded."
        except requests.exceptions.Timeout:
            log(f"[{self.label}] Timeout in correct_text")
            self.status_changed.emit("Error: timeout")
            return "[Error] Server took too long to respond."
        except Exception as e:
            log(f"[{self.label}] Error in correct_text: {e}")
            error_msg = str(e)
            if "500" in error_msg:
                self.status_changed.emit("Error: server error")
                return "[Error] Server error (500). Check server_log.txt."
            self.status_changed.emit(f"Error: {error_msg[:50]}")
            return None

    # ── patch-based correction (structured JSON output) ──────────────────
    def correct_text_patch(
        self,
        text: str,
        system: str | None = None,
        examples: list[dict] | None = None,
    ) -> str | None:
        """Return corrected text by asking the LLM for a JSON patch list.

        The LLM outputs only the words that need changing, dramatically reducing
        output tokens. Falls back to None on any parsing error so the caller
        can use correct_text() instead.

        Parameters:
            text: The text to correct.
            system: Complete system prompt (caller builds it per strength level).
            examples: Few-shot message pairs (caller builds per strength level).
        """
        if not self.is_loaded():
            if not self.load_model():
                return None
        self.last_used = datetime.now()
        self.status_changed.emit("Correcting…")

        if not system:
            system = (
                "You are a text correction engine. You output ONLY a JSON array of patches.\n"
                "Each patch object has exactly two keys:\n"
                '  "old" — the wrong word or short phrase as it appears in the text\n'
                '  "new" — the corrected replacement\n\n'
                "Rules:\n"
                "- Only include words that are actually wrong\n"
                "- Preserve correct words exactly as-is — do NOT include them in patches\n"
                "- Keep each patch short: 1-3 words max for old/new\n"
                "- If the text is already perfect, output an empty array []\n"
                "- Output ONLY the JSON array, nothing else\n"
            )

        messages = [{"role": "system", "content": system}]
        if examples:
            messages.extend(examples)
        messages.append({"role": "user", "content": text})

        payload = {
            "messages": messages,
            "max_tokens": min(max(len(text.split()) * 2 + 30, 60), 512),
            "temperature": 0.0,
            "top_k": self.cfg.get("top_k", 40),
            "top_p": self.cfg.get("top_p", 0.95),
            "stream": False,
            "think": False,
        }

        try:
            log(f"[{self.label}] PATCH POST {self._chat_url()} payload={json.dumps(payload)[:300]}")
            r = requests.post(self._chat_url(), json=payload, timeout=120)
            if not r.ok:
                log(f"[{self.label}] HTTP {r.status_code} — body: {r.text[:500]}")
            r.raise_for_status()
            resp = r.json()
            log(f"[{self.label}] Patch raw response: {json.dumps(resp)[:500]}")

            raw, finish_reason = _extract_content_from_response(resp)
            log(f"[{self.label}] Patch raw content: {repr(raw[:300])}, finish_reason={finish_reason}")

            # Thinking model consumed all tokens — no usable output
            if not raw and finish_reason == "length":
                log(f"[{self.label}] finish_reason=length with empty content — falling back")
                return None

            patches = _extract_patches_from_response(raw)

            if patches is None:
                log(f"[{self.label}] No valid patches extracted from response")
                return None

            # Filter out no-op patches where old == new
            real_patches = [
                p
                for p in patches
                if p.get("old", "").strip() != p.get("new", "").strip()
            ]
            if not real_patches:
                log(f"[{self.label}] No corrections needed — text is already correct")
                return text

            log(
                f"[{self.label}] Extracted {len(real_patches)} real patches (out of {len(patches)} total): {real_patches}"
            )
            result = _apply_patches(text, real_patches)
            log(f"[{self.label}] Patch result: {repr(result[:200])}")

            # Patches exist but none applied — fall back to full-text
            if result == text and len(real_patches) > 0:
                log(f"[{self.label}] No patches applied successfully — falling back to full text")
                return None

            self.last_used = datetime.now()
            self.status_changed.emit("Ready")
            return result
        except Exception as e:
            log(f"[{self.label}] correct_text_patch error: {e}")
            self.status_changed.emit(f"Error: {str(e)[:50]}")
            return None

    # ── chat with model (non-streaming) ───────────────────────────────────
    def chat_with_model(
        self,
        messages: list,
        max_tokens: int = 1000,
    ) -> str | None:
        """Chat with the model for text refinement via chat completions API.
        
        Uses the same comprehensive stripping logic as correct_text().
        """
        if not self.is_loaded():
            if not self.load_model():
                return None
        self.last_used = datetime.now()
        self.status_changed.emit("Thinking...")

        payload = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": self.cfg.get("temperature", 0.1),
            "top_k": self.cfg.get("top_k", 40),
            "top_p": self.cfg.get("top_p", 0.95),
            "min_p": self.cfg.get("min_p", 0.05),
            "frequency_penalty": self.cfg.get("frequency_penalty", 0.0),
            "presence_penalty": self.cfg.get("presence_penalty", 0.0),
            "repeat_penalty": self.cfg.get("repeat_penalty", 1.0),
            "stream": False,
        }

        url = self._chat_url()
        log(f"[{self.label}] chat_with_model: POST {url}")

        try:
            r = requests.post(url, json=payload, timeout=120)
            log(f"[{self.label}] chat_with_model: Response {r.status_code}")
            r.raise_for_status()

            result = r.json()
            reply = (
                result.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )

            # Strip thinking tokens
            reply = strip_thinking_tokens(reply)
            # Strip meta-commentary
            reply = strip_meta_commentary(reply)
            log(f"[{self.label}] chat_with_model: Reply length (after strip) {len(reply)}")

            self.last_used = datetime.now()
            self.status_changed.emit("Ready")
            return reply

        except requests.exceptions.ConnectionError:
            log(f"[{self.label}] Connection error in chat_with_model")
            self.status_changed.emit("Error: server unreachable")
            return None
        except requests.exceptions.Timeout:
            log(f"[{self.label}] Timeout in chat_with_model")
            self.status_changed.emit("Error: timeout")
            return None
        except Exception as e:
            log(f"[{self.label}] Error in chat_with_model: {e}")
            self.status_changed.emit(f"Error: {str(e)[:50]}")
            return None

    # ── streaming chat ─────────────────────────────────────────────────────
    def make_stream_worker(
        self, messages: list, max_tokens: int = 1024
    ) -> StreamWorker:
        payload = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": self.cfg.get("temperature", 0.3),
            "top_k": self.cfg.get("top_k", 40),
            "top_p": self.cfg.get("top_p", 0.95),
            "think": False,
        }
        return StreamWorker(self._chat_url(), payload)

    # ── idle check ─────────────────────────────────────────────────────────
    def check_idle(self):
        if self.cfg.get(self.keep_loaded_key, True):
            log(f"[{self.label}] keep_model_loaded=True — skipping idle check")
            return
        if not self.is_loaded() or not self.last_used:
            return
        idle = (datetime.now() - self.last_used).total_seconds()
        timeout = self.cfg.get(self.idle_timeout_key, 300)
        if idle >= timeout:
            log(f"[{self.label}] Idle {idle:.0f}s — unloading")
            self.unload_model()


# ═══════════════════════════════════════════════════════════════════════════════
# Theme
# ═══════════════════════════════════════════════════════════════════════════════

THEME = """
/* ── Global ─────────────────────────────────────────────────────────── */
QWidget {
    font-family: 'Segoe UI', 'SF Pro Display', 'Inter', system-ui, sans-serif;
    font-size: 13px;
    color: #e2e8f0;
}
/* ── Main window card ───────────────────────────────────────────────── */
QWidget#card {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #060d1f, stop:0.6 #0a1628, stop:1 #060d1f);
    border: 1px solid rgba(59,130,246,0.18);
    border-radius: 14px;
}
/* ── Text areas ─────────────────────────────────────────────────────── */
QTextEdit {
    background: rgba(4,10,28,0.75);
    border: 1px solid rgba(59,130,246,0.18);
    border-radius: 10px;
    padding: 10px 12px;
    color: #e2e8f0;
    selection-background-color: rgba(59,130,246,0.35);
    line-height: 1.5;
}
QTextEdit:focus {
    border: 1px solid rgba(96,165,250,0.55);
    background: rgba(4,10,28,0.9);
}
/* ── Line edit ──────────────────────────────────────────────────────── */
QLineEdit {
    background: rgba(4,10,28,0.75);
    border: 1px solid rgba(59,130,246,0.18);
    border-radius: 8px;
    padding: 8px 12px;
    color: #e2e8f0;
}
QLineEdit:focus { border: 1px solid rgba(96,165,250,0.55); }
/* ── Primary button ─────────────────────────────────────────────────── */
QPushButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #1d4ed8, stop:1 #3b82f6);
    border: 1px solid rgba(96,165,250,0.3);
    border-radius: 8px;
    padding: 9px 18px;
    color: #fff;
    font-weight: 600;
    font-size: 13px;
}
QPushButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #1e40af, stop:1 #2563eb);
    border: 1px solid rgba(96,165,250,0.5);
}
QPushButton:pressed {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #1e3a8a, stop:1 #1d4ed8);
}
QPushButton:disabled {
    background: rgba(255,255,255,0.04);
    color: rgba(255,255,255,0.22);
    border: 1px solid rgba(255,255,255,0.06);
}
/* ── Secondary / ghost button ───────────────────────────────────────── */
QPushButton#ghost {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.1);
    color: #94a3b8;
}
QPushButton#ghost:hover {
    background: rgba(255,255,255,0.08);
    border: 1px solid rgba(255,255,255,0.2);
    color: #e2e8f0;
}
/* ── Danger button ──────────────────────────────────────────────────── */
QPushButton#danger {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #991b1b,stop:1 #dc2626);
    border: 1px solid rgba(248,113,113,0.3);
}
QPushButton#danger:hover {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #7f1d1d,stop:1 #b91c1c);
}
/* ── Labels ─────────────────────────────────────────────────────────── */
QLabel { color: #e2e8f0; background: transparent; }
QLabel#title { font-size: 20px; font-weight: 700; color: #f1f5f9; letter-spacing: -0.3px; }
QLabel#tag   { font-size: 10px; font-weight: 700; color: #60a5fa;
               letter-spacing: 1.8px; text-transform: uppercase; }
QLabel#dim   { color: #64748b; font-size: 12px; }
QLabel#status { color: #94a3b8; font-size: 11px; padding: 3px 10px;
                background: rgba(255,255,255,0.04); border-radius: 10px;
                border: 1px solid rgba(255,255,255,0.06); }
/* ── Separator ──────────────────────────────────────────────────────── */
QFrame#sep { background: rgba(59,130,246,0.12); max-height: 1px; }
/* ── Chat bubble area ───────────────────────────────────────────────── */
QWidget#chatPanel {
    background: rgba(3,7,20,0.6);
    border: 1px solid rgba(59,130,246,0.12);
    border-radius: 10px;
}
/* ── Combo box ──────────────────────────────────────────────────────── */
QComboBox {
    background: rgba(4,10,28,0.75);
    border: 1px solid rgba(59,130,246,0.18);
    border-radius: 8px;
    padding: 6px 12px;
    color: #e2e8f0;
}
QComboBox:hover { border: 1px solid rgba(96,165,250,0.4); }
QComboBox QAbstractItemView {
    background: #0a1628;
    color: #e2e8f0;
    selection-background-color: #1d4ed8;
    border: 1px solid rgba(59,130,246,0.3);
}
/* ── Checkbox ───────────────────────────────────────────────────────── */
QCheckBox { color: #cbd5e1; spacing: 8px; }
QCheckBox::indicator { width: 17px; height: 17px; border-radius: 4px;
                        border: 1px solid rgba(255,255,255,0.15);
                        background: rgba(4,10,28,0.75); }
QCheckBox::indicator:checked {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #1d4ed8,stop:1 #3b82f6);
    border: 1px solid rgba(96,165,250,0.4);
}
/* ── Spin box ───────────────────────────────────────────────────────── */
QSpinBox, QDoubleSpinBox {
    background: rgba(4,10,28,0.75);
    border: 1px solid rgba(59,130,246,0.18);
    border-radius: 8px;
    padding: 6px 10px;
    color: #e2e8f0;
}
/* ── Scrollbar ──────────────────────────────────────────────────────── */
QScrollBar:vertical { background: transparent; width: 5px; border-radius: 3px; }
QScrollBar::handle:vertical { background: rgba(96,165,250,0.2); border-radius: 3px; min-height: 20px; }
QScrollBar::handle:vertical:hover { background: rgba(96,165,250,0.4); }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { height: 0; }
"""


# ═══════════════════════════════════════════════════════════════════════════════
# Settings dialog
# ═══════════════════════════════════════════════════════════════════════════════


class SettingsDialog(QDialog):
    saved = pyqtSignal()

    def __init__(self, cfg: ConfigManager, parent=None, re_register_cb=None):
        super().__init__(parent)
        self.cfg = cfg
        self._re_register_cb = re_register_cb
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self._drag_pos = None
        self.setMinimumSize(580, 680)
        self.resize(680, 820)
        self._build_ui()
        self._load()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(e.pos())
            if child is None or isinstance(child, QLabel):
                self._drag_pos = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e):
        if self._drag_pos and e.buttons() == Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None

    def _row(self, label: str, widget) -> QHBoxLayout:
        row = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setFixedWidth(130)
        lbl.setStyleSheet("color:#94a3b8;")
        row.addWidget(lbl)
        row.addWidget(widget, 1)
        return row

    def _browse_file(self, edit: QLineEdit, caption: str, filt: str):
        path, _ = QFileDialog.getOpenFileName(self, caption, "", filt)
        if path:
            edit.setText(path)

    def _build_ui(self):
        # Solid background so no click-through on Windows
        self.setStyleSheet(
            THEME
            + "\nSettingsDialog { background: #060d1f; border: 1px solid rgba(59,130,246,0.18); }"
            + _checkbox_css()
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 24)
        lay.setSpacing(16)

        tb = QHBoxLayout()
        title = QLabel("Settings")
        title.setObjectName("title")
        tb.addWidget(title)
        tb.addStretch()
        close = QPushButton("✕")
        close.setObjectName("ghost")
        close.setFixedSize(30, 30)
        close.setStyleSheet(
            "QPushButton{background:transparent;border:none;color:#64748b;font-size:16px;}"
            "QPushButton:hover{background:#7f1d1d;color:white;border-radius:6px;}"
        )
        close.clicked.connect(self.reject)
        tb.addWidget(close)
        lay.addLayout(tb)

        sep = QFrame()
        sep.setObjectName("sep")
        sep.setFrameShape(QFrame.Shape.HLine)
        lay.addWidget(sep)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollArea{background:#060d1f;border:none;}"
            "QScrollArea>QWidget>QWidget{background:#060d1f;}"
        )
        inner = QWidget()
        inner.setStyleSheet("QWidget{background:#060d1f;}")
        form = QVBoxLayout(inner)
        form.setSpacing(12)
        form.setContentsMargins(0, 0, 0, 0)

        def section(title_txt):
            lbl = QLabel(title_txt)
            lbl.setObjectName("tag")
            lbl.setStyleSheet(
                "QLabel{color:#60a5fa;font-size:10px;font-weight:700;"
                "letter-spacing:1.8px;margin-top:6px;}"
            )
            form.addWidget(lbl)

        # LLM Server ─────────────────────────────────────────────────────
        section("LLM SERVER")
        self.server_edit = QLineEdit()
        self.server_edit.setReadOnly(True)
        btn_s = QPushButton("Browse")
        btn_s.setObjectName("ghost")
        btn_s.setFixedWidth(80)
        btn_s.clicked.connect(
            lambda: self._browse_file(
                self.server_edit,
                "Select llama-server",
                "Executable (llama-server*);;All (*)",
            )
        )
        srv_row = QHBoxLayout()
        srv_row.addWidget(self.server_edit, 1)
        srv_row.addWidget(btn_s)
        srv_w = QWidget()
        srv_w.setLayout(srv_row)
        form.addLayout(self._row("Server binary", srv_w))

        # Chat model ─────────────────────────────────────────────────────
        self.model_edit = QLineEdit()
        self.model_edit.setReadOnly(True)
        btn_m = QPushButton("Browse")
        btn_m.setObjectName("ghost")
        btn_m.setFixedWidth(80)
        btn_m.clicked.connect(
            lambda: self._browse_file(
                self.model_edit, "Select GGUF model", "GGUF (*.gguf)"
            )
        )
        mod_row = QHBoxLayout()
        mod_row.addWidget(self.model_edit, 1)
        mod_row.addWidget(btn_m)
        mod_w = QWidget()
        mod_w.setLayout(mod_row)
        form.addLayout(self._row("Chat model", mod_w))

        self.recent_combo = QComboBox()
        self.recent_combo.currentTextChanged.connect(
            lambda t: self.model_edit.setText(t) if t else None
        )
        form.addLayout(self._row("Recent models", self.recent_combo))

        # Autocorrect model ──────────────────────────────────────────────
        self.ac_same_cb = QCheckBox("Same model as chat")
        self.ac_same_cb.setStyleSheet("color:#cbd5e1; spacing:8px;")
        form.addWidget(self.ac_same_cb)

        self.ac_model_edit = QLineEdit()
        self.ac_model_edit.setReadOnly(True)
        btn_ac = QPushButton("Browse")
        btn_ac.setObjectName("ghost")
        btn_ac.setFixedWidth(80)
        btn_ac.clicked.connect(
            lambda: self._browse_file(
                self.ac_model_edit, "Select autocorrect model", "GGUF (*.gguf)"
            )
        )
        ac_row = QHBoxLayout()
        ac_row.addWidget(self.ac_model_edit, 1)
        ac_row.addWidget(btn_ac)
        self.ac_row_w = QWidget()
        self.ac_row_w.setLayout(ac_row)
        form.addLayout(self._row("Autocorrect model", self.ac_row_w))

        self.port_spin = no_scroll(QSpinBox())
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setFixedWidth(100)
        form.addLayout(self._row("Port", self.port_spin))

        self.ctx_spin = no_scroll(QSpinBox())
        self.ctx_spin.setRange(512, 131072)
        self.ctx_spin.setSingleStep(512)
        self.ctx_spin.setFixedWidth(100)
        form.addLayout(self._row("Context size", self.ctx_spin))

        self.gpu_spin = no_scroll(QSpinBox())
        self.gpu_spin.setRange(0, 999)
        self.gpu_spin.setFixedWidth(100)
        form.addLayout(self._row("GPU layers", self.gpu_spin))

        self.temp_spin = no_scroll(QDoubleSpinBox())
        self.temp_spin.setRange(0.0, 2.0)
        self.temp_spin.setSingleStep(0.05)
        self.temp_spin.setDecimals(2)
        self.temp_spin.setFixedWidth(100)
        form.addLayout(self._row("Temperature", self.temp_spin))

        self.topk_spin = no_scroll(QSpinBox())
        self.topk_spin.setRange(0, 1000)
        self.topk_spin.setFixedWidth(100)
        form.addLayout(self._row("Top-K", self.topk_spin))

        self.topp_spin = no_scroll(QDoubleSpinBox())
        self.topp_spin.setRange(0.0, 1.0)
        self.topp_spin.setSingleStep(0.05)
        self.topp_spin.setDecimals(2)
        self.topp_spin.setFixedWidth(100)
        form.addLayout(self._row("Top-P", self.topp_spin))

        self.minp_spin = no_scroll(QDoubleSpinBox())
        self.minp_spin.setRange(0.0, 1.0)
        self.minp_spin.setSingleStep(0.01)
        self.minp_spin.setDecimals(2)
        self.minp_spin.setFixedWidth(100)
        form.addLayout(self._row("Min-P", self.minp_spin))

        self.keep_cb = QCheckBox("Keep chat model loaded in memory")
        form.addWidget(self.keep_cb)

        self.idle_spin = no_scroll(QSpinBox())
        self.idle_spin.setRange(30, 3600)
        self.idle_spin.setSingleStep(30)
        self.idle_spin.setFixedWidth(100)
        form.addLayout(self._row("Idle timeout (s)", self.idle_spin))

        # Hotkey ──────────────────────────────────────────────────────────
        section("HOTKEY")
        self.hotkey_edit = HotkeyEdit(re_register_cb=self._re_register_cb)
        form.addLayout(self._row("Trigger hotkey", self.hotkey_edit))

        # System prompt ───────────────────────────────────────────────────
        section("SYSTEM PROMPT  (override)")
        self.sysprompt_edit = QTextEdit()
        self.sysprompt_edit.setPlaceholderText(
            "Leave blank to use the built-in correction prompt."
        )
        self.sysprompt_edit.setFixedHeight(90)
        form.addWidget(self.sysprompt_edit)

        # Correction mode ─────────────────────────────────────────────────
        section("CORRECTION MODE")
        self.mode_combo = no_scroll(QComboBox())
        self.mode_combo.addItems([
            "Conservative — typos & obvious errors only",
            "Smart Fix — capitalization, punctuation & grammar",
        ])
        form.addLayout(self._row("Mode", self.mode_combo))

        form.addStretch()
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)

        sep2 = QFrame()
        sep2.setObjectName("sep")
        sep2.setFrameShape(QFrame.Shape.HLine)
        lay.addWidget(sep2)
        btns = QHBoxLayout()
        btns.setSpacing(8)
        btns.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setObjectName("ghost")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save settings")
        save.clicked.connect(self._save)
        btns.addWidget(cancel)
        btns.addWidget(save)
        lay.addLayout(btns)

        grip_row = QHBoxLayout()
        grip_row.addStretch()
        grip = QSizeGrip(self)
        grip.setFixedSize(16, 16)
        grip.setStyleSheet("QSizeGrip{background:transparent;}")
        grip_row.addWidget(grip)
        lay.addLayout(grip_row)

        # Wire up the "same as chat" toggle
        self.ac_same_cb.toggled.connect(self._on_ac_same_toggled)

    def _on_ac_same_toggled(self, checked: bool):
        self.ac_row_w.setEnabled(not checked)

    def _load(self):
        self.server_edit.setText(self.cfg.get("llama_server_path", ""))
        self.model_edit.setText(self.cfg.get("model_path", ""))
        recents = self.cfg.get("recent_models", [])
        self.recent_combo.addItems(recents)
        self.ac_same_cb.setChecked(self.cfg.get("ac_same_as_chat", True))
        self.ac_model_edit.setText(self.cfg.get("ac_model_path", ""))
        self.port_spin.setValue(self.cfg.get("server_port", 8080))
        self.ctx_spin.setValue(self.cfg.get("context_size", 4096))
        self.gpu_spin.setValue(self.cfg.get("gpu_layers", 99))
        self.temp_spin.setValue(self.cfg.get("temperature", 0.1))
        self.topk_spin.setValue(self.cfg.get("top_k", 40))
        self.topp_spin.setValue(self.cfg.get("top_p", 0.95))
        self.minp_spin.setValue(self.cfg.get("min_p", 0.05))
        self.keep_cb.setChecked(self.cfg.get("keep_model_loaded", True))
        self.idle_spin.setValue(self.cfg.get("idle_timeout_seconds", 300))
        self.hotkey_edit.setText(self.cfg.get("hotkey", "ctrl+shift+space"))
        self.sysprompt_edit.setPlainText(self.cfg.get("system_prompt", ""))
        try:
            _mode_idx = int(self.cfg.get("correction_mode", 0))
        except (TypeError, ValueError):
            _mode_idx = 0
        self.mode_combo.setCurrentIndex(_mode_idx)
        self._on_ac_same_toggled(self.ac_same_cb.isChecked())

    def _save(self):
        self.cfg.set("llama_server_path", self.server_edit.text())
        self.cfg.set("model_path", self.model_edit.text())
        self.cfg.set("ac_same_as_chat", self.ac_same_cb.isChecked())
        if self.ac_same_cb.isChecked():
            self.cfg.set("ac_model_path", self.model_edit.text())
        else:
            self.cfg.set("ac_model_path", self.ac_model_edit.text())
        self.cfg.set("server_port", self.port_spin.value())
        self.cfg.set("context_size", self.ctx_spin.value())
        self.cfg.set("gpu_layers", self.gpu_spin.value())
        self.cfg.set("temperature", self.temp_spin.value())
        self.cfg.set("top_k", self.topk_spin.value())
        self.cfg.set("top_p", self.topp_spin.value())
        self.cfg.set("min_p", self.minp_spin.value())
        self.cfg.set("keep_model_loaded", self.keep_cb.isChecked())
        self.cfg.set("idle_timeout_seconds", self.idle_spin.value())
        self.cfg.set("hotkey", self.hotkey_edit.text())
        self.cfg.set("system_prompt", self.sysprompt_edit.toPlainText().strip())
        self.cfg.set("correction_mode", self.mode_combo.currentIndex())
        model = self.model_edit.text()
        if model:
            self.cfg.add_recent(model)
        self.saved.emit()
        self.accept()


# ═══════════════════════════════════════════════════════════════════════════════
# Correction window
# ═══════════════════════════════════════════════════════════════════════════════


class CorrectionWindow(QWidget):
    """Main floating popup shown when the hotkey fires."""

    accepted = pyqtSignal(str)
    _correction_ready = pyqtSignal(str, str)
    _correction_failed = pyqtSignal()
    _chat_token = pyqtSignal(str)
    _chat_done = pyqtSignal(str)
    _chat_error = pyqtSignal(str)

    def __init__(
        self,
        original: str,
        ac_model: ModelManager,
        chat_model: ModelManager,
        cfg: ConfigManager,
        re_register_cb=None,
    ):
        super().__init__()
        self.original = original
        self.corrected = original
        self.ac_model = ac_model
        self.chat_model = chat_model
        self.cfg = cfg
        self._re_register_cb = re_register_cb or (lambda: None)
        self.chat_history: list[dict] = []
        self._stream_worker: StreamWorker | None = None
        self._stream_buf = ""
        self._drag_pos = None

        self._build_ui()
        self._position_window()
        self._connect_signals()
        self._setup_shortcuts()

        threading.Thread(target=self._do_correction, daemon=True).start()

    def _position_window(self):
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        sr = screen.geometry()
        w, h = min(740, int(sr.width() * 0.8)), min(860, int(sr.height() * 0.9))
        self.resize(w, h)
        cx, cy = QCursor.pos().x(), QCursor.pos().y()
        x = max(sr.x(), min(cx - w // 2, sr.right() - w))
        y = max(sr.y(), min(cy - h // 2, sr.bottom() - h))
        self.move(x, y)

    def _connect_signals(self):
        self._correction_ready.connect(self._on_correction_ready)
        self._correction_failed.connect(self._on_correction_failed)
        self._chat_token.connect(self._on_chat_token)
        self._chat_done.connect(self._on_chat_done)
        self._chat_error.connect(self._on_chat_error)
        self.ac_model.status_changed.connect(self._on_model_status)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            ch = self.childAt(e.pos())
            if ch is None or isinstance(ch, QLabel):
                self._drag_pos = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e):
        if self._drag_pos and e.buttons() == Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None

    def _setup_shortcuts(self):
        from PyQt6.QtGui import QShortcut, QKeySequence

        sc_accept = QShortcut(QKeySequence("Ctrl+Return"), self)
        sc_accept.activated.connect(self._accept_if_ready)
        sc_esc = QShortcut(QKeySequence("Escape"), self)
        sc_esc.activated.connect(self.close)

    def _accept_if_ready(self):
        if self.accept_btn.isEnabled():
            self._accept()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.chat_input.hasFocus():
                self._send_chat()
                return
            elif not self.corr_edit.hasFocus() and self.accept_btn.isEnabled():
                self._accept()
                return
        super().keyPressEvent(e)

    def _make_sep(self):
        f = QFrame()
        f.setObjectName("sep")
        f.setFrameShape(QFrame.Shape.HLine)
        return f

    def _build_ui(self):
        self.setWindowTitle("TextCorrector")
        self.setMinimumSize(500, 560)
        self.setStyleSheet(THEME)

        card = QWidget()
        card.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(12)

        hdr = QHBoxLayout()
        hdr.setSpacing(10)
        title = QLabel("TextCorrector")
        title.setObjectName("title")
        hdr.addWidget(title)
        hdr.addStretch()

        self.method_badge = QLabel("")
        self.method_badge.setObjectName("status")
        self.method_badge.hide()
        hdr.addWidget(self.method_badge)

        self.status_lbl = QLabel("⏳  Correcting…")
        self.status_lbl.setObjectName("status")
        hdr.addWidget(self.status_lbl)

        settings_btn = QPushButton("⚙")
        settings_btn.setObjectName("ghost")
        settings_btn.setFixedSize(30, 30)
        settings_btn.setStyleSheet(
            "QPushButton{background:rgba(255,255,255,0.04);"
            "border:1px solid rgba(255,255,255,0.08);border-radius:8px;font-size:15px;padding:0;}"
            "QPushButton:hover{background:rgba(255,255,255,0.1);}"
        )
        settings_btn.clicked.connect(self._open_settings)
        hdr.addWidget(settings_btn)
        lay.addLayout(hdr)
        lay.addWidget(self._make_sep())

        orig_lbl = QLabel("ORIGINAL")
        orig_lbl.setObjectName("tag")
        lay.addWidget(orig_lbl)
        self.orig_edit = QTextEdit()
        self.orig_edit.setPlainText(self.original)
        self.orig_edit.setReadOnly(True)
        self.orig_edit.setMinimumHeight(65)
        lay.addWidget(self.orig_edit)

        corr_lbl = QLabel("CORRECTED")
        corr_lbl.setObjectName("tag")
        lay.addWidget(corr_lbl)
        self.corr_edit = QTextEdit()
        self.corr_edit.setPlaceholderText("Processing…")
        self.corr_edit.setReadOnly(True)
        self.corr_edit.setMinimumHeight(160)
        lay.addWidget(self.corr_edit, 1)

        lay.addWidget(self._make_sep())

        chat_panel = QWidget()
        chat_panel.setObjectName("chatPanel")
        cp_lay = QVBoxLayout(chat_panel)
        cp_lay.setContentsMargins(14, 10, 14, 10)
        cp_lay.setSpacing(8)

        chat_lbl = QLabel("ASK AI")
        chat_lbl.setObjectName("tag")
        cp_lay.addWidget(chat_lbl)

        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        self.chat_display.setPlaceholderText("Chat with the AI for further changes…")
        self.chat_display.setMinimumHeight(45)
        self.chat_display.setMaximumHeight(130)
        self.chat_display.setStyleSheet(
            "QTextEdit{background:rgba(2,6,18,0.6);border:none;border-radius:8px;padding:8px;}"
        )
        cp_lay.addWidget(self.chat_display)

        ci_row = QHBoxLayout()
        ci_row.setSpacing(8)
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText(
            "e.g. 'Make it more formal', 'Shorter', 'Fix only spelling'…"
        )
        self.chat_input.returnPressed.connect(self._send_chat)
        ci_row.addWidget(self.chat_input, 1)
        self.send_btn = QPushButton("Send")
        self.send_btn.setFixedWidth(72)
        self.send_btn.setEnabled(False)
        self.send_btn.clicked.connect(self._send_chat)
        ci_row.addWidget(self.send_btn)
        cp_lay.addLayout(ci_row)

        # Template buttons row
        tmpl_sc = QScrollArea()
        tmpl_sc.setWidgetResizable(True)
        tmpl_sc.setFixedHeight(36)
        tmpl_sc.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
            "QScrollBar:horizontal{height:0;}"
        )
        self.tmp_w = QWidget()
        self.tmp_w.setStyleSheet("background:transparent;")
        self.tmp_lay = QHBoxLayout(self.tmp_w)
        self.tmp_lay.setContentsMargins(0, 0, 0, 0)
        self.tmp_lay.setSpacing(6)
        self.tmp_lay.setAlignment(Qt.AlignmentFlag.AlignLeft)
        tmpl_sc.setWidget(self.tmp_w)
        cp_lay.addWidget(tmpl_sc)
        self._refresh_templates()

        lay.addWidget(chat_panel)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.accept_btn = QPushButton("✓  Accept & Paste")
        self.accept_btn.setEnabled(False)
        self.accept_btn.clicked.connect(self._accept)
        btn_row.addWidget(self.accept_btn, 2)

        self.copy_btn = QPushButton("Copy")
        self.copy_btn.setObjectName("ghost")
        self.copy_btn.setEnabled(False)
        self.copy_btn.clicked.connect(self._copy)
        btn_row.addWidget(self.copy_btn)

        reset_btn = QPushButton("Reset")
        reset_btn.setObjectName("ghost")
        reset_btn.clicked.connect(self._reset)
        btn_row.addWidget(reset_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("danger")
        cancel_btn.clicked.connect(self.close)
        btn_row.addWidget(cancel_btn)

        lay.addLayout(btn_row)

        grip_row = QHBoxLayout()
        grip_row.addStretch()
        grip = QSizeGrip(self)
        grip.setFixedSize(16, 16)
        grip.setStyleSheet("QSizeGrip{background:transparent;}")
        grip_row.addWidget(grip)
        lay.addLayout(grip_row)

    # ── templates ─────────────────────────────────────────────────────────
    def _refresh_templates(self):
        while self.tmp_lay.count():
            w = self.tmp_lay.takeAt(0).widget()
            if w:
                w.deleteLater()

        core_templates = [
            ("📧 Email", "Rewrite this as a professional email with a proper greeting, clear body, and closing. Keep the core message."),
            ("💬 Social", "Rewrite this as a social media post. Keep the casual tone, original capitalization style, and personality. Make it engaging but don't over-polish it."),
            ("📝 Formal", "Rewrite this in formal English. Expand contractions, use complete sentences, maintain professional tone."),
            ("⚡ Tighten", "Optimize this text. Make it tighter, straight to the point, without cutting details or meaning."),
            ("📢 Headline", "Rewrite this as a punchy, engaging headline or short tagline. Title case."),
        ]
        custom_templates = self.cfg.get("custom_templates", [])

        btn_style = (
            "QPushButton{font-size:11px;padding:4px 10px;border-radius:6px;"
            "background:rgba(255,255,255,0.05);color:#cbd5e1;"
            "border:1px solid rgba(255,255,255,0.08);}"
            "QPushButton:hover{background:rgba(59,130,246,0.15);color:#93c5fd;}"
        )

        def _make_btn(name, prompt):
            b = QPushButton(name)
            b.setObjectName("ghost")
            b.setStyleSheet(btn_style)
            b.clicked.connect(lambda _, p=prompt: self._apply_template(p))
            return b

        for name, prompt in core_templates:
            self.tmp_lay.addWidget(_make_btn(name, prompt))
        for ct in custom_templates:
            self.tmp_lay.addWidget(_make_btn(ct.get("name", "Custom"), ct.get("prompt", "")))

        add_btn = QPushButton("➕ Add")
        add_btn.setObjectName("ghost")
        add_btn.setStyleSheet(
            "QPushButton{font-size:11px;padding:4px 10px;border-radius:6px;"
            "color:#94a3b8;border:1px dashed rgba(255,255,255,0.12);background:transparent;}"
            "QPushButton:hover{background:rgba(255,255,255,0.05);}"
        )
        add_btn.clicked.connect(self._add_custom_template)
        self.tmp_lay.addWidget(add_btn)

    def _apply_template(self, prompt: str):
        if self._stream_worker and self._stream_worker.isRunning():
            self._stream_worker.stop()
            self._stream_worker.wait(500)
        self.chat_display.clear()
        self.chat_history.clear()
        self.chat_input.setText(prompt)
        self._send_chat()

    def _add_custom_template(self):
        from PyQt6.QtWidgets import QInputDialog
        name, ok1 = QInputDialog.getText(self, "New Template", "Template name (e.g. 🤓 Fun):")
        if not ok1 or not name.strip():
            return
        prompt, ok2 = QInputDialog.getText(self, "New Template", "AI prompt (e.g. Rewrite this as a fun joke):")
        if not ok2 or not prompt.strip():
            return
        customs = self.cfg.get("custom_templates", [])
        customs.append({"name": name.strip(), "prompt": prompt.strip()})
        self.cfg.set("custom_templates", customs)
        self._refresh_templates()

    # ── correction logic ──────────────────────────────────────────────────
    def _on_model_status(self, msg: str):
        ml = msg.lower()
        if "ready" in ml:
            return  # completion signal handles the final state
        elif "correcting" in ml:
            self.status_lbl.setText("⏳  Processing…")
            self.status_lbl.setStyleSheet("color:#94a3b8;font-size:11px;")
        elif "loading" in ml or "starting" in ml:
            self.status_lbl.setText("⏳  Loading model…")
            self.status_lbl.setStyleSheet("color:#f59e0b;font-size:11px;")
        elif "error" in ml or "failed" in ml or "not found" in ml:
            self.status_lbl.setText(f"⚠  {msg[:45]}")
            self.status_lbl.setStyleSheet("color:#f87171;font-size:11px;")

    def _do_correction(self):
        """Autocorrect using the LLM (autocorrect model).

        Tries patch-based correction first (structured JSON output, minimal tokens).
        Falls back to full-text correction if patch mode fails.
        """
        import traceback

        log("[CW] _do_correction started (LLM autocorrect)")
        try:
            text = self.original

            if not self.ac_model.is_loaded():
                self.ac_model.load_model()

            if not self.ac_model.is_loaded():
                log("[CW] Autocorrect model unavailable — showing original")
                self._correction_ready.emit(text, "No changes (model error)")
                return

            # ── Mode-aware correction ─────────────────────────────────
            try:
                mode = int(self.cfg.get("correction_mode", 0))
            except (TypeError, ValueError):
                mode = 0
            log(f"[CW] Correction mode: {mode}")

            custom_sys = self.cfg.get("system_prompt", "").strip()

            _EX1_INPUT = "the project were delayed because of bad wether"
            _EX2_INPUT = "i dont know if its gona work"

            if mode == 1:
                # Smart Fix mode: patch-based, aggressive, outputs only changed words
                patch_system = (
                    "You are a text correction engine. Output ONLY a JSON array of patches.\n"
                    "Each patch has exactly two keys: 'old' (wrong word/phrase) and 'new' (corrected replacement).\n\n"
                    "CRITICAL RULES:\n"
                    "- Fix ALL errors: spelling, grammar, capitalization, punctuation, apostrophes.\n"
                    "- Capitalize first letter of sentences, proper nouns, and 'I'.\n"
                    "- Add missing periods, question marks, commas.\n"
                    "- Fix apostrophes: dont→don't, its→it's, im→I'm, doesnt→doesn't.\n"
                    "- Include punctuation in 'new' value when adding to end of sentence.\n"
                    "- Only include words that need changing — omit correct words entirely.\n"
                    "- Keep patches short: 1-4 words max.\n"
                    "- If text is perfect, output [].\n"
                    "- Output ONLY the JSON array, nothing else."
                )
                patch_examples = [
                    {"role": "user", "content": _EX1_INPUT},
                    {"role": "assistant", "content": '[{"old": "the", "new": "The"}, {"old": "were", "new": "was"}, {"old": "wether", "new": "weather."}]'},
                    {"role": "user", "content": _EX2_INPUT},
                    {"role": "assistant", "content": '[{"old": "i", "new": "I"}, {"old": "dont", "new": "don\'t"}, {"old": "its", "new": "it\'s"}, {"old": "gona", "new": "gonna"}, {"old": "work", "new": "work."}]'},
                    {"role": "user", "content": "samsung released a new phone"},
                    {"role": "assistant", "content": '[{"old": "samsung", "new": "Samsung"}, {"old": "phone", "new": "phone."}]'},
                ]
                log("[CW] Smart Fix mode: patch-based correction")
                result = self.ac_model.correct_text_patch(
                    text, system=patch_system, examples=patch_examples
                )
                if result is not None:
                    if result == text:
                        self._correction_ready.emit(text, "Already correct")
                    else:
                        self._correction_ready.emit(result, "Smart Fix (patch)")
                    return
                # Patch failed — fall back to full-text with aggressive prompt
                log("[CW] Patch failed, falling back to full-text…")
                full_system = custom_sys or (
                    "You are a text correction engine.\n"
                    "OUTPUT ONLY the corrected text — no labels, no preamble, no explanations.\n"
                    "Fix ALL errors: spelling, grammar, capitalization, punctuation, apostrophes.\n"
                    "Preserve formatting, line breaks, and original tone.\n"
                    "If the text is already correct, return it unchanged."
                )
                full_examples = [
                    {"role": "user", "content": _EX1_INPUT},
                    {"role": "assistant", "content": "The project was delayed because of bad weather."},
                    {"role": "user", "content": _EX2_INPUT},
                    {"role": "assistant", "content": "I don't know if it's gonna work."},
                ]
                result = self.ac_model.correct_text(text, system=full_system, examples=full_examples)
                if result is not None and result.strip():
                    self._correction_ready.emit(result, "Smart Fix (full-text)")
                else:
                    self._correction_ready.emit(text, "No changes (model error)")

            else:
                # Conservative mode (default): full-text, typos and obvious errors only
                full_system = custom_sys or (
                    "You are a text correction engine.\n"
                    "OUTPUT ONLY the corrected text — no labels, no preamble, no explanations.\n"
                    "ONLY fix clear misspellings and obvious typos.\n"
                    "Do NOT change grammar, capitalization, punctuation, style, or word choice.\n"
                    "Preserve everything else exactly as written.\n"
                    "If the text has no typos, return it unchanged."
                )
                full_examples = [
                    {"role": "user", "content": _EX1_INPUT},
                    {"role": "assistant", "content": "the project were delayed because of bad weather"},
                    {"role": "user", "content": _EX2_INPUT},
                    {"role": "assistant", "content": "i dont know if its gonna work"},
                ]
                log("[CW] Conservative mode: full-text, typos only")
                result = self.ac_model.correct_text(text, system=full_system, examples=full_examples)
                if result is not None and result.strip():
                    self._correction_ready.emit(result, "Conservative")
                else:
                    self._correction_ready.emit(text, "No changes (model error)")

        except Exception as e:
            log(f"[CW] _do_correction CRASHED: {e}\n{traceback.format_exc()}")
            self._correction_failed.emit()

    def _on_correction_ready(self, corrected: str, method: str):
        self.corrected = corrected
        self._render_diff(corrected)
        self.status_lbl.setText("✓  Done")
        self.status_lbl.setStyleSheet("color:#4ade80;font-size:11px;")
        self.method_badge.setText(f"via {method}")
        self.method_badge.show()
        self.accept_btn.setEnabled(True)
        self.copy_btn.setEnabled(True)
        self.send_btn.setEnabled(True)

    def _on_correction_failed(self):
        self.status_lbl.setText("⚠  Could not correct")
        self.status_lbl.setStyleSheet("color:#f87171;font-size:11px;")
        self.corr_edit.setPlainText(self.original)
        self.corrected = self.original
        self.accept_btn.setEnabled(True)
        self.copy_btn.setEnabled(True)
        self.send_btn.setEnabled(True)

    def _render_diff(self, corrected: str):
        import difflib
        import html as _html

        # Use a placeholder so newlines survive the word-split/rejoin pipeline
        NL = "\x00NL\x00"

        def prep(text: str) -> list[str]:
            t = text.replace("\r\n", "\n").replace("\r", "\n")
            # Insert placeholder around newline sequences so they become tokens
            t = t.replace("\n", f" {NL} ")
            return t.split()

        orig_words = prep(self.original)
        corr_words = prep(corrected)
        sm = difflib.SequenceMatcher(None, orig_words, corr_words, autojunk=False)
        parts: list[str] = []
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            for w in corr_words[j1:j2]:
                if w == NL:
                    parts.append("<br>")
                elif tag == "equal":
                    parts.append(_html.escape(w) + " ")
                else:  # replace / insert
                    parts.append(
                        f'<span style="background:rgba(59,130,246,0.28);'
                        f'color:#93c5fd;border-radius:3px;padding:1px 2px;">'
                        f"{_html.escape(w)}</span> "
                    )
        html = "".join(parts)
        self.corr_edit.setHtml(
            f'<body style="color:#e2e8f0;font-family:Segoe UI,sans-serif;font-size:13px;">'
            f"{html}</body>"
        )

    # ── chat ──────────────────────────────────────────────────────────────
    def _send_chat(self):
        msg = self.chat_input.text().strip()
        if not msg:
            return
        self.chat_input.clear()
        self.send_btn.setEnabled(False)
        self.accept_btn.setEnabled(False)

        self.chat_display.append(
            f'<span style="color:#60a5fa;font-weight:600;">You:</span> {msg}'
        )
        self.chat_display.append(
            '<span style="color:#64748b;font-style:italic;">AI is thinking…</span>'
        )

        system = (
            "You are a helpful writing assistant. The user may ask you to rewrite, "
            "shorten, change tone, or otherwise modify the text. "
            "Respond with ONLY the new text unless the user explicitly asks a question."
        )
        if not self.chat_history:
            self.chat_history = [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": f"Here is the text to work with:\n\n{self.corrected}",
                },
                {
                    "role": "assistant",
                    "content": "Understood. I'm ready to help modify this text.",
                },
            ]
        self.chat_history.append({"role": "user", "content": msg})

        # When ac_same_as_chat=True, the AC server handles chat too — never kill it to load a second one
        if self.cfg.get("ac_same_as_chat", True) and self.ac_model.is_loaded():
            self._do_stream()
            return

        if not self.chat_model.is_loaded():
            self.chat_display.append(
                '<span style="color:#f59e0b;font-style:italic;">⏳ Loading chat model…</span>'
            )
            threading.Thread(target=self._load_then_send, daemon=True).start()
            return

        self._do_stream()

    def _load_then_send(self):
        self.chat_model.load_model()
        if self.chat_model.is_loaded():
            self._chat_token.emit("")
            QTimer.singleShot(0, self._do_stream)
        else:
            self._chat_error.emit("Chat model could not be loaded. Check Settings.")

    def _do_stream(self):
        self._stream_buf = ""
        # Route to AC model when ac_same_as_chat=True — it's already running on the same server
        backend = (
            self.ac_model
            if (self.cfg.get("ac_same_as_chat", True) and self.ac_model.is_loaded())
            else self.chat_model
        )
        worker = backend.make_stream_worker(self.chat_history, max_tokens=1024)
        worker.token.connect(self._chat_token)
        worker.done.connect(self._chat_done)
        worker.error.connect(self._chat_error)
        self._stream_worker = worker
        worker.start()

    def _on_chat_token(self, token: str):
        import html as _html

        self._stream_buf += token
        escaped = _html.escape(self._stream_buf).replace("\n", "<br>")
        from PyQt6.QtGui import QTextCursor

        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
        cursor.removeSelectedText()
        cursor.insertHtml(
            f'<span style="color:#e2e8f0;white-space:pre-wrap;">'
            f'<b style="color:#a78bfa;">AI:</b>&nbsp;{escaped}</span>'
        )
        self.chat_display.setTextCursor(cursor)
        self.chat_display.ensureCursorVisible()

    def _on_chat_done(self, full: str):
        full = strip_think(full)
        full = strip_preamble(full, self.corrected)
        self.chat_history.append({"role": "assistant", "content": full})
        self.corrected = full
        self._render_diff(full)
        self.method_badge.setText("via AI chat")
        self.send_btn.setEnabled(True)
        self.accept_btn.setEnabled(True)
        self.chat_display.append("")

    def _on_chat_error(self, err: str):
        self.chat_display.append(f'<span style="color:#f87171;">Error: {err}</span>')
        self.send_btn.setEnabled(True)
        self.accept_btn.setEnabled(True)

    # ── actions ──────────────────────────────────────────────────────────
    def _accept(self):
        text = self.corr_edit.toPlainText()
        self.corrected = text
        self.close()
        self.accepted.emit(text)

    def _copy(self):
        pyperclip.copy(self.corr_edit.toPlainText())

    def _reset(self):
        self.corrected = self.original
        self.corr_edit.setPlainText(self.original)
        self.chat_history.clear()
        self.status_lbl.setText("⏳  Correcting…")
        self.status_lbl.setStyleSheet("color:#94a3b8;font-size:11px;")
        self.method_badge.hide()

    def _open_settings(self):
        dlg = SettingsDialog(self.cfg, self, re_register_cb=self._re_register_cb)
        dlg.saved.connect(self._re_register_cb)
        dlg.exec()

    def closeEvent(self, e):
        try:
            self.ac_model.status_changed.disconnect(self._on_model_status)
        except Exception:
            pass
        if self._stream_worker and self._stream_worker.isRunning():
            self._stream_worker.stop()
            self._stream_worker.wait(500)
        super().closeEvent(e)


# ═══════════════════════════════════════════════════════════════════════════════
# Tray icon helper
# ═══════════════════════════════════════════════════════════════════════════════


def make_tray_icon(color: str) -> QIcon:
    logo_path = SCRIPT_DIR / "logo.png"
    if logo_path.exists():
        base = QPixmap(str(logo_path)).scaled(
            64,
            64,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    else:
        base = QPixmap(64, 64)
        base.fill(Qt.GlobalColor.transparent)
    result = QPixmap(64, 64)
    result.fill(Qt.GlobalColor.transparent)
    p = QPainter(result)
    p.drawPixmap(0, 0, base)
    p.setBrush(QColor(color))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(44, 44, 18, 18)
    p.end()
    return QIcon(result)


# ═══════════════════════════════════════════════════════════════════════════════
# Main application
# ═══════════════════════════════════════════════════════════════════════════════


class TextCorrectorApp(QApplication):
    _trigger = pyqtSignal(str)
    _notify = pyqtSignal(str, str)

    def __init__(self):
        super().__init__(sys.argv)
        self.setQuitOnLastWindowClosed(False)
        self.setApplicationName("TextCorrector")

        self.cfg = ConfigManager()
        _ac_path_boot = self.cfg.get("model_path", "") if self.cfg.get("ac_same_as_chat", True) else self.cfg.get("ac_model_path", "")
        log(f"[APP] Boot — ac_same_as_chat: {self.cfg.get('ac_same_as_chat', True)}")
        log(f"[APP] Boot — Autocorrect model: {_ac_path_boot}")
        log(f"[APP] Boot — Chat model: {self.cfg.get('model_path', '')}")
        log(f"[APP] Boot — keep_model_loaded: {self.cfg.get('keep_model_loaded', True)}")
        log(f"[APP] Boot — gpu_layers: {self.cfg.get('gpu_layers', 99)}")
        log(f"[APP] Boot — correction_mode: {self.cfg.get('correction_mode', 0)}")
        self.ac_model = ModelManager(
            self.cfg,
            model_path_key="ac_model_path",
            label="AC",
            keep_loaded_key="keep_model_loaded",
            idle_timeout_key="idle_timeout_seconds",
        )
        self.chat_model = ModelManager(
            self.cfg,
            model_path_key="model_path",
            label="Chat",
            keep_loaded_key="keep_model_loaded",
            idle_timeout_key="idle_timeout_seconds",
        )
        self._window: CorrectionWindow | None = None
        self._old_clip = ""

        self._trigger.connect(self._show_window)
        self._notify.connect(self._show_notify)
        self.ac_model.status_changed.connect(self._on_ac_status)
        self.chat_model.status_changed.connect(self._on_chat_status)
        self.ac_model.model_loaded.connect(lambda: self._set_tray_icon("#3b82f6"))
        self.chat_model.model_loaded.connect(lambda: self._set_tray_icon("#a78bfa"))
        self.chat_model.model_unloaded.connect(lambda: self._set_tray_icon("#475569"))

        self._build_tray()
        self._register_hotkey()

        self._idle_timer = QTimer()
        self._idle_timer.timeout.connect(self.chat_model.check_idle)
        self._idle_timer.start(60_000)

        # Load autocorrect model at boot
        if self.cfg.get("ac_same_as_chat", True):
            ac_path = self.cfg.get("model_path", "")
        else:
            ac_path = self.cfg.get("ac_model_path", "")
        if ac_path:
            threading.Thread(target=self.ac_model.load_model, daemon=True).start()

        # Check for llama.cpp updates 5 s after boot (non-blocking)
        self._update_checker: UpdateChecker | None = None
        QTimer.singleShot(5000, self._check_llama_update)

    def _build_tray(self):
        self.tray = QSystemTrayIcon(make_tray_icon("#475569"), self)
        self.tray.setToolTip("TextCorrector")
        self.tray.activated.connect(self._tray_activated)

        menu = QMenu()
        menu.setStyleSheet(
            "QMenu{background:#0a1628;border:1px solid rgba(59,130,246,0.25);border-radius:8px;"
            "padding:4px;color:#e2e8f0;font-size:13px;}"
            "QMenu::item{padding:8px 18px;border-radius:4px;}"
            "QMenu::item:selected{background:rgba(59,130,246,0.25);}"
            "QMenu::separator{height:1px;background:rgba(59,130,246,0.15);margin:4px 0;}"
        )

        self._status_action = QAction("Status: idle", self)
        self._status_action.setEnabled(False)
        menu.addAction(self._status_action)
        menu.addSeparator()

        llm_menu = menu.addMenu("LLM Model")
        llm_menu.setStyleSheet(menu.styleSheet())
        act_llm_load = QAction("Load chat model", self)
        act_llm_unload = QAction("Unload chat model", self)
        act_llm_browse = QAction("Browse GGUF…", self)
        act_llm_load.triggered.connect(
            lambda: threading.Thread(
                target=self.chat_model.load_model, daemon=True
            ).start()
        )
        act_llm_unload.triggered.connect(self.chat_model.unload_model)
        act_llm_browse.triggered.connect(self._browse_model)
        llm_menu.addAction(act_llm_load)
        llm_menu.addAction(act_llm_unload)
        llm_menu.addSeparator()
        llm_menu.addAction(act_llm_browse)
        self._rebuild_recent_menu(llm_menu)

        menu.addSeparator()
        act_settings = QAction("Settings…", self)
        act_settings.triggered.connect(self._open_settings)
        menu.addAction(act_settings)

        act_test = QAction("Test hotkey", self)
        act_test.triggered.connect(self._test_hotkey)
        menu.addAction(act_test)

        menu.addSeparator()
        if WINDOWS:
            act_startup = QAction("Add to startup", self)
            act_rmstart = QAction("Remove from startup", self)
            act_startup.triggered.connect(self._add_startup)
            act_rmstart.triggered.connect(self._rm_startup)
            menu.addAction(act_startup)
            menu.addAction(act_rmstart)
            menu.addSeparator()

        act_quit = QAction("Quit", self)
        act_quit.triggered.connect(self._quit)

        # Update checker — shown before Quit; text changes when update found
        self._update_action = QAction("Check for llama.cpp update", self)
        self._update_action.triggered.connect(self._check_llama_update)
        menu.addAction(self._update_action)
        menu.addSeparator()
        menu.addAction(act_quit)

        self.tray.setContextMenu(menu)
        self.tray.show()

    def _set_tray_icon(self, color: str):
        self.tray.setIcon(make_tray_icon(color))

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._open_settings()

    def _rebuild_recent_menu(self, parent: QMenu):
        parent.addSeparator()
        for path in self.cfg.get("recent_models", [])[:8]:
            act = QAction(friendly_name(path), self)
            act.triggered.connect(lambda checked, p=path: self._select_model(p))
            parent.addAction(act)

    def _on_ac_status(self, msg: str):
        self._status_action.setText(f"Autocorrect: {msg}")

    def _on_chat_status(self, msg: str):
        color = (
            "#a78bfa"
            if "ready" in msg.lower()
            else "#f59e0b"
            if "loading" in msg.lower() or "starting" in msg.lower()
            else "#ef4444"
            if "error" in msg.lower() or "failed" in msg.lower()
            else "#475569"
        )
        self._set_tray_icon(color)

    def _register_hotkey(self):
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass
        hk = self.cfg.get("hotkey", "ctrl+shift+space")
        try:
            keyboard.add_hotkey(hk, self._hotkey_fired, suppress=False)
            log(f"[Hotkey] Registered: {hk}")
        except Exception as e:
            log(f"[Hotkey] Failed to register '{hk}': {e}")
            self.tray.showMessage(
                "TextCorrector",
                f"Could not register hotkey '{hk}'. Try running as administrator.",
                QSystemTrayIcon.MessageIcon.Warning,
                4000,
            )

    def _hotkey_fired(self):
        log("[Hotkey] Fired")
        try:
            for k in ("ctrl", "shift", "alt"):
                try:
                    keyboard.release(k)
                except Exception:
                    pass
            time.sleep(0.1)

            self._old_clip = pyperclip.paste()
            pyperclip.copy("")
            time.sleep(0.05)
            keyboard.send("ctrl+c")

            selected = ""
            for _ in range(20):
                time.sleep(0.05)
                try:
                    clip = pyperclip.paste()
                    if clip:
                        selected = clip
                        break
                except Exception:
                    pass

            if selected.strip():
                self._trigger.emit(selected.strip())
            else:
                if self._old_clip:
                    pyperclip.copy(self._old_clip)
                self._notify.emit(
                    "No text selected. Select text first, then press the hotkey.",
                    "info",
                )
        except Exception as e:
            log(f"[Hotkey] Error: {e}")

    def _show_notify(self, msg: str, icon: str):
        ico = (
            QSystemTrayIcon.MessageIcon.Warning
            if icon == "warn"
            else QSystemTrayIcon.MessageIcon.Information
        )
        self.tray.showMessage("TextCorrector", msg, ico, 2500)

    def _show_window(self, text: str):
        log(f"[Window] _show_window called, text length={len(text)}")
        try:
            if self._window:
                self._window.close()

            self._window = CorrectionWindow(
                text,
                self.ac_model,
                self.chat_model,
                self.cfg,
                re_register_cb=self._register_hotkey,
            )
            self._window.accepted.connect(self._paste_text)
            self._window.show()
            self._window.raise_()
            self._window.activateWindow()
            log("[Window] Window shown successfully")
        except Exception as e:
            import traceback

            log(f"[Window] CRASH in _show_window: {e}\n{traceback.format_exc()}")

    def _paste_text(self, text: str):
        pyperclip.copy(text)
        time.sleep(0.15)
        keyboard.send("ctrl+v")
        time.sleep(0.1)
        if self._old_clip and self._old_clip != text:
            QTimer.singleShot(500, lambda: pyperclip.copy(self._old_clip))

    def _open_settings(self):
        dlg = SettingsDialog(self.cfg, re_register_cb=self._register_hotkey)
        dlg.saved.connect(self._on_settings_saved)
        dlg.exec()

    def _on_settings_saved(self):
        self._register_hotkey()
        # If autocorrect model changed, reload
        if self.cfg.get("ac_same_as_chat", True):
            ac_path = self.cfg.get("model_path", "")
        else:
            ac_path = self.cfg.get("ac_model_path", "")
        if self.ac_model.is_loaded():
            self.ac_model.unload_model()
        if ac_path:
            threading.Thread(target=self.ac_model.load_model, daemon=True).start()

    def _browse_model(self):
        path, _ = QFileDialog.getOpenFileName(
            None, "Select GGUF Model", "", "GGUF (*.gguf)"
        )
        if path:
            self._select_model(path)

    def _select_model(self, path: str):
        self.cfg.set("model_path", path)
        self.cfg.add_recent(path)
        self.chat_model.unload_model()
        # Reload autocorrect model if it shares the chat model
        if self.cfg.get("ac_same_as_chat", True):
            if self.ac_model.is_loaded():
                self.ac_model.unload_model()
            threading.Thread(target=self.ac_model.load_model, daemon=True).start()
        self.tray.showMessage(
            "TextCorrector",
            f"Model selected: {os.path.basename(path)}",
            QSystemTrayIcon.MessageIcon.Information,
            2500,
        )

    def _test_hotkey(self):
        self._trigger.emit(
            "The quick brown fox jumps over the lazy dog. Ths is a tset."
        )

    def _add_startup(self):
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE,
            )
            exe = sys.executable
            cmd = f'"{exe.replace("python.exe", "pythonw.exe") if Path(exe.replace("python.exe", "pythonw.exe")).exists() else exe}" "{__file__}"'
            winreg.SetValueEx(key, "TextCorrector", 0, winreg.REG_SZ, cmd)
            winreg.CloseKey(key)
            self.tray.showMessage(
                "TextCorrector",
                "Added to Windows startup.",
                QSystemTrayIcon.MessageIcon.Information,
                2500,
            )
        except Exception as e:
            self.tray.showMessage(
                "TextCorrector",
                f"Startup error: {e}",
                QSystemTrayIcon.MessageIcon.Warning,
                3000,
            )

    def _rm_startup(self):
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE,
            )
            try:
                winreg.DeleteValue(key, "TextCorrector")
            except FileNotFoundError:
                pass
            winreg.CloseKey(key)
            self.tray.showMessage(
                "TextCorrector",
                "Removed from startup.",
                QSystemTrayIcon.MessageIcon.Information,
                2500,
            )
        except Exception as e:
            self.tray.showMessage(
                "TextCorrector",
                f"Error: {e}",
                QSystemTrayIcon.MessageIcon.Warning,
                3000,
            )

    def _check_llama_update(self):
        """Start background update check. Safe to call multiple times."""
        if self._update_checker and self._update_checker.isRunning():
            return
        self._update_checker = UpdateChecker()
        self._update_checker.update_available.connect(self._on_update_available)
        self._update_checker.start()

    def _on_update_available(self, tag: str, build: int):
        local = _get_local_build_number()
        log(f"[Update] New llama.cpp available: {tag} (build {build}, you have {local})")
        self._update_action.setText(f"⬆️  llama.cpp {tag} available — run update.py --llama")
        self.tray.showMessage(
            "TextCorrector — Update available",
            f"llama.cpp {tag} is out (you have b{local}).\nRun: python update.py --llama",
            QSystemTrayIcon.MessageIcon.Information,
            8000,
        )

    def _quit(self):
        self.ac_model.unload_model()
        self.chat_model.unload_model()
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass
        self.quit()


# ═══════════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════════


def main():
    import traceback

    def _excepthook(exc_type, exc_value, exc_tb):
        msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        log(f"[UNCAUGHT EXCEPTION]\n{msg}")
        print(msg, file=sys.stderr)
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook

    def _thread_excepthook(args):
        msg = "".join(
            traceback.format_exception(
                args.exc_type, args.exc_value, args.exc_traceback
            )
        )
        log(f"[THREAD EXCEPTION in {args.thread}]\n{msg}")

    threading.excepthook = _thread_excepthook

    _lock_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _lock_sock.bind(("127.0.0.1", 47321))
    except OSError:
        print("TextCorrector is already running.")
        sys.exit(0)

    app = TextCorrectorApp()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
