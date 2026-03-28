import sys
import re
import difflib
import os
import threading
import time
import winreg
import subprocess
import json
import requests
from datetime import datetime
from pathlib import Path

# Enforce Qt scaling environment variables BEFORE importing PyQt
os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
os.environ["QT_SCALE_FACTOR_ROUNDING_POLICY"] = "PassThrough"

import keyboard
import pyperclip
from PyQt5.QtWidgets import (
    QApplication,
    QSystemTrayIcon,
    QMenu,
    QWidget,
    QVBoxLayout,
    QTextEdit,
    QPushButton,
    QLabel,
    QHBoxLayout,
    QFileDialog,
    QCheckBox,
    QDialog,
    QLineEdit,
    QComboBox,
    QMessageBox,
    QSplitter,
    QFrame,
    QAction,
    QActionGroup,
    QSizeGrip,
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject, QSettings, QThread, QPoint
from PyQt5.QtGui import QIcon, QPixmap, QCursor

# Get script directory for portable paths
if getattr(sys, 'frozen', False):
    SCRIPT_DIR = Path(sys.executable).parent.resolve()
else:
    SCRIPT_DIR = Path(__file__).parent.resolve()

CONFIG_FILE = SCRIPT_DIR / "config.json"
LLAMA_CPP_DIR = SCRIPT_DIR / "llama_cpp"
LOG_FILE = SCRIPT_DIR / "server_log.txt"
DEBUG_LOG = SCRIPT_DIR / "app_debug.log"


def log_debug(msg):
    """Log debug message to file"""
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {msg}\n")
    except:
        pass


def _has_nvidia_gpu() -> bool:
    """Return True if an NVIDIA GPU with a working driver is detected."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode == 0 and result.stdout.strip():
            log_debug(f"GPU detected: {result.stdout.strip().splitlines()[0]}")
            return True
    except Exception:
        pass
    log_debug("No NVIDIA GPU detected — will use CPU mode (gpu_layers=0)")
    return False


# Default system prompt — used when user hasn't set a custom one
DEFAULT_SYSTEM_PROMPT = (
    "You are a text correction engine. Your ONLY task is to proofread and refine text.\n\n"
    "CRITICAL RULES - FOLLOW EXACTLY:\n"
    "1. Output ONLY the corrected text - absolutely nothing else\n"
    "2. NEVER add: 'Here is', 'Sure', 'Corrected:', 'The corrected version', or ANY preamble\n"
    "3. NEVER add explanations, commentary, or questions\n"
    "4. NEVER wrap output in quotes, markdown, or code blocks\n"
    "5. If text is perfect, return it exactly as-is\n"
    "6. Fix only: spelling, grammar, punctuation, and minor word choice\n"
    "7. Maintain original meaning, tone, and style precisely\n"
    "8. PRESERVE ALL LINE BREAKS AND PARAGRAPH SPACING - do not remove blank lines\n"
    "9. Maintain original formatting exactly, including multiple line breaks"
)


def strip_thinking_tokens(text):
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


def strip_meta_commentary(text, original_text=""):
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
    # Only strip quotes if the model added them (not if original had quotes)
    if len(cleaned) > 2 and cleaned[0] == '"' and cleaned[-1] == '"':
        if not (original_text.startswith('"') and original_text.endswith('"')):
            cleaned = cleaned[1:-1]
    if len(cleaned) > 2 and cleaned[0] == "'" and cleaned[-1] == "'":
        if not (original_text.startswith("'") and original_text.endswith("'")):
            cleaned = cleaned[1:-1]
    # Strip markdown code blocks if wrapping the entire output
    if cleaned.startswith("```") and cleaned.endswith("```"):
        lines = cleaned.split("\n")
        if len(lines) >= 2:
            # Remove first and last lines (the ``` markers)
            cleaned = "\n".join(lines[1:-1])
    return cleaned.strip()


def contains_meta_commentary(text):
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

    # Check for multiple sentences that look like explanations
    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
    if len(sentences) > 3:
        # If there are many short sentences, might be commentary
        short_sentences = sum(1 for s in sentences if len(s.split()) < 5)
        if short_sentences > len(sentences) / 2:
            return True

    return False


# Default configuration
DEFAULT_CONFIG = {
    "llama_server_path": str(LLAMA_CPP_DIR / "llama-server.exe"),
    "model_path": "",
    "server_host": "127.0.0.1",
    "server_port": 8080,
    "hotkey": "ctrl+shift+space",
    "keep_model_loaded": True,
    "idle_timeout_seconds": 300,
    "context_size": 4096,
    "gpu_layers": 99,  # Full GPU offload (CUDA)
    "recent_models": [],
    "temperature": 0.0,  # 0.0 for deterministic outputs (prevents chatty responses)
    "top_k": 40,
    "top_p": 0.95,
    "min_p": 0.05,
    "frequency_penalty": 0.0,
    "presence_penalty": 0.0,
    "repeat_penalty": 1.0,
    "onnx_model_dir": "",  # ONNX model directory
}


def discover_models():
    """Find all .gguf files in the app directory"""
    models = []
    for f in SCRIPT_DIR.glob("*.gguf"):
        models.append(str(f))
    return sorted(models)


def friendly_model_name(path):
    """Get a friendly display name from a GGUF filename"""
    name = os.path.basename(path)
    name = name.replace(".gguf", "")
    # Common transformations
    name = name.replace("-it-", " IT ")
    name = name.replace("-F16", " (F16)")
    name = name.replace("-BF16", " (BF16)")
    name = name.replace("-Q4_K_M", " (Q4_K_M)")
    name = name.replace("-Q8_0", " (Q8)")
    name = name.replace("-IQ4_NL", " (IQ4)")
    return name


class ConfigManager:
    """Manages application configuration with JSON file"""

    def __init__(self):
        self.config = self.load_config()
        self._auto_detect_model()

    def _auto_detect_model(self):
        """Auto-detect a model if none is configured or the configured one doesn't exist"""
        model_path = self.config.get("model_path", "")
        if not model_path or not os.path.exists(model_path):
            models = discover_models()
            if models:
                self.config["model_path"] = models[0]
                # Also populate recent_models
                self.config["recent_models"] = models
                self.save_config()

    def load_config(self):
        """Load configuration from file or create default"""
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r") as f:
                    config = json.load(f)
                    # Merge with defaults to ensure all keys exist
                    for key, value in DEFAULT_CONFIG.items():
                        if key not in config:
                            config[key] = value
                    return config
            except Exception as e:
                print(f"Error loading config: {e}")
                return DEFAULT_CONFIG.copy()
        return DEFAULT_CONFIG.copy()

    def save_config(self):
        """Save configuration to file"""
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            print(f"Error saving config: {e}")

    def get(self, key, default=None):
        return self.config.get(key, default)

    def set(self, key, value):
        self.config[key] = value
        self.save_config()

    def add_recent_model(self, model_path):
        """Add a model to recent models list"""
        recent = self.config.get("recent_models", [])
        if model_path in recent:
            recent.remove(model_path)
        recent.insert(0, model_path)
        # Keep only last 10
        self.config["recent_models"] = recent[:10]
        self.save_config()


# ---------------------------------------------------------------------------
# Qt key constant → keyboard-lib name mapping
# ---------------------------------------------------------------------------
_QT_KEY_NAMES = {
    Qt.Key_Space: "space",
    Qt.Key_Return: "enter",
    Qt.Key_Enter: "enter",
    Qt.Key_Tab: "tab",
    Qt.Key_Backspace: "backspace",
    Qt.Key_Delete: "delete",
    Qt.Key_Escape: "escape",
    Qt.Key_Home: "home",
    Qt.Key_End: "end",
    Qt.Key_PageUp: "page up",
    Qt.Key_PageDown: "page down",
    Qt.Key_Left: "left",
    Qt.Key_Right: "right",
    Qt.Key_Up: "up",
    Qt.Key_Down: "down",
    Qt.Key_Insert: "insert",
    Qt.Key_F1: "f1", Qt.Key_F2: "f2", Qt.Key_F3: "f3", Qt.Key_F4: "f4",
    Qt.Key_F5: "f5", Qt.Key_F6: "f6", Qt.Key_F7: "f7", Qt.Key_F8: "f8",
    Qt.Key_F9: "f9", Qt.Key_F10: "f10", Qt.Key_F11: "f11", Qt.Key_F12: "f12",
    Qt.Key_Semicolon: ";",
    Qt.Key_Equal: "=",
    Qt.Key_Minus: "-",
    Qt.Key_BracketLeft: "[",
    Qt.Key_BracketRight: "]",
    Qt.Key_Backslash: "\\\\",
    Qt.Key_Apostrophe: "'",
    Qt.Key_Comma: ",",
    Qt.Key_Period: ".",
    Qt.Key_Slash: "/",
    Qt.Key_QuoteLeft: "`",
}

_MODIFIER_KEYS = {
    Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt,
    Qt.Key_Meta, Qt.Key_AltGr, Qt.Key_Super_L, Qt.Key_Super_R,
}


def _combo_to_display(combo: str) -> str:
    """Convert 'ctrl+shift+space' → 'Ctrl + Shift + Space' for display."""
    parts = combo.split("+")
    return " + ".join(p.capitalize() for p in parts)


class HotkeyEdit(QLineEdit):
    """Windows-style hotkey recorder.

    Click → enters recording mode (shows 'Press keys…', border pulses blue).
    Hold modifiers + press a key → captures the combo and exits.
    Escape → cancels and reverts to the previous shortcut.
    Modifier-only combos (e.g. just Ctrl) are rejected with a hint.
    Focus-out → auto-cancels recording.
    """

    shortcut_changed = pyqtSignal(str)  # emitted with keyboard-lib string

    _IDLE_STYLE = (
        "QLineEdit {"
        "  background-color: rgba(15, 23, 42, 0.6);"
        "  border: 1px solid rgba(255, 255, 255, 0.15);"
        "  border-radius: 8px;"
        "  padding: 8px 14px;"
        "  color: #f8fafc;"
        "  font-size: 13px;"
        "}"
        "QLineEdit:hover {"
        "  border: 1px solid rgba(56, 189, 248, 0.45);"
        "  cursor: pointer;"
        "}"
    )
    _RECORDING_STYLE = (
        "QLineEdit {"
        "  background-color: rgba(14, 165, 233, 0.12);"
        "  border: 2px solid rgba(56, 189, 248, 0.8);"
        "  border-radius: 8px;"
        "  padding: 8px 14px;"
        "  color: #38bdf8;"
        "  font-size: 13px;"
        "}"
    )

    def __init__(self, parent=None, re_register_cb=None):
        super().__init__(parent)
        self._combo = ""          # keyboard-lib format, e.g. 'ctrl+shift+space'
        self._recording = False
        self._re_register_cb = re_register_cb  # called on cancel to restore hotkey
        self.setReadOnly(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(self._IDLE_STYLE)
        self.setFocusPolicy(Qt.StrongFocus)
        self._update_display()

    # ------------------------------------------------------------------
    # Public API — mirrors QLineEdit so save/load code needs no changes
    # ------------------------------------------------------------------
    def text(self) -> str:
        return self._combo

    def setText(self, value: str):
        self._combo = value.lower().strip()
        self._recording = False
        self.setStyleSheet(self._IDLE_STYLE)
        self._update_display()

    # ------------------------------------------------------------------
    # Display helpers
    # ------------------------------------------------------------------
    def _update_display(self):
        """Refresh the visible label from self._combo."""
        if self._combo:
            super().setText(_combo_to_display(self._combo))
        else:
            super().setText("Click to record shortcut")

    def _set_display(self, label: str):
        """Update only the visible text, leaving self._combo untouched."""
        super().setText(label)

    # ------------------------------------------------------------------
    # Recording lifecycle
    # ------------------------------------------------------------------
    def _start_recording(self):
        self._recording = True
        self.setStyleSheet(self._RECORDING_STYLE)
        self._set_display("Press keys…")
        # Temporarily pause the global keyboard hook so it doesn't
        # swallow modifier+key combos before Qt sees them.
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass

    def _cancel_recording(self):
        self._recording = False
        self.setStyleSheet(self._IDLE_STYLE)
        self._update_display()
        # Re-register the hotkey that was unhooked when recording started
        if self._re_register_cb:
            try:
                self._re_register_cb()
            except Exception:
                pass

    def _finish_recording(self, combo: str):
        self._recording = False
        self._combo = combo
        self.setStyleSheet(self._IDLE_STYLE)
        self._update_display()
        self.shortcut_changed.emit(combo)

    # ------------------------------------------------------------------
    # Mouse / focus events
    # ------------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self._recording:
            self._start_recording()
            self.setFocus()
        else:
            super().mousePressEvent(event)

    def focusOutEvent(self, event):
        if self._recording:
            self._cancel_recording()
        super().focusOutEvent(event)

    # ------------------------------------------------------------------
    # Key capture
    # ------------------------------------------------------------------
    def keyPressEvent(self, event):
        if not self._recording:
            return  # read-only — ignore normal typing

        key = event.key()
        mods = event.modifiers()

        # Escape → cancel
        if key == Qt.Key_Escape:
            self._cancel_recording()
            return

        # Ignore standalone modifier presses — wait for the trigger key
        if key in _MODIFIER_KEYS:
            return

        # Build modifier prefix
        parts = []
        if mods & Qt.ControlModifier:
            parts.append("ctrl")
        if mods & Qt.ShiftModifier:
            parts.append("shift")
        if mods & Qt.AltModifier:
            parts.append("alt")

        # Require at least one modifier
        if not parts:
            self._set_display("Add Ctrl / Shift / Alt…")
            return

        # Resolve the trigger key name
        if key in _QT_KEY_NAMES:
            key_name = _QT_KEY_NAMES[key]
        else:
            key_text = event.text().lower()
            key_name = key_text if key_text else None

        if not key_name:
            return  # unknown key — stay in recording mode

        parts.append(key_name)
        self._finish_recording("+".join(parts))

    def keyReleaseEvent(self, event):
        # Intentionally suppressed while recording
        if not self._recording:
            super().keyReleaseEvent(event)


class SettingsDialog(QDialog):
    """Settings dialog for configuring the application"""

    settings_changed = pyqtSignal()

    def __init__(self, config_manager, parent=None, re_register_cb=None):
        super().__init__(parent)
        self.config = config_manager
        self._re_register_cb = re_register_cb
        
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.dragging = False
        self.drag_position = None
        
        self.setWindowTitle("Text Corrector Settings")
        
        # Lower minimum size to allow shrinking on smaller scaled displays
        self.setMinimumSize(450, 450)
        
        # Initialize ONNX directory edit
        self.onnx_dir_edit = None
        
        # Dynamically size to prevent being massive on smaller screens
        cursor_pos = QCursor.pos()
        screen = QApplication.screenAt(cursor_pos)
        if not screen:
            screen = QApplication.primaryScreen()
        screen_rect = screen.geometry()

        base_width = 700
        base_height = 750
        max_width = int(screen_rect.width() * 0.8)
        max_height = int(screen_rect.height() * 0.85)
        
        window_width = min(base_width, max_width)
        window_height = min(base_height, max_height)
        
        self.resize(window_width, window_height)
        
        self.setup_ui()
        self.load_settings()

    def setup_ui(self):
        # We need an outer layout and a styled main widget to handle translucent corners
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        
        main_widget = QWidget()
        main_widget.setObjectName("settingsMainWidget")
        outer_layout.addWidget(main_widget)
        
        layout = QVBoxLayout(main_widget)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)

        # Style - matching premium dark slate-blue theme
        self.setStyleSheet("""
            QWidget#settingsMainWidget {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #0f172a, stop:0.5 #1e293b, stop:1 #0f172a);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 16px;
                color: #f8fafc;
            }
            QLabel {
                color: #e2e8f0;
                font-size: 13px;
                background: transparent;
            }
            QLineEdit {
                background-color: rgba(15, 23, 42, 0.6);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 8px;
                padding: 8px 12px;
                color: #f8fafc;
                font-size: 13px;
            }
            QLineEdit:focus {
                border: 1px solid rgba(56, 189, 248, 0.6);
                background-color: rgba(15, 23, 42, 0.8);
            }
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #0ea5e9, stop:1 #38bdf8);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 8px;
                padding: 8px 18px;
                color: #ffffff;
                font-weight: 600;
                font-size: 13px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #0284c7, stop:1 #0ea5e9);
                border: 1px solid rgba(255, 255, 255, 0.3);
            }
            QCheckBox {
                color: #e2e8f0;
                font-size: 13px;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 4px;
                border: 1px solid rgba(255, 255, 255, 0.15);
                background: rgba(15, 23, 42, 0.6);
            }
            QCheckBox::indicator:checked {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #0ea5e9, stop:1 #38bdf8);
                border: 1px solid rgba(56, 189, 248, 0.5);
            }
            QComboBox {
                background-color: rgba(15, 23, 42, 0.6);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 8px;
                padding: 6px 12px;
                color: #f8fafc;
                font-size: 13px;
            }
            QComboBox:hover {
                border: 1px solid rgba(56, 189, 248, 0.5);
            }
            QComboBox::drop-down {
                border: none;
                padding-right: 8px;
            }
            QComboBox QAbstractItemView {
                background-color: #1e293b;
                color: #f8fafc;
                selection-background-color: #0ea5e9;
                selection-color: white;
                border: 1px solid rgba(255, 255, 255, 0.10);
            }
        """)

        # Custom Title Bar
        title_bar = QHBoxLayout()
        title_label = QLabel("Text Corrector Settings")
        title_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #f8fafc;")
        
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(30, 30)
        close_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                color: #94a3b8;
                font-size: 16px;
                font-weight: bold;
                padding: 0px;
            }
            QPushButton:hover {
                background: #ef4444;
                color: white;
                border-radius: 6px;
            }
        """)
        close_btn.clicked.connect(self.reject)
        
        title_bar.addWidget(title_label)
        title_bar.addStretch()
        title_bar.addWidget(close_btn)
        layout.addLayout(title_bar)

        # Llama Server Path
        server_layout = QHBoxLayout()
        server_layout.addWidget(QLabel("Llama Server:"))
        self.server_path_edit = QLineEdit()
        self.server_path_edit.setReadOnly(True)
        server_layout.addWidget(self.server_path_edit)
        browse_server_btn = QPushButton("Browse...")
        browse_server_btn.clicked.connect(self.browse_server)
        server_layout.addWidget(browse_server_btn)
        layout.addLayout(server_layout)

        # Model Path
        model_h_layout = QHBoxLayout()
        model_h_layout.addWidget(QLabel("Model File:"))
        self.model_path_edit = QLineEdit()
        self.model_path_edit.setReadOnly(True)
        model_h_layout.addWidget(self.model_path_edit)
        browse_model_btn = QPushButton("Browse...")
        browse_model_btn.clicked.connect(self.browse_model)
        model_h_layout.addWidget(browse_model_btn)
        layout.addLayout(model_h_layout)

        # ONNX Model Directory
        onnx_label = QLabel("ONNX Model Directory:")
        onnx_label.setStyleSheet("font-weight: bold; color: #ffffff;")
        settings_layout = QHBoxLayout()
        self.onnx_dir_edit = QLineEdit()
        self.onnx_dir_edit.setPlaceholderText("Path to ONNX model folder (e.g., onnx_models/grammar_t5/)")
        self.onnx_dir_edit.setStyleSheet("""
            QLineEdit {
                background: rgba(255, 255, 255, 0.1);
                border: 1px solid rgba(255, 255, 255, 0.2);
                border-radius: 4px;
                padding: 8px;
                color: #ffffff;
            }
            QLineEdit:focus {
                border: 1px solid rgba(100, 150, 255, 0.5);
                background: rgba(255, 255, 255, 0.15);
            }
        """)
        settings_layout.addWidget(self.onnx_dir_edit)

        onnx_browse_btn = QPushButton("Browse...")
        onnx_browse_btn.setCursor(QCursor(Qt.PointingHandCursor))
        onnx_browse_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #4a9eff, stop:1 #0066cc);
                border: none;
                border-radius: 4px;
                padding: 8px 16px;
                color: white;
                font-weight: bold;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #5aafff, stop:1 #1077dd);
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #3a8eef, stop:1 #0055bb);
            }
        """)
        settings_layout.addWidget(onnx_browse_btn)
        layout.addLayout(settings_layout)

        # Connect browse button
        onnx_browse_btn.clicked.connect(self.browse_onnx)

        # Add info label
        onnx_info = QLabel("💡 Uses T5 model for fast grammar correction. Leave empty to use LLM only.")
        onnx_info.setWordWrap(True)
        onnx_info.setStyleSheet("color: #888888; font-size: 11px;")
        layout.addWidget(onnx_info)

        # Recent Models
        recent_layout = QHBoxLayout()
        recent_layout.addWidget(QLabel("Recent Models:"))
        self.recent_combo = QComboBox()
        self.recent_combo.setMinimumWidth(300)
        self.recent_combo.currentTextChanged.connect(self.on_recent_selected)
        recent_layout.addWidget(self.recent_combo)
        layout.addLayout(recent_layout)

        # Server Settings
        settings_layout = QHBoxLayout()

        settings_layout.addWidget(QLabel("Port:"))
        self.port_edit = QLineEdit()
        self.port_edit.setFixedWidth(80)
        settings_layout.addWidget(self.port_edit)

        settings_layout.addWidget(QLabel("Context:"))
        self.context_edit = QLineEdit()
        self.context_edit.setFixedWidth(80)
        settings_layout.addWidget(self.context_edit)

        settings_layout.addWidget(QLabel("GPU Layers:"))
        self.gpu_layers_edit = QLineEdit()
        self.gpu_layers_edit.setFixedWidth(80)
        settings_layout.addWidget(self.gpu_layers_edit)

        settings_layout.addStretch()
        layout.addLayout(settings_layout)

        # Model Behavior
        behavior_layout = QVBoxLayout()

        self.keep_loaded_check = QCheckBox("Keep model loaded (disable auto-unload)")
        self.keep_loaded_check.setToolTip(
            "If checked, model stays in VRAM until you manually unload it"
        )
        behavior_layout.addWidget(self.keep_loaded_check)

        timeout_layout = QHBoxLayout()
        timeout_layout.addWidget(QLabel("Auto-unload after (seconds):"))
        self.timeout_edit = QLineEdit()
        self.timeout_edit.setFixedWidth(100)
        timeout_layout.addWidget(self.timeout_edit)
        timeout_layout.addStretch()
        behavior_layout.addLayout(timeout_layout)

        layout.addLayout(behavior_layout)

        # Generation Parameters
        gen_layout1 = QHBoxLayout()
        gen_layout1.addWidget(QLabel("Temperature:"))
        self.temp_edit = QLineEdit()
        self.temp_edit.setFixedWidth(50)
        gen_layout1.addWidget(self.temp_edit)

        gen_layout1.addWidget(QLabel("Top K:"))
        self.topk_edit = QLineEdit()
        self.topk_edit.setFixedWidth(50)
        gen_layout1.addWidget(self.topk_edit)

        gen_layout1.addWidget(QLabel("Top P:"))
        self.topp_edit = QLineEdit()
        self.topp_edit.setFixedWidth(50)
        gen_layout1.addWidget(self.topp_edit)

        gen_layout1.addWidget(QLabel("Min P:"))
        self.minp_edit = QLineEdit()
        self.minp_edit.setFixedWidth(50)
        gen_layout1.addWidget(self.minp_edit)
        gen_layout1.addStretch()
        layout.addLayout(gen_layout1)

        gen_layout2 = QHBoxLayout()
        gen_layout2.addWidget(QLabel("Repetition:"))
        self.repeat_edit = QLineEdit()
        self.repeat_edit.setFixedWidth(50)
        self.repeat_edit.setToolTip("Repetition Penalty (1.0 = standard, usually 1.1 - 1.2 to penalize)")
        gen_layout2.addWidget(self.repeat_edit)

        gen_layout2.addWidget(QLabel("Freq Pen:"))
        self.freq_edit = QLineEdit()
        self.freq_edit.setFixedWidth(50)
        self.freq_edit.setToolTip(
            "Frequency Penalty (0.0 - 2.0). Higher values reduce repetition."
        )
        gen_layout2.addWidget(self.freq_edit)

        gen_layout2.addWidget(QLabel("Pres Pen:"))
        self.pres_edit = QLineEdit()
        self.pres_edit.setFixedWidth(50)
        self.pres_edit.setToolTip(
            "Presence Penalty (0.0 - 2.0). Higher values encourage new topics."
        )
        gen_layout2.addWidget(self.pres_edit)

        gen_layout2.addStretch()
        layout.addLayout(gen_layout2)

        # Hotkey — Windows-style live recorder
        hotkey_layout = QHBoxLayout()
        hotkey_layout.addWidget(QLabel("Hotkey:"))
        self.hotkey_edit = HotkeyEdit(re_register_cb=self._re_register_cb)
        self.hotkey_edit.setToolTip(
            "Click, then press your desired key combination (e.g. Ctrl+Shift+Space).\n"
            "Escape cancels. A modifier key (Ctrl/Shift/Alt) is required."
        )
        hotkey_layout.addWidget(self.hotkey_edit)
        layout.addLayout(hotkey_layout)

        # System Prompt
        prompt_label = QLabel("System Prompt:")
        prompt_label.setToolTip(
            "Customize the instruction sent to the model. Leave empty for default."
        )
        layout.addWidget(prompt_label)

        self.prompt_edit = QTextEdit()
        self.prompt_edit.setPlaceholderText(
            "Leave empty to use default prompt. Custom prompt overrides the built-in instruction."
        )
        self.prompt_edit.setMaximumHeight(90)
        layout.addWidget(self.prompt_edit)

        # Info
        info_label = QLabel("Note: Changes take effect after restart")
        info_label.setStyleSheet("color: rgba(255, 255, 255, 0.4); font-size: 11px;")
        layout.addWidget(info_label)

        layout.addStretch()

        # Buttons
        button_layout = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self.save_settings)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(
            "background: rgba(255, 255, 255, 0.08); border: 1px solid rgba(255, 255, 255, 0.15);"
        )
        cancel_btn.clicked.connect(self.reject)
        button_layout.addStretch()
        button_layout.addWidget(save_btn)
        button_layout.addWidget(cancel_btn)
        layout.addLayout(button_layout)

        self.setLayout(layout)

    def load_settings(self):
        """Load current settings into UI"""
        self.server_path_edit.setText(self.config.get("llama_server_path", ""))
        self.model_path_edit.setText(self.config.get("model_path", ""))
        self.port_edit.setText(str(self.config.get("server_port", 8080)))
        self.context_edit.setText(str(self.config.get("context_size", 4096)))
        self.gpu_layers_edit.setText(str(self.config.get("gpu_layers", 99)))
        self.timeout_edit.setText(str(self.config.get("idle_timeout_seconds", 300)))
        self.temp_edit.setText(str(self.config.get("temperature", 0.0)))
        self.topk_edit.setText(str(self.config.get("top_k", 40)))
        self.topp_edit.setText(str(self.config.get("top_p", 0.95)))
        self.minp_edit.setText(str(self.config.get("min_p", 0.05)))
        self.repeat_edit.setText(str(self.config.get("repeat_penalty", 1.0)))
        self.freq_edit.setText(str(self.config.get("frequency_penalty", 0.0)))
        self.pres_edit.setText(str(self.config.get("presence_penalty", 0.0)))
        self.hotkey_edit.setText(self.config.get("hotkey", "alt+shift+t"))
        self.keep_loaded_check.setChecked(self.config.get("keep_model_loaded", False))
        self.prompt_edit.setPlainText(self.config.get("system_prompt", ""))

        # Load ONNX model directory
        if self.onnx_dir_edit:
            self.onnx_dir_edit.setText(self.config.get("onnx_model_dir", ""))

        # Load recent models
        self.recent_combo.clear()
        self.recent_combo.addItem("-- Select recent model --")
        recent = self.config.get("recent_models", [])
        for model in recent:
            if os.path.exists(model):
                self.recent_combo.addItem(model)

    def on_recent_selected(self, text):
        """Handle selection from recent models dropdown"""
        if text and text != "-- Select recent model --":
            self.model_path_edit.setText(text)

    def mousePressEvent(self, event):
        """Start window dragging — only when clicking on empty chrome"""
        if event.button() == Qt.LeftButton:
            child = self.childAt(event.pos())
            if child is None or isinstance(child, QLabel):
                self.dragging = True
                self.drag_position = event.globalPos() - self.pos()
                event.accept()
            else:
                super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Handle window dragging"""
        if self.dragging and event.buttons() == Qt.LeftButton:
            self.move(event.globalPos() - self.drag_position)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def browse_server(self):
        """Browse for llama-server.exe"""
        path, _ = QFileDialog.getOpenFileName(
            self, "Select llama-server.exe", "", "Executable (*.exe)"
        )
        if path:
            self.server_path_edit.setText(path)

    def browse_model(self):
        """Browse for model file"""
        path, _ = QFileDialog.getOpenFileName(
            self, "Select GGUF Model", "", "GGUF Models (*.gguf)"
        )
        if path:
            self.model_path_edit.setText(path)
            self.config.add_recent_model(path)
            self.load_settings()  # Refresh recent list

    def browse_onnx(self):
        """Browse for ONNX model directory"""
        dir_path = QFileDialog.getExistingDirectory(
            self, "Select ONNX Model Directory"
        )
        if dir_path:
            self.onnx_dir_edit.setText(dir_path)

    def save_settings(self):
        """Save settings to config"""
        self.config.set("llama_server_path", self.server_path_edit.text())
        self.config.set("model_path", self.model_path_edit.text())
        self.config.set("keep_model_loaded", self.keep_loaded_check.isChecked())

        try:
            self.config.set("server_port", int(self.port_edit.text()))
            self.config.set("context_size", int(self.context_edit.text()))
            self.config.set("gpu_layers", int(self.gpu_layers_edit.text()))
            self.config.set("idle_timeout_seconds", int(self.timeout_edit.text()))
            self.config.set("temperature", float(self.temp_edit.text()))
            self.config.set("top_k", int(self.topk_edit.text()))
            self.config.set("top_p", float(self.topp_edit.text()))
            self.config.set("min_p", float(self.minp_edit.text()))
            self.config.set("repeat_penalty", float(self.repeat_edit.text()))
            self.config.set("frequency_penalty", float(self.freq_edit.text()))
            self.config.set("presence_penalty", float(self.pres_edit.text()))
        except ValueError:
            QMessageBox.warning(
                self, "Invalid Value", "Please enter valid numbers for numeric fields"
            )
            return

        self.config.set("hotkey", self.hotkey_edit.text())
        self.config.set("system_prompt", self.prompt_edit.toPlainText().strip())

        # Save ONNX model directory
        if self.onnx_dir_edit:
            self.config.set("onnx_model_dir", self.onnx_dir_edit.text())

        if self.model_path_edit.text():
            self.config.add_recent_model(self.model_path_edit.text())

        self.settings_changed.emit()
        self.accept()

    def nativeEvent(self, eventType, message):
        """Handle native Windows events for true frameless window resizing"""
        if eventType == b'windows_generic_MSG' or eventType == b'windows_dispatcher_MSG':
            import ctypes
            import ctypes.wintypes
            try:
                msg = ctypes.wintypes.MSG.from_address(message.__int__())
                if msg.message == 0x0084: # WM_NCHITTEST
                    # Use QCursor.pos() which Qt natively translates to logical High DPI multi-monitor coordinates
                    pos = self.mapFromGlobal(QCursor.pos())
                    
                    # 10px margin around the window acts as native resize borders
                    margin = 10
                    left = pos.x() < margin
                    right = pos.x() > self.width() - margin
                    top = pos.y() < margin
                    bottom = pos.y() > self.height() - margin
                    
                    res = 0
                    if left and top: res = 13 # HTTOPLEFT
                    elif right and top: res = 14 # HTTOPRIGHT
                    elif left and bottom: res = 16 # HTBOTTOMLEFT
                    elif right and bottom: res = 17 # HTBOTTOMRIGHT
                    elif left: res = 10 # HTLEFT
                    elif right: res = 11 # HTRIGHT
                    elif top: res = 12 # HTTOP
                    elif bottom: res = 15 # HTBOTTOM
                    
                    if res != 0:
                        return True, res
            except Exception:
                pass
        return super().nativeEvent(eventType, message)


class ONNXManager(QObject):
    """Manages the ONNX model inference for fast proofreading"""
    
    status_changed = pyqtSignal(str, str)  # status, color
    
    def __init__(self, config_manager):
        super().__init__()
        self.config = config_manager
        self.pipeline = None
        self.model_dir = ""
        self.is_loaded = False
        self.loading = False
        self.is_seq2seq = False
        self._loading_lock = threading.Lock()
        
    def load_model(self):
        self.model_dir = self.config.get("onnx_model_dir", "")
        if not self.model_dir or not os.path.exists(self.model_dir):
            self.status_changed.emit("No ONNX Model", "gray")
            return False
            
        try:
            self.loading = True
            self.status_changed.emit("Loading ONNX...", "orange")
            
            import onnxruntime as ort
            from transformers import AutoTokenizer
            import numpy as np
            
            # Detect whether it's an encoder-decoder (Seq2Seq) or decoder-only (Causal LM)
            is_seq2seq = os.path.exists(os.path.join(self.model_dir, "encoder_model.onnx"))
            self.is_seq2seq = is_seq2seq
            
            log_debug(f"[ONNX] Loading model from {self.model_dir}, is_seq2seq={is_seq2seq}")
            
            # Load tokenizer
            tokenizer = AutoTokenizer.from_pretrained(self.model_dir)
            
            # Try CUDA first, fall back to CPU if CUDA fails
            cuda_available = 'CUDAExecutionProvider' in ort.get_available_providers()
            providers = ['CUDAExecutionProvider', 'CPUExecutionProvider'] if cuda_available else ['CPUExecutionProvider']
            log_debug(f"[ONNX] Using providers: {providers}")
            
            try:
                if is_seq2seq:
                    # Load encoder and decoder ONNX sessions directly (no PyTorch/optimum)
                    encoder_path = os.path.join(self.model_dir, "encoder_model.onnx")
                    decoder_path = os.path.join(self.model_dir, "decoder_model.onnx")
                    
                    log_debug(f"[ONNX] Loading encoder: {encoder_path}")
                    log_debug(f"[ONNX] Loading decoder: {decoder_path}")
                    
                    self.encoder_session = ort.InferenceSession(encoder_path, providers=providers)
                    self.decoder_session = ort.InferenceSession(decoder_path, providers=providers)
                    
                    self.tokenizer = tokenizer
                    self.is_loaded = True
                    self.loading = False
                    self.status_changed.emit("ONNX Ready (T5)", "green")
                    log_debug("[ONNX] Model loaded successfully (Seq2Seq)")
                    return True
                else:
                    # Decoder-only model
                    model_path = os.path.join(self.model_dir, "model.onnx")
                    log_debug(f"[ONNX] Loading causal LM: {model_path}")
                    
                    self.decoder_session = ort.InferenceSession(model_path, providers=providers)
                    self.tokenizer = tokenizer
                    self.is_loaded = True
                    self.loading = False
                    self.status_changed.emit("ONNX Ready", "green")
                    log_debug("[ONNX] Model loaded successfully (Causal LM)")
                    return True
                    
            except Exception as load_error:
                # CUDA failed, retry with CPU only
                if cuda_available:
                    log_debug(f"[ONNX] CUDA execution failed: {load_error}, falling back to CPU")
                    self.status_changed.emit("CUDA failed, using CPU...", "orange")
                    providers = ['CPUExecutionProvider']
                    
                    if is_seq2seq:
                        encoder_path = os.path.join(self.model_dir, "encoder_model.onnx")
                        decoder_path = os.path.join(self.model_dir, "decoder_model.onnx")
                        self.encoder_session = ort.InferenceSession(encoder_path, providers=providers)
                        self.decoder_session = ort.InferenceSession(decoder_path, providers=providers)
                        self.tokenizer = tokenizer
                        self.is_loaded = True
                        self.loading = False
                        self.status_changed.emit("ONNX Ready (T5)", "green")
                        log_debug("[ONNX] Model loaded successfully with CPU (Seq2Seq)")
                        return True
                    else:
                        model_path = os.path.join(self.model_dir, "model.onnx")
                        self.decoder_session = ort.InferenceSession(model_path, providers=providers)
                        self.tokenizer = tokenizer
                        self.is_loaded = True
                        self.loading = False
                        self.status_changed.emit("ONNX Ready", "green")
                        log_debug("[ONNX] Model loaded successfully with CPU (Causal LM)")
                        return True
                else:
                    raise
                
        except Exception as e:
            log_debug(f"[ONNX] Load model error: {e}")
            self.is_loaded = False
            self.loading = False
            self.status_changed.emit("ONNX Load Failed", "red")
            return False
    
    def proofread(self, text):
        log_debug(f"[ONNX] Proofreading text: {text[:100]}...")
        
        # Use mutex to prevent race condition on concurrent calls
        with self._loading_lock:
            if not self.is_loaded:
                log_debug("[ONNX] Model not loaded, loading now...")
                if not self.load_model():
                    self.status_changed.emit("ONNX inference failed", "red")
                    log_debug("[ONNX] Failed to load model.")
                    return None  # Return None so caller can handle fallback explicitly
                    
        try:
            import numpy as np
            if self.is_seq2seq:
                result = self._run_seq2seq(text, prefix="Fix grammar: ", max_tokens=200)
            else:
                # Causal LM inference (not used for T5)
                prompt = f"Fix grammar and typos in the following text:\n{text}\n\nCorrected:"
                log_debug(f"[ONNX] Using Causal LM. Prompt: {prompt[:100]}...")
                
                inputs = self.tokenizer(prompt, return_tensors="np", truncation=True, max_length=512)
                input_ids = inputs["input_ids"].astype(np.int64)
                attention_mask = inputs["attention_mask"].astype(np.int64)
                
                # Run decoder
                outputs = self.decoder_session.run(None, {
                    "input_ids": input_ids,
                    "attention_mask": attention_mask
                })
                
                # Get generated tokens
                output_ids = np.argmax(outputs[0], axis=-1)[0]
                result = self.tokenizer.decode(output_ids, skip_special_tokens=True)
                # Remove prompt from result
                result = result.replace(prompt, "").strip()
            
            if result is None:
                return None
                
            log_debug(f"[ONNX] Result length: {len(result)}")
            
            # Apply post-processing (same as LLM)
            result = strip_thinking_tokens(result)
            result = strip_meta_commentary(result, text)
            
            # Handle empty result after stripping
            if not result.strip():
                log_debug("[ONNX] Empty result after post-processing")
                return None
            
            return result.strip()
            
        except Exception as e:
            log_debug(f"ONNX Proofread Error: {e}")
            self.status_changed.emit("ONNX inference error", "red")
            return None
    

    def _run_seq2seq(self, text, prefix="Fix grammar: ", max_tokens=200):
        """Run T5 seq2seq with proper KV-cache handling.
        
        Key details of the merged ONNX decoder model:
        - Outputs are named 'present.X.{decoder|encoder}.{key|value}'
        - Inputs are named 'past_key_values.X.{decoder|encoder}.{key|value}'  
        - When use_cache_branch=False (step 0): encoder KVs are computed fresh, outputs have proper shapes
        - When use_cache_branch=True (step 1+): encoder KVs are cached internally by the model,
          and the model outputs EMPTY (batch=0) encoder KV tensors. We must preserve the encoder
          KVs from step 0 and only update decoder KVs from each step's output.
        """
        import numpy as np
        
        prompt = f"{prefix}{text}"
        log_debug(f"[ONNX] Seq2Seq prompt: {prompt[:100]}...")
        
        # Tokenize input
        inputs = self.tokenizer(prompt, return_tensors="np", truncation=True, max_length=512)
        input_ids = inputs["input_ids"].astype(np.int64)
        attention_mask = inputs["attention_mask"].astype(np.int64)
        
        # Run encoder
        encoder_outputs = self.encoder_session.run(None, {
            "input_ids": input_ids,
            "attention_mask": attention_mask
        })
        
        # Build output→input name mappings, separated by type
        output_names = [out.name for out in self.decoder_session.get_outputs()]
        decoder_kv_map = {}  # present.X.decoder.{key|value} → past_key_values.X.decoder.{key|value}
        encoder_kv_map = {}  # present.X.encoder.{key|value} → past_key_values.X.encoder.{key|value}
        for name in output_names:
            if name.startswith("present."):
                input_name = "past_key_values." + name[len("present."):]
                if ".encoder." in name:
                    encoder_kv_map[name] = input_name
                elif ".decoder." in name:
                    decoder_kv_map[name] = input_name
        
        # Get past_key_values input metadata for zero-fill shapes
        pkv_inputs = [inp for inp in self.decoder_session.get_inputs()
                      if inp.name.startswith("past_key_values.")]
        
        # Collect generated token IDs
        generated_ids = [self.tokenizer.pad_token_id]
        decoder_input_ids = np.array([[self.tokenizer.pad_token_id]], dtype=np.int64)
        past_kv_feed = None
        encoder_kv_cache = None  # Preserved from step 0
        
        for step in range(max_tokens):
            decoder_inputs = {
                "input_ids": decoder_input_ids,
                "encoder_hidden_states": encoder_outputs[0],
                "encoder_attention_mask": attention_mask
            }
            
            if past_kv_feed is not None:
                decoder_inputs.update(past_kv_feed)
                decoder_inputs["use_cache_branch"] = np.array([True], dtype=np.bool_)
            else:
                for inp in pkv_inputs:
                    shape = inp.shape
                    decoder_inputs[inp.name] = np.zeros(
                        (1, shape[1], 0, shape[3]), dtype=np.float32
                    )
                decoder_inputs["use_cache_branch"] = np.array([False], dtype=np.bool_)
            
            decoder_outputs = self.decoder_session.run(None, decoder_inputs)
            
            if step == 0:
                log_debug(f"[ONNX] Step 0 output shape: {decoder_outputs[0].shape}, count: {len(decoder_outputs)}")
                # Capture encoder KV-cache from step 0 (the only time it's valid)
                encoder_kv_cache = {}
                for idx, out_name in enumerate(output_names):
                    if out_name in encoder_kv_map:
                        encoder_kv_cache[encoder_kv_map[out_name]] = decoder_outputs[idx]
            
            # Get next token (greedy)
            next_token_logits = decoder_outputs[0][:, -1, :]
            next_token_id = int(np.argmax(next_token_logits, axis=-1)[0])
            generated_ids.append(next_token_id)
            
            # Check for EOS
            if next_token_id == self.tokenizer.eos_token_id:
                log_debug(f"[ONNX] EOS at step {step}")
                break
            
            # Next step: feed only the new token (KV-cache has the history)
            decoder_input_ids = np.array([[next_token_id]], dtype=np.int64)
            
            # Build KV-cache feed: decoder KVs from this step + encoder KVs from step 0
            past_kv_feed = {}
            # Decoder KVs: always from current step output (they grow with each token)
            for idx, out_name in enumerate(output_names):
                if out_name in decoder_kv_map:
                    past_kv_feed[decoder_kv_map[out_name]] = decoder_outputs[idx]
            # Encoder KVs: always from step 0 (model returns empty on subsequent steps)
            past_kv_feed.update(encoder_kv_cache)
        
        # Decode
        result = self.tokenizer.decode(generated_ids, skip_special_tokens=True)
        return result
    
    def chat(self, messages, max_tokens=1000):
        """Simple chat/refinement using ONNX model.
        
        This is a lightweight alternative to the full LLM chat.
        It uses the same seq2seq logic as proofread() but with a different prefix.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            max_tokens: Maximum tokens to generate
            
        Returns:
            str: Model response or None on failure
        """
        log_debug(f"[ONNX] Chat with {len(messages)} messages")
        
        # Use mutex to prevent race condition on concurrent calls
        with self._loading_lock:
            if not self.is_loaded:
                log_debug("[ONNX] Model not loaded for chat, loading now...")
                if not self.load_model():
                    self.status_changed.emit("ONNX chat failed", "red")
                    log_debug("[ONNX] Failed to load model for chat.")
                    return None
        
        try:
            import numpy as np
            
            # Extract the latest user message
            user_content = ""
            for msg in reversed(messages[-4:]):
                if msg.get("role") == "user":
                    user_content = msg.get("content", "")
                    break
            
            if not user_content:
                log_debug("[ONNX] No user content found in messages")
                return None
            
            # Truncate if too long for ONNX model context
            if len(user_content) > 1000:
                user_content = user_content[-1000:]
            
            if self.is_seq2seq:
                result = self._run_seq2seq(user_content, prefix="Refine text: ", max_tokens=max_tokens)
            else:
                # Causal LM inference (not used for T5)
                prompt = f"Refine and improve the following text:\n{user_content}\n\nImproved:"
                log_debug(f"[ONNX Chat] Using Causal LM. Prompt: {prompt[:100]}...")
                
                inputs = self.tokenizer(prompt, return_tensors="np", truncation=True, max_length=512)
                input_ids = inputs["input_ids"].astype(np.int64)
                attention_mask = inputs["attention_mask"].astype(np.int64)
                
                outputs = self.decoder_session.run(None, {
                    "input_ids": input_ids,
                    "attention_mask": attention_mask
                })
                
                output_ids = np.argmax(outputs[0], axis=-1)[0]
                result = self.tokenizer.decode(output_ids, skip_special_tokens=True)
                result = result.replace(prompt, "").strip()
            
            if result is None:
                return None
                
            log_debug(f"[ONNX Chat] Result length: {len(result)}")
            
            # Apply post-processing
            result = strip_thinking_tokens(result)
            result = strip_meta_commentary(result, user_content)
            
            if not result.strip():
                log_debug("[ONNX Chat] Empty result after post-processing")
                return None
            
            return result.strip()
            
        except Exception as e:
            log_debug(f"ONNX Chat Error: {e}")
            self.status_changed.emit("ONNX chat error", "red")
            return None


class ModelManager(QObject):
    """Manages the llama.cpp server and model inference"""

    status_changed = pyqtSignal(str)
    model_loaded = pyqtSignal()
    model_unloaded = pyqtSignal()
    correction_done = pyqtSignal(str)

    def __init__(self, config_manager):
        super().__init__()
        self.config = config_manager
        self.server_process = None
        self.last_used = None
        self.loading = False
        self.lock = threading.Lock()
        self.log_file = None
        self.timer = QTimer()
        self.timer.timeout.connect(self._check_idle)
        self.timer.start(10000)  # Check every 10 seconds

    def _get_chat_url(self):
        """Get the OpenAI-compatible chat completions URL"""
        host = self.config.get("server_host", "127.0.0.1")
        port = self.config.get("server_port", 8080)
        return f"http://{host}:{port}/v1/chat/completions"

    def _get_health_url(self):
        """Get the health check URL"""
        host = self.config.get("server_host", "127.0.0.1")
        port = self.config.get("server_port", 8080)
        return f"http://{host}:{port}/health"

    def _check_idle(self):
        """Check if model should be auto-unloaded"""
        if self.config.get("keep_model_loaded", False):
            return

        if self.server_process is not None and self.last_used is not None:
            idle_timeout = self.config.get("idle_timeout_seconds", 300)
            idle_time = (datetime.now() - self.last_used).total_seconds()
            remaining = idle_timeout - idle_time

            if remaining <= 0:
                self.unload_model()
            elif remaining <= 60:
                self.status_changed.emit(f"Unloading in {int(remaining)}s")

    def _kill_existing_servers(self):
        """Kill any existing llama-server processes to prevent port conflicts"""
        try:
            import psutil

            for proc in psutil.process_iter(["pid", "name"]):
                if proc.info["name"] and "llama-server" in proc.info["name"].lower():
                    try:
                        log_debug(
                            f"Killing existing llama-server process (PID: {proc.info['pid']})"
                        )
                        proc.terminate()
                        proc.wait(timeout=3)
                    except:
                        try:
                            proc.kill()
                        except:
                            pass
        except Exception as e:
            log_debug(f"Could not kill existing servers: {e}")

    def load_model(self):
        """Load the model by starting llama-server"""
        log_debug("load_model called")

        # Kill any existing server processes first
        self._kill_existing_servers()

        with self.lock:
            if self.server_process is not None:
                log_debug("Model already loaded (lock check)")
                self.status_changed.emit("Model already loaded")
                return True
            if self.loading:
                log_debug("Model loading already in progress")
                return False
            self.loading = True

        try:
            log_debug("Starting inference server process...")
            self.status_changed.emit("Starting inference server...")

            server_path = self.config.get("llama_server_path")
            model_path = self.config.get("model_path")

            if not server_path or not os.path.exists(server_path):
                raise Exception(f"llama-server.exe not found at: {server_path}")

            if not model_path or not os.path.exists(model_path):
                raise Exception(f"Model not found at: {model_path}")

            host = self.config.get("server_host", "127.0.0.1")
            port = self.config.get("server_port", 8080)
            context = self.config.get("context_size", 4096)
            gpu_layers = self.config.get("gpu_layers", 99)

            # Auto-detect GPU: override to CPU mode if no NVIDIA driver found
            if gpu_layers > 0 and not _has_nvidia_gpu():
                log_debug("No NVIDIA GPU — falling back to CPU mode (gpu_layers=0)")
                self.status_changed.emit("No GPU found — loading in CPU mode (may be slow)...")
                gpu_layers = 0

            cmd = [
                server_path,
                "-m",
                model_path,
                "-c",
                str(context),
                "-ngl",
                str(gpu_layers),
                "--port",
                str(port),
                "--host",
                host,
                "--no-warmup",  # Skip warmup to speed up startup
                "--reasoning-format",
                "none",  # Disable thinking mode (Qwen3)
            ]

            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            # Open log file
            self.log_file = open(LOG_FILE, "w")

            # Set cwd to the server directory so it finds DLLs
            server_dir = os.path.dirname(server_path)

            self.server_process = subprocess.Popen(
                cmd,
                cwd=server_dir,
                startupinfo=startupinfo,
                stdout=self.log_file,
                stderr=subprocess.STDOUT,
            )

            # Wait for server to be ready (with fast failure detection)
            # CUDA fitting + model loading can take 30-90s on first launch
            max_wait = 180
            for i in range(max_wait):
                # Check if server process died (fast fail)
                if self.server_process.poll() is not None:
                    # Read the log to show the actual error
                    try:
                        self.log_file.flush()
                        with open(LOG_FILE, "r") as lf:
                            log_content = lf.read()
                        # Extract the error line
                        for line in log_content.splitlines():
                            if "error" in line.lower() or "failed" in line.lower():
                                raise Exception(f"Server failed: {line.strip()[:80]}")
                    except Exception as read_err:
                        if "Server failed" in str(read_err):
                            raise
                    raise Exception("Server exited immediately. Check server_log.txt")
                try:
                    response = requests.get(self._get_health_url(), timeout=1)
                    if response.status_code == 200:
                        break
                except requests.RequestException:
                    pass

                # Show progress every 10 seconds
                if i > 0 and i % 10 == 0:
                    self.status_changed.emit(f"Loading model... ({i}s)")
                time.sleep(1)
            else:
                raise Exception(f"Server failed to start after {max_wait}s")

            self.last_used = datetime.now()
            self.loading = False
            model_name = friendly_model_name(model_path)
            log_debug(f"Model loaded successfully: {model_name}")
            self.status_changed.emit(f"Ready — {model_name}")
            self.model_loaded.emit()
            return True

        except Exception as e:
            err_str = str(e)
            log_debug(f"load_model failed: {err_str}")
            self.loading = False
            self.unload_model()

            # If a CUDA error caused the crash and we were using GPU, retry on CPU
            _cuda_keywords = ("cuda", "cublas", "cudart", "ggml-cuda", "out of memory", "no gpu")
            _was_gpu = self.config.get("gpu_layers", 99) > 0
            if _was_gpu and any(kw in err_str.lower() for kw in _cuda_keywords):
                log_debug("CUDA error detected — retrying in CPU mode (gpu_layers=0)")
                self.status_changed.emit("GPU error — retrying in CPU mode...")
                # Temporarily patch config for this attempt only
                _orig_layers = self.config.get("gpu_layers", 99)
                self.config.config["gpu_layers"] = 0
                result = self.load_model()
                self.config.config["gpu_layers"] = _orig_layers
                return result

            self.status_changed.emit(f"Load error: {err_str[:80]}")
            return False

    def unload_model(self):
        """Unload the model by stopping the server"""
        with self.lock:
            if self.server_process is not None:
                self.status_changed.emit("Stopping server...")
                try:
                    self.server_process.terminate()
                    self.server_process.wait(timeout=5)
                except:
                    try:
                        self.server_process.kill()
                    except:
                        pass

                self.server_process = None
                if self.log_file:
                    try:
                        self.log_file.close()
                    except:
                        pass
                    self.log_file = None

                self.last_used = None
                self.status_changed.emit("Model unloaded")
                self.model_unloaded.emit()

    def is_loaded(self):
        """Check if model is currently loaded"""
        return self.server_process is not None and self.server_process.poll() is None

    def correct_text(self, text, custom_instruction=None):
        """Correct text using the model via chat completions API"""
        log_debug(f"correct_text called with length: {len(text)}")

        if not self.is_loaded():
            log_debug("Model not loaded, loading now...")
            if not self.load_model():
                log_debug("Failed to load model")
                return None

        self.last_used = datetime.now()

        try:
            self.status_changed.emit("Correcting...")

            # Get custom instruction or use default
            if custom_instruction:
                # Custom instruction path - respecting user's manual override
                messages = [
                    {"role": "user", "content": f"{custom_instruction}\n\n{text}"}
                ]
            else:
                custom_prompt = self.config.get("system_prompt", "").strip()
                if custom_prompt:
                    # User-defined system prompt from Settings
                    messages = [
                        {"role": "system", "content": custom_prompt},
                        {"role": "user", "content": text},
                    ]
                else:
                    # Enhanced Few-Shot Prompting Strategy
                    # Uses strict system prompt + multiple diverse examples + forces completion
                    messages = [
                        {
                            "role": "system",
                            "content": (
                                "You are a text correction engine. Your task is to proofread and refine text.\n"
                                "CRITICAL RULES - VIOLATING THESE IS AN ERROR:\n"
                                "1. Output ONLY the corrected text - no explanations, no labels, no greetings\n"
                                "2. NEVER start with phrases like 'Here is', 'Sure', 'Corrected', 'The corrected'\n"
                                "3. NEVER wrap output in quotes or markdown\n"
                                "4. If text is perfect, return it unchanged\n"
                                "5. Fix spelling, grammar, punctuation while preserving meaning and tone\n"
                                "6. PRESERVE ALL LINE BREAKS AND PARAGRAPH SPACING - do not remove blank lines\n"
                                "7. Maintain original formatting exactly, including multiple line breaks"
                            ),
                        },
                        {
                            "role": "user",
                            "content": "the project were delayed because of bad wether",
                        },
                        {
                            "role": "assistant",
                            "content": "The project was delayed because of bad weather.",
                        },
                        {"role": "user", "content": "i dont know if its gona work"},
                        {
                            "role": "assistant",
                            "content": "I don't know if it's going to work.",
                        },
                        {"role": "user", "content": "Hello, how are you doing today?"},
                        {
                            "role": "assistant",
                            "content": "Hello, how are you doing today?",
                        },
                        {
                            "role": "user",
                            "content": "The data shows that their is an increase in sales.",
                        },
                        {
                            "role": "assistant",
                            "content": "The data shows that there is an increase in sales.",
                        },
                        {
                            "role": "user",
                            "content": "Dear John,\n\nHow are you?\n\nI hope your doing well.",
                        },
                        {
                            "role": "assistant",
                            "content": "Dear John,\n\nHow are you?\n\nI hope you're doing well.",
                        },
                        {"role": "user", "content": text},
                        {"role": "assistant", "content": ""},
                    ]

            temperature = self.config.get("temperature", 0.1)
            top_k = self.config.get("top_k", 40)
            top_p = self.config.get("top_p", 0.95)
            min_p = self.config.get("min_p", 0.05)
            frequency_penalty = self.config.get("frequency_penalty", 0.0)
            presence_penalty = self.config.get("presence_penalty", 0.0)
            repeat_penalty = self.config.get("repeat_penalty", 1.0)
            # Give enough room for thinking overhead + corrections
            max_tokens = min(len(text) * 3 + 500, 4096)

            payload = {
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "top_k": top_k,
                "top_p": top_p,
                "min_p": min_p,
                "frequency_penalty": frequency_penalty,
                "presence_penalty": presence_penalty,
                "repeat_penalty": repeat_penalty,
            }

            url = self._get_chat_url()
            log_debug(f"Sending POST to {url}")

            response = requests.post(url, json=payload, timeout=120)
            log_debug(f"Response received. Status: {response.status_code}")

            if response.status_code == 400:
                error_body = response.text.lower()
                if "context" in error_body or "too long" in error_body:
                    self.status_changed.emit("Error: text too long")
                    return "[Error] Text exceeds the model's context limit. Try shorter text or increase context size in Settings."
                response.raise_for_status()

            response.raise_for_status()

            result = response.json()
            log_debug("JSON parsed successfully")

            corrected = (
                result.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )

            # Strip thinking tokens (Qwen3 thinking mode)
            corrected = strip_thinking_tokens(corrected)
            # Strip meta-commentary ("Here is the corrected text:")
            corrected = strip_meta_commentary(corrected, text)

            # Check if output still contains conversational elements and retry if needed
            if contains_meta_commentary(corrected):
                log_debug(
                    "Detected conversational output, retrying with stronger prompt..."
                )
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
                }

                retry_response = requests.post(url, json=retry_payload, timeout=120)
                retry_response.raise_for_status()

                retry_result = retry_response.json()
                corrected = (
                    retry_result.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                    .strip()
                )

                # Strip again
                corrected = strip_thinking_tokens(corrected)
                corrected = strip_meta_commentary(corrected, text)
                log_debug(f"Retry correction length: {len(corrected)}")

            log_debug(f"Correction length (after strip): {len(corrected)}")

            self.last_used = datetime.now()
            self.status_changed.emit("Ready")
            return corrected if corrected else text

        except requests.exceptions.ConnectionError:
            log_debug("Connection error in correct_text")
            self.status_changed.emit("Error: server unreachable")
            return (
                "[Error] Cannot reach inference server. Make sure the model is loaded."
            )
        except requests.exceptions.Timeout:
            log_debug("Timeout in correct_text")
            self.status_changed.emit("Error: timeout")
            return "[Error] Server took too long to respond. The model may be too large for your GPU."
        except Exception as e:
            log_debug(f"Error in correct_text: {e}")
            error_msg = str(e)
            if "500" in error_msg:
                self.status_changed.emit("Error: server error")
                return "[Error] Server error (500). The model may not support this input. Check server_log.txt."
            self.status_changed.emit(f"Error: {error_msg[:50]}")
            return None

    def chat_with_model(self, messages, max_tokens=1000):
        """Chat with the model for text refinement via chat completions API"""
        log_debug(f"chat_with_model called with {len(messages)} messages")

        if not self.is_loaded():
            if not self.load_model():
                log_debug("chat_with_model: failed to load model")
                return None

        self.last_used = datetime.now()

        try:
            self.status_changed.emit("Thinking...")

            temperature = self.config.get("temperature", 0.1)
            top_k = self.config.get("top_k", 40)
            top_p = self.config.get("top_p", 0.95)
            min_p = self.config.get("min_p", 0.05)
            frequency_penalty = self.config.get("frequency_penalty", 0.0)
            presence_penalty = self.config.get("presence_penalty", 0.0)
            repeat_penalty = self.config.get("repeat_penalty", 1.0)

            payload = {
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "top_k": top_k,
                "top_p": top_p,
                "min_p": min_p,
                "frequency_penalty": frequency_penalty,
                "presence_penalty": presence_penalty,
                "repeat_penalty": repeat_penalty,
            }

            url = self._get_chat_url()
            log_debug(f"chat_with_model: Sending POST to {url}")

            response = requests.post(url, json=payload, timeout=120)
            log_debug(f"chat_with_model: Response {response.status_code}")

            response.raise_for_status()

            result = response.json()
            reply = (
                result.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )

            # Strip thinking tokens (Qwen3 thinking mode)
            reply = strip_thinking_tokens(reply)
            # Strip meta-commentary
            reply = strip_meta_commentary(reply, "")
            log_debug(f"chat_with_model: Reply length (after strip) {len(reply)}")

            self.last_used = datetime.now()
            self.status_changed.emit("Ready")
            return reply

        except requests.exceptions.ConnectionError:
            log_debug("Connection error in chat_with_model")
            self.status_changed.emit("Error: server unreachable")
            return None
        except requests.exceptions.Timeout:
            log_debug("Timeout in chat_with_model")
            self.status_changed.emit("Error: timeout")
            return None
        except Exception as e:
            log_debug(f"Error in chat_with_model: {e}")
            self.status_changed.emit(f"Error: {str(e)[:50]}")
            return None


class CorrectionWindow(QWidget):
    """Main correction window with chat interface"""

    correction_accepted = pyqtSignal(str)
    correction_ready = pyqtSignal(str, str)
    correction_failed_signal = pyqtSignal()
    chat_response_ready = pyqtSignal(str)
    chat_error_signal = pyqtSignal()

    def __init__(self, original_text, model_manager, onnx_manager, config_manager):
        super().__init__()
        self.original_text = original_text
        self.model_manager = model_manager
        self.onnx_manager = onnx_manager
        self.config = config_manager
        self.corrected_text = None
        self.chat_history = []

        self.init_ui()
        self.setup_window()

        # Connect signals for cross-thread communication
        self.correction_ready.connect(self.on_correction_done)
        self.correction_failed_signal.connect(self.on_correction_failed)
        self.chat_response_ready.connect(self.on_chat_response)
        self.chat_error_signal.connect(self.on_chat_error)

        # Start correction in background
        threading.Thread(target=self.perform_initial_correction, daemon=True).start()

    def setup_window(self):
        """Configure window properties"""
        # Keep window on top, and make it frameless and draggable again
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, False)

        # Enable window dragging
        self.dragging = False
        self.drag_position = None

        # Position at cursor relative to active screen
        cursor_pos = QCursor.pos()
        screen = QApplication.screenAt(cursor_pos)
        if not screen:
            screen = QApplication.primaryScreen()
        screen_rect = screen.geometry()

        # Dynamically size to prevent being "massive" on smaller screens
        base_width = 720
        base_height = 650
        max_width = int(screen_rect.width() * 0.8)
        max_height = int(screen_rect.height() * 0.85)
        
        window_width = min(base_width, max_width)
        window_height = min(base_height, max_height)
        self.resize(window_width, window_height)

        # Ensure window stays on screen
        x = min(cursor_pos.x() - window_width // 2, screen_rect.right() - window_width)
        y = min(cursor_pos.y() - window_height // 2, screen_rect.bottom() - window_height)
        x = max(x, screen_rect.x())
        y = max(y, screen_rect.y())

        self.move(x, y)

    def init_ui(self):
        """Initialize the user interface"""
        self.setWindowTitle("AI Text Corrector")
        self.setMinimumSize(400, 350)
        self.resize(720, 650)

        # Premium dark slate-blue theme
        self.setStyleSheet("""
            QWidget#mainWidget {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #0f172a, stop:0.5 #1e293b, stop:1 #0f172a);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 16px;
            }
            QWidget {
                color: #f8fafc;
                font-family: 'Segoe UI', system-ui, sans-serif;
                font-size: 14px;
            }
            QTextEdit {
                background-color: rgba(15, 23, 42, 0.6);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 10px;
                padding: 12px;
                color: #f8fafc;
                font-size: 14px;
                selection-background-color: rgba(56, 189, 248, 0.4);
            }
            QTextEdit:focus {
                border: 1px solid rgba(56, 189, 248, 0.6);
            }
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #0ea5e9, stop:1 #38bdf8);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 8px;
                padding: 10px 20px;
                color: white;
                font-weight: 600;
                font-size: 13px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #0284c7, stop:1 #0ea5e9);
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #0369a1, stop:1 #0284c7);
            }
            QPushButton:disabled {
                background: rgba(255, 255, 255, 0.04);
                color: rgba(255, 255, 255, 0.25);
            }
            QPushButton#secondaryBtn {
                background: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.15);
            }
            QPushButton#secondaryBtn:hover {
                background: rgba(255, 255, 255, 0.1);
                border: 1px solid rgba(255, 255, 255, 0.3);
            }
            QPushButton#dangerBtn {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #ef4444, stop:1 #f87171);
            }
            QPushButton#dangerBtn:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #dc2626, stop:1 #ef4444);
            }
            QLabel {
                color: #f8fafc;
                font-size: 13px;
                background: transparent;
            }
            QLabel#status {
                color: #cbd5e1;
                font-size: 12px;
                font-weight: 600;
                padding: 4px 10px;
                background: rgba(255, 255, 255, 0.05);
                border-radius: 12px;
                border: 1px solid rgba(255, 255, 255, 0.05);
            }
            QLabel#header {
                font-size: 22px;
                font-weight: 800;
                color: #f8fafc;
                letter-spacing: -0.5px;
            }
            QLabel#sectionLabel {
                font-size: 11px;
                font-weight: 800;
                color: #38bdf8;
                text-transform: uppercase;
                letter-spacing: 1.5px;
                padding-bottom: 2px;
            }
            QLineEdit {
                background-color: rgba(15, 23, 42, 0.6);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 10px;
                padding: 10px 14px;
                color: #f8fafc;
                font-size: 14px;
                selection-background-color: rgba(56, 189, 248, 0.4);
            }
            QLineEdit:focus {
                border: 1px solid rgba(56, 189, 248, 0.6);
            }
            QFrame#chatFrame {
                background-color: rgba(0, 0, 0, 0.30);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 12px;
            }
            QFrame#separator {
                background-color: rgba(255, 255, 255, 0.1);
                max-height: 1px;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 6px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 0.15);
                border-radius: 3px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(255, 255, 255, 0.30);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

        # Main widget with background
        main_widget = QWidget()
        main_widget.setObjectName("mainWidget")
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(24, 20, 24, 20)
        main_layout.setSpacing(14)

        # Header row
        header_layout = QHBoxLayout()
        header_layout.setSpacing(12)

        title = QLabel("✦ Text Corrector")
        title.setObjectName("header")
        header_layout.addWidget(title)

        header_layout.addStretch()

        self.model_label = QLabel("")
        self.model_label.setObjectName("status")
        self.model_label.hide()
        header_layout.addWidget(self.model_label)

        self.status_label = QLabel("⏳ Loading model...")
        self.status_label.setObjectName("status")
        header_layout.addWidget(self.status_label)

        settings_btn = QPushButton("⚙")
        settings_btn.setObjectName("secondaryBtn")
        settings_btn.setFixedSize(32, 32)
        settings_btn.setToolTip("Settings")
        settings_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.07);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                font-size: 16px;
                padding: 0;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.15);
            }
        """)
        settings_btn.clicked.connect(self.open_settings)
        header_layout.addWidget(settings_btn)
        main_layout.addLayout(header_layout)

        # Separator
        sep1 = QFrame()
        sep1.setObjectName("separator")
        sep1.setFrameShape(QFrame.HLine)
        main_layout.addWidget(sep1)

        # Original text section
        orig_label = QLabel("ORIGINAL")
        orig_label.setObjectName("sectionLabel")
        main_layout.addWidget(orig_label)

        self.original_edit = QTextEdit()
        self.original_edit.setPlainText(self.original_text)
        self.original_edit.setReadOnly(True)
        self.original_edit.setMinimumHeight(60)
        main_layout.addWidget(self.original_edit)

        # Corrected text section
        corrected_label = QLabel("CORRECTED")
        corrected_label.setObjectName("sectionLabel")
        main_layout.addWidget(corrected_label)

        self.corrected_edit = QTextEdit()
        self.corrected_edit.setPlaceholderText("Processing...")
        self.corrected_edit.setMinimumHeight(60)
        main_layout.addWidget(self.corrected_edit)

        # Chat section
        chat_frame = QFrame()
        chat_frame.setObjectName("chatFrame")
        chat_layout = QVBoxLayout(chat_frame)
        chat_layout.setContentsMargins(14, 12, 14, 12)
        chat_layout.setSpacing(8)

        chat_header = QLabel("REFINE")
        chat_header.setObjectName("sectionLabel")
        chat_layout.addWidget(chat_header)

        # Chat history display
        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        self.chat_display.setPlaceholderText("Ask the AI for further changes...")
        self.chat_display.setMinimumHeight(50)
        self.chat_display.setMaximumHeight(120)
        chat_layout.addWidget(self.chat_display)

        # Chat input
        chat_input_layout = QHBoxLayout()
        chat_input_layout.setSpacing(8)
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText(
            "e.g. 'Make it more formal', 'Shorter please'..."
        )
        self.chat_input.returnPressed.connect(self.send_chat_message)
        chat_input_layout.addWidget(self.chat_input)

        self.send_btn = QPushButton("Send")
        self.send_btn.clicked.connect(self.send_chat_message)
        self.send_btn.setEnabled(False)
        self.send_btn.setFixedWidth(80)
        chat_input_layout.addWidget(self.send_btn)

        chat_layout.addLayout(chat_input_layout)
        main_layout.addWidget(chat_frame)

        main_layout.addStretch()

        # Action buttons
        button_layout = QHBoxLayout()
        button_layout.setSpacing(8)

        self.accept_btn = QPushButton("✓  Accept && Paste")
        self.accept_btn.clicked.connect(self.accept_correction)
        self.accept_btn.setEnabled(False)
        button_layout.addWidget(self.accept_btn)

        self.copy_btn = QPushButton("Copy")
        self.copy_btn.setObjectName("secondaryBtn")
        self.copy_btn.clicked.connect(self.copy_corrected)
        self.copy_btn.setEnabled(False)
        button_layout.addWidget(self.copy_btn)

        reset_btn = QPushButton("Reset")
        reset_btn.setObjectName("secondaryBtn")
        reset_btn.clicked.connect(self.reset_text)
        button_layout.addWidget(reset_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("dangerBtn")
        cancel_btn.clicked.connect(self.close)
        button_layout.addWidget(cancel_btn)

        main_layout.addLayout(button_layout)

        # Set the main widget as the layout
        outer_layout = QVBoxLayout()
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(main_widget)
        self.setLayout(outer_layout)

    def nativeEvent(self, eventType, message):
        """Handle native Windows events for true frameless window resizing"""
        if eventType == b'windows_generic_MSG' or eventType == b'windows_dispatcher_MSG':
            import ctypes
            import ctypes.wintypes
            try:
                msg = ctypes.wintypes.MSG.from_address(message.__int__())
                if msg.message == 0x0084: # WM_NCHITTEST
                    # Use QCursor.pos() which Qt natively translates to logical High DPI multi-monitor coordinates
                    pos = self.mapFromGlobal(QCursor.pos())
                    
                    # 10px margin around the window acts as native resize borders
                    margin = 10
                    left = pos.x() < margin
                    right = pos.x() > self.width() - margin
                    top = pos.y() < margin
                    bottom = pos.y() > self.height() - margin
                    
                    res = 0
                    if left and top: res = 13 # HTTOPLEFT
                    elif right and top: res = 14 # HTTOPRIGHT
                    elif left and bottom: res = 16 # HTBOTTOMLEFT
                    elif right and bottom: res = 17 # HTBOTTOMRIGHT
                    elif left: res = 10 # HTLEFT
                    elif right: res = 11 # HTRIGHT
                    elif top: res = 12 # HTTOP
                    elif bottom: res = 15 # HTBOTTOM
                    
                    if res != 0:
                        return True, res
            except Exception:
                pass
        return super().nativeEvent(eventType, message)

    def keyPressEvent(self, event):
        """Handle keyboard shortcuts"""
        if event.key() == Qt.Key_Escape:
            self.close()
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.corrected_text:
            if self.chat_input.hasFocus():
                self.send_chat_message()
            else:
                self.accept_correction()
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        """Start window dragging — only when clicking on empty chrome, not on child widgets"""
        if event.button() == Qt.LeftButton:
            # Only drag if clicking on the window background, not on a child widget
            child = self.childAt(event.pos())
            if child is None or isinstance(child, QLabel):
                self.dragging = True
                self.drag_position = event.globalPos() - self.pos()
                event.accept()
            else:
                super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Handle window dragging"""
        if self.dragging and event.buttons() == Qt.LeftButton:
            self.move(event.globalPos() - self.drag_position)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """Stop window dragging"""
        if event.button() == Qt.LeftButton and self.dragging:
            self.dragging = False
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def perform_initial_correction(self):
        """Perform the initial text correction - ALWAYS uses ONNX first (T5-first architecture)"""
        log_debug("perform_initial_correction started (background thread)")
        
        # ALWAYS try ONNX first for autocorrect (T5-first architecture)
        corrected = None
        onnx_attempted = False
        used_model = "Unknown"
        
        if self.onnx_manager:
            log_debug("ONNX manager available, using for initial correction")
            onnx_attempted = True
            corrected = self.onnx_manager.proofread(self.original_text)
            
            if corrected:
                log_debug("ONNX correction successful")
                used_model = "T5 (ONNX)"
            else:
                log_debug("ONNX correction returned None - model may not be loaded")
        
        # Only fall back to LLM if ONNX explicitly failed (not just "not configured")
        if not corrected and onnx_attempted:
            log_debug("ONNX failed, falling back to LLM for initial correction")
            corrected = self.model_manager.correct_text(self.original_text)
            if corrected: used_model = "LLM"
        elif not corrected:
            log_debug("ONNX not available, using LLM for initial correction")
            corrected = self.model_manager.correct_text(self.original_text)
            if corrected: used_model = "LLM"
        
        log_debug(
            f"perform_initial_correction finished. Result len: {len(corrected) if corrected else 'None'}"
        )

        if corrected:
            self.corrected_text = corrected
            # Emit signal to update UI from main thread
            self.correction_ready.emit(corrected, used_model)
        else:
            self.correction_failed_signal.emit()

    def on_correction_done(self, corrected, model_name="Unknown"):
        """Handle successful correction with diff highlighting"""
        log_debug("on_correction_done called (main thread)")

        self.model_label.setText(f"Model: {model_name}")
        self.model_label.show()

        # Check for error messages returned from correct_text
        if corrected.startswith("[Error]"):
            self.corrected_edit.setPlainText(corrected)
            self.status_label.setText("⚠ Error")
            self.status_label.setStyleSheet(
                "color: #fbbf24; font-size: 12px; font-weight: 600;"
            )
            self.add_chat_message("system", corrected)
            self.send_btn.setEnabled(True)
            return

        # Show diff-highlighted version
        if corrected.strip() != self.original_text.strip():
            self._show_diff(self.original_text, corrected)
            self.status_label.setText("✓ Corrected")
            self.status_label.setStyleSheet(
                "color: #4ade80; font-size: 12px; font-weight: 600;"
            )
        else:
            self.corrected_edit.setPlainText(corrected)
            self.status_label.setText("No changes needed")
            self.status_label.setStyleSheet(
                "color: rgba(255, 255, 255, 0.5); font-size: 12px;"
            )

        self.accept_btn.setEnabled(True)
        self.copy_btn.setEnabled(True)
        self.send_btn.setEnabled(True)

        # Add system message to chat
        self.add_chat_message(
            "system", "Text corrected. You can ask for further changes above."
        )

    def _show_diff(self, original, corrected):
        """Show corrected text with changed words highlighted in green"""
        import html
        
        orig_words = [t for t in re.split(r'(\s+)', original) if t]
        corr_words = [t for t in re.split(r'(\s+)', corrected) if t]
        matcher = difflib.SequenceMatcher(None, orig_words, corr_words)

        html_parts = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            chunk = "".join(corr_words[j1:j2])
            if not chunk:
                continue
                
            chunk_escaped = html.escape(chunk)
            chunk_html = chunk_escaped.replace('\n', '<br>')
            
            if tag == "equal":
                # Unchanged text
                html_parts.append(chunk_html)
            elif tag in ("replace", "insert"):
                # Changed/Inserted text — highlight in green
                html_parts.append(
                    f'<span style="background-color: rgba(74, 222, 128, 0.2); '
                    f'color: #86efac; border-radius: 3px; padding: 1px 2px;">{chunk_html}</span>'
                )
            # tag == 'delete' — words removed, skip

        html_content = "".join(html_parts)
        self.corrected_edit.setHtml(
            f'<div style="color: #e2e8f0; font-size: 14px; font-family: Segoe UI; white-space: pre-wrap;">{html_content}</div>'
        )

    def on_correction_failed(self):
        """Handle failed correction"""
        self.corrected_edit.setPlainText(
            "Error: Could not correct text.\n\n"
            "Possible causes:\n"
            "• Model failed to load — check the model file path in Settings\n"
            "• Server crashed — check server_log.txt\n"
            "• Model too large for GPU — try a smaller model"
        )
        self.status_label.setText("✗ Failed")
        self.status_label.setStyleSheet(
            "color: #f87171; font-size: 12px; font-weight: 600;"
        )
        self.add_chat_message(
            "system", "Error: Correction failed. Check status message for details."
        )
        self.send_btn.setEnabled(True)

    def open_settings(self):
        """Open settings dialog from the correction window"""
        dialog = SettingsDialog(self.config, parent=self)
        dialog.exec_()

    def add_chat_message(self, role, content):
        """Add a message to the chat display"""
        colors = {"user": "#4CAF50", "model": "#2196F3", "system": "#888"}
        labels = {"user": "You", "model": "AI", "system": "System"}

        color = colors.get(role, "#888")
        label = labels.get(role, role)

        self.chat_display.append(
            f'<span style="color: {color}; font-weight: bold;">{label}:</span> '
            f'<span style="color: #ddd;">{content}</span>'
        )

    def send_chat_message(self):
        """Send a chat message for refinement"""
        user_message = self.chat_input.text().strip()
        if not user_message:
            return

        self.chat_input.clear()
        self.send_btn.setEnabled(False)
        self.add_chat_message("user", user_message)
        self.status_label.setText("Thinking...")

        # Get current text being edited
        current_text = self.corrected_edit.toPlainText()

        # Build conversation history for context
        # Start with STRICT system message for chat
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a text editing assistant. CRITICAL OUTPUT RULES - VIOLATING THESE IS AN ERROR:\n\n"
                    "1. OUTPUT ONLY THE MODIFIED TEXT - absolutely nothing else\n"
                    "2. NEVER add preamble like 'Here is...', 'Sure, ...', 'Certainly', 'I've made the changes', etc.\n"
                    "3. NEVER add explanations, commentary, or questions after the output\n"
                    "4. NEVER wrap output in quotes, markdown code blocks, or labels\n"
                    "5. If user asks a question ABOUT the text (not to modify), answer briefly and helpfully\n"
                    "6. PRESERVE ALL LINE BREAKS AND PARAGRAPH SPACING - do not remove blank lines\n"
                    "7. Maintain original formatting exactly, including multiple line breaks\n\n"
                    "DETECTION RULE:\n"
                    "- If user request is to FIX/CORRECT/MODIFY/REWRITE text → output ONLY the corrected text\n"
                    "- If user request is a QUESTION about the text → answer briefly, then stop\n"
                    "- NEVER say 'The corrected version is:' or similar labels\n\n"
                    "/no_think"
                ),
            },
        ]

        # Add recent chat history for context (last 3 exchanges = 6 messages)
        if len(self.chat_history) > 0:
            messages.extend(self.chat_history[-6:])

        # Add current request
        messages.append(
            {
                "role": "user",
                "content": f"Current text:\n{current_text}\n\nUser request: {user_message}",
            }
        )

        # Store this exchange in history
        self.chat_history.append({"role": "user", "content": user_message})

        # Process in background - ALWAYS use LLM for chat (user wants conversation)
        threading.Thread(
            target=self._process_chat_message,
            args=(messages, user_message),
            daemon=True,
        ).start()

    def _detect_chat_output_type(self, output: str, user_message: str) -> dict:
        """Detect whether chat output is correction or conversation.
        
        Returns dict with:
            - is_correction: bool - True if output is mostly corrected text
            - should_paste: bool - True if output should be pasted to clipboard
            - confidence: float - 0.0 to 1.0 confidence in detection
        """
        if not output or not user_message:
            return {"is_correction": False, "should_paste": False, "confidence": 0.0}
        
        output_lower = output.lower().strip()
        user_lower = user_message.lower().strip()
        
        # Heuristic 1: Check for conversational prefixes (meta-commentary)
        conversational_prefixes = [
            "sure", "here's", "here is", "i've", "i have", "let me",
            "i can", "of course", "certainly", "absolutely", "happy to",
            "glad to", "the corrected", "corrected version", "here you",
            "i'd be", "i will", "i would", "in response", "to answer"
        ]
        has_conversational_prefix = any(output_lower.startswith(p) for p in conversational_prefixes)
        
        # Heuristic 2: Length similarity - corrections are usually similar length to input
        # Extract text to correct from user message if possible
        text_to_correct = ""
        if "current text:" in user_lower:
            # Try to extract the original text portion
            parts = user_message.split("\n")
            for i, part in enumerate(parts):
                if part.strip() and not part.lower().startswith(("current text", "user request", "please", "can you", "could you")):
                    text_to_correct = part.strip()
                    break
        
        output_len = len(output.split())
        input_len = len(text_to_correct.split()) if text_to_correct else len(user_message.split())
        length_ratio = output_len / max(input_len, 1)
        is_similar_length = 0.5 <= length_ratio <= 2.0
        
        # Heuristic 3: Check if output contains explanations or multiple paragraphs
        paragraph_count = output.count("\n\n") + 1
        sentence_count = len(re.findall(r'[.!?]+', output))
        has_explanation = paragraph_count > 2 or sentence_count > 4
        
        # Heuristic 4: Check for question patterns (conversation indicator)
        has_question = "?" in output
        
        # Heuristic 5: Check for common correction patterns
        # If user message contains correction-related keywords
        correction_keywords = ["correct", "fix", "grammar", "spelling", "proofread", "improve", "rewrite", "edit"]
        user_wants_correction = any(kw in user_lower for kw in correction_keywords)
        
        # Decision logic
        confidence = 0.0
        is_correction = False
        should_paste = False
        
        if has_conversational_prefix and not is_similar_length:
            # Clearly conversational
            is_correction = False
            should_paste = False
            confidence = 0.8
        elif has_question or has_explanation:
            # Contains explanations or questions - likely conversation
            is_correction = False
            should_paste = False
            confidence = 0.7
        elif user_wants_correction and is_similar_length and not has_conversational_prefix:
            # User asked for correction and output is similar length without preamble
            is_correction = True
            should_paste = True
            confidence = 0.85
        elif is_similar_length and len(output) < len(user_message) * 3:
            # Output is concise and similar length - likely correction
            is_correction = True
            should_paste = True
            confidence = 0.6
        else:
            # Default to conversation for safety
            is_correction = False
            should_paste = False
            confidence = 0.5
        
        return {
            "is_correction": is_correction,
            "should_paste": should_paste,
            "confidence": confidence
        }

    def _process_chat_message(self, messages, user_message):
        """Process chat message in background - ALWAYS use LLM for chat.
        
        Chat is for conversation about text. Use LLM with strict guardrails.
        """
        response = None
        
        # ALWAYS use LLM for chat (user wants conversation/refinement)
        log_debug("Using LLM for chat message processing")
        response = self.model_manager.chat_with_model(messages)
        
        if response:
            self.corrected_text = response
            
            # Detect output type for proper handling
            output_type = self._detect_chat_output_type(response, user_message)
            log_debug(f"Chat output type detection: {output_type}")
            
            # Store assistant response in history
            self.chat_history.append({"role": "assistant", "content": response})
            # Keep history manageable (last 10 exchanges)
            if len(self.chat_history) > 20:
                self.chat_history = self.chat_history[-20:]
            self.chat_response_ready.emit(response)
        else:
            self.chat_error_signal.emit()

    def on_chat_response(self, response):
        """Handle chat response - always update the text with the model's response"""
        # Always update the corrected text with the model's response
        # This is more reliable than trying to detect if it's a conversation
        self.corrected_edit.setPlainText(response)
        self.model_label.setText("Model: LLM")
        self.model_label.show()
        self.add_chat_message("model", "Text updated ✓")

        self.send_btn.setEnabled(True)
        self.status_label.setText("Updated ✓")

    def on_chat_error(self):
        """Handle chat error"""
        self.add_chat_message("system", "Error: Could not process request.")
        self.send_btn.setEnabled(True)
        self.status_label.setText("Error")

    def reset_text(self):
        """Reset corrected text to original"""
        self.corrected_edit.setPlainText(self.original_text)
        self.corrected_text = self.original_text
        self.status_label.setText("Reset to original")

    def copy_corrected(self):
        """Copy corrected text to clipboard"""
        text = self.corrected_edit.toPlainText()
        if text:
            pyperclip.copy(text)
            self.status_label.setText("✓ Copied")
            self.status_label.setStyleSheet(
                "color: #4ade80; font-size: 12px; font-weight: 600;"
            )

    def accept_correction(self):
        """Accept the correction and paste it"""
        text = self.corrected_edit.toPlainText()
        if text:
            pyperclip.copy(text)
            self.correction_accepted.emit(text)
            self.hide() # hide instead of close to prevent closeEvent from restoring old clipboard too early
            # Simulate paste
            QTimer.singleShot(200, lambda: keyboard.send("ctrl+v"))
            
            # Restore old clipboard after a delay (so paste works first), then close
            def finish_paste():
                self._restore_old_clipboard()
                self.close()
                
            QTimer.singleShot(500, finish_paste)

    def _restore_old_clipboard(self):
        """Restore the original clipboard content"""
        app = QApplication.instance()
        if app and hasattr(app, "old_clipboard"):
            pyperclip.copy(app.old_clipboard)
            del app.old_clipboard

    def closeEvent(self, event):
        """Handle window close - restore old clipboard if not already restored"""
        app = QApplication.instance()
        if app and hasattr(app, "old_clipboard"):
            pyperclip.copy(app.old_clipboard)
            del app.old_clipboard
        event.accept()


class TextCorrectorApp(QApplication):
    """Main application class"""

    trigger_correction = pyqtSignal(str)

    def __init__(self, argv):
        super().__init__(argv)
        self.setQuitOnLastWindowClosed(False)

        # Initialize config
        self.config = ConfigManager()

        # Initialize model manager
        self.model_manager = ModelManager(self.config)
        self.model_manager.status_changed.connect(self.update_status)
        self.model_manager.model_loaded.connect(self.on_model_loaded)
        self.model_manager.model_unloaded.connect(self.on_model_unloaded)

        # Initialize ONNX manager
        self.onnx_manager = ONNXManager(self.config)
        # Connect ONNX status to UI for visibility
        self.onnx_manager.status_changed.connect(self.update_status)

        # Create tray
        self.create_tray_icon()

        # Connect hotkey signal
        self.trigger_correction.connect(self.show_correction_window)

        # Track correction window
        self.correction_window = None

        # Register hotkey (delayed to ensure UI is ready)
        QTimer.singleShot(1000, self.register_hotkey)

        # T5-first architecture: Preload ONNX at startup if configured
        onnx_dir = self.config.get("onnx_model_dir", "")
        if onnx_dir and os.path.exists(onnx_dir):
            # Preload ONNX model at startup for fast autocorrect
            log_debug("ONNX model dir configured, preloading at startup")
            QTimer.singleShot(500, self._preload_onnx_model)
            # Don't load LLM at startup if ONNX is available
            log_debug("ONNX available, skipping LLM auto-load at startup")
        elif self.config.get("model_path"):
            # No ONNX, fall back to LLM
            log_debug("No ONNX configured, loading LLM at startup")
            QTimer.singleShot(500, self._load_model_threaded)
        else:
            # No model configured, show welcome
            QTimer.singleShot(2000, self.show_first_run_dialog)

    def create_tray_icon(self):
        """Create system tray icon and menu"""
        self.tray_icon = QSystemTrayIcon(self._create_icon("#888"), self)
        self.tray_icon.setToolTip("Text Corrector — Not loaded")

        menu = QMenu()
        menu.setStyleSheet("""
            QMenu { background-color: #2b2b2b; color: #fff; border: 1px solid #555; }
            QMenu::item { padding: 6px 20px; }
            QMenu::item:selected { background-color: #0078d4; }
            QMenu::separator { height: 1px; background: #555; margin: 4px 0; }
        """)

        # Status
        self.status_action = menu.addAction("Status: Not loaded")
        self.status_action.setEnabled(False)
        menu.addSeparator()

        # Quick Model Selector submenu
        self.model_menu = menu.addMenu("Switch Model")
        self._rebuild_model_menu()

        menu.addSeparator()

        # Load / Unload
        load_action = menu.addAction("Load Model")
        load_action.triggered.connect(self._load_model_threaded)

        unload_action = menu.addAction("Unload Model")
        unload_action.triggered.connect(self.model_manager.unload_model)

        # Keep loaded toggle
        self.keep_loaded_action = menu.addAction("Keep Model Loaded")
        self.keep_loaded_action.setCheckable(True)
        self.keep_loaded_action.setChecked(self.config.get("keep_model_loaded", False))
        self.keep_loaded_action.toggled.connect(self.toggle_keep_loaded)

        menu.addSeparator()

        # Test Hotkey (manual trigger)
        test_hotkey_action = menu.addAction("Test Hotkey")
        test_hotkey_action.triggered.connect(self.test_hotkey)

        menu.addSeparator()

        # Settings
        settings_action = menu.addAction("Settings...")
        settings_action.triggered.connect(self.show_settings)

        # Select Model (file browser)
        select_model_action = menu.addAction("Browse for Model...")
        select_model_action.triggered.connect(self.select_model_dialog)

        menu.addSeparator()

        # Startup
        startup_action = menu.addAction("Add to Startup")
        startup_action.triggered.connect(self.add_to_startup)

        remove_startup_action = menu.addAction("Remove from Startup")
        remove_startup_action.triggered.connect(self.remove_from_startup)

        menu.addSeparator()

        # Exit
        exit_action = menu.addAction("Exit")
        exit_action.triggered.connect(self.quit_app)

        self.tray_icon.setContextMenu(menu)
        self.tray_icon.show()

    def _rebuild_model_menu(self):
        """Rebuild the quick model selector submenu"""
        self.model_menu.clear()

        # Discover models in app directory
        models = discover_models()
        # Also add recent models that might be elsewhere
        recent = self.config.get("recent_models", [])

        # Normalize all paths to avoid duplicates with different slash formats
        normalized_models = set()
        unique_models = []
        for m in models + recent:
            if os.path.exists(m):
                norm_path = os.path.normpath(os.path.abspath(m)).lower()
                if norm_path not in normalized_models:
                    normalized_models.add(norm_path)
                    unique_models.append(m)
        models = unique_models

        current_model = self.config.get("model_path", "")

        if not models:
            no_model_action = self.model_menu.addAction("No models found")
            no_model_action.setEnabled(False)
            return

        model_group = QActionGroup(self.model_menu)
        model_group.setExclusive(True)

        for model_path in models:
            name = friendly_model_name(model_path)
            action = self.model_menu.addAction(name)
            action.setCheckable(True)
            model_group.addAction(action)
            # Case-insensitive comparison on Windows
            if current_model:
                is_checked = (
                    os.path.normpath(model_path).lower()
                    == os.path.normpath(current_model).lower()
                )
                action.setChecked(is_checked)
            else:
                action.setChecked(False)
                
            # Use default argument to capture model_path in lambda
            action.triggered.connect(
                lambda checked, mp=model_path: self._switch_model(mp)
            )

    def _create_icon(self, color):
        """Create an icon combining the logo and status color"""
        from PyQt5.QtGui import QPainter, QBrush, QColor, QPixmap, QIcon, QPen

        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        logo_path = str(SCRIPT_DIR / "logo.png")
        import os
        if os.path.exists(logo_path):
            logo = QPixmap(logo_path)
            logo = logo.scaled(32, 32, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            painter.drawPixmap(0, 0, logo)

            # Draw status dot
            painter.setBrush(QBrush(QColor(color)))
            pen = QPen(QColor("#000000"))
            pen.setWidth(1)
            painter.setPen(pen)
            painter.drawEllipse(18, 18, 12, 12)
        else:
            # Fallback to plain circle
            painter.setBrush(QBrush(QColor(color)))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(4, 4, 24, 24)

        painter.end()
        return QIcon(pixmap)

    def toggle_keep_loaded(self, checked):
        """Toggle keep model loaded setting"""
        self.config.set("keep_model_loaded", checked)
        if checked:
            self.tray_icon.showMessage(
                "Text Corrector",
                "Model will stay loaded in VRAM",
                QSystemTrayIcon.Information,
                2000,
            )

    def show_first_run_dialog(self):
        """Show dialog on first run to configure model"""
        msg = QMessageBox()
        msg.setWindowTitle("Text Corrector - First Run")
        msg.setText("Welcome to Text Corrector!")
        msg.setInformativeText(
            "Please configure your settings:\n\n"
            "1. Select a GGUF model file (or use the included Gemma model)\n"
            "2. The llama.cpp server is included\n\n"
            "Would you like to open settings now?"
        )
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg.setDefaultButton(QMessageBox.Yes)

        if msg.exec_() == QMessageBox.Yes:
            self.show_settings()

    def show_settings(self):
        """Show settings dialog"""
        dialog = SettingsDialog(self.config, re_register_cb=self.register_hotkey)
        dialog.settings_changed.connect(self.on_settings_changed)
        dialog.exec_()

    def on_settings_changed(self):
        """Handle settings changes"""
        # Unload and reload with new settings
        self.model_manager.unload_model()
        self._rebuild_model_menu()
        self.register_hotkey()

        # Reload model automatically if a model path is set
        if self.config.get("model_path"):
            QTimer.singleShot(500, self._load_model_threaded)

    def select_model_dialog(self):
        """Quick model selection dialog"""
        path, _ = QFileDialog.getOpenFileName(
            None, "Select GGUF Model", "", "GGUF Models (*.gguf)"
        )

        if path:
            self.config.set("model_path", path)
            self.config.add_recent_model(path)

            # Unload current model to force reload with new one
            self.model_manager.unload_model()
            self._rebuild_model_menu()

            self.tray_icon.showMessage(
                "Text Corrector",
                f"Model selected:\n{os.path.basename(path)}",
                QSystemTrayIcon.Information,
                3000,
            )

    def _load_model_threaded(self):
        """Load model in background thread"""
        threading.Thread(target=self.model_manager.load_model, daemon=True).start()

    def _preload_onnx_model(self):
        """Preload ONNX model at startup for fast autocorrect"""
        log_debug("Preloading ONNX model at startup")
        if self.onnx_manager:
            success = self.onnx_manager.load_model()
            if success:
                log_debug("ONNX model preloaded successfully")
                self.tray_icon.setIcon(self._create_icon("#4CAF50"))
                onnx_dir = os.path.basename(self.config.get("onnx_model_dir", ""))
                self.tray_icon.setToolTip(f"Text Corrector — ONNX Ready ({onnx_dir})")
            else:
                log_debug("Failed to preload ONNX model")
                self.tray_icon.setIcon(self._create_icon("#f44336"))
                self.tray_icon.setToolTip("Text Corrector — ONNX Load Failed")

    def on_model_loaded(self):
        """Handle model loaded event (LLM)"""
        self.tray_icon.setIcon(self._create_icon("#4CAF50"))
        model_name = friendly_model_name(self.config.get("model_path", ""))
        self.tray_icon.setToolTip(f"Text Corrector — {model_name}")

    def on_model_unloaded(self):
        """Handle model unloaded event"""
        self.tray_icon.setIcon(self._create_icon("#888"))
        self.tray_icon.setToolTip("Text Corrector — Not loaded")

    def register_hotkey(self):
        """Register global hotkey"""
        log_debug("Registering hotkey...")
        try:
            keyboard.unhook_all_hotkeys()
            log_debug("Cleared existing hotkeys")
        except Exception as e:
            log_debug(f"Error clearing hotkeys: {e}")
            pass

        hotkey = self.config.get("hotkey", "alt+shift+t")
        log_debug(f"Attempting to register hotkey: {hotkey}")

        # Try multiple formats
        hotkey_formats = [
            hotkey,  # Original format
            hotkey.replace("+", " "),  # Space-separated
            hotkey.replace("alt", "alt")
            .replace("shift", "shift")
            .replace("ctrl", "ctrl"),  # Ensure lowercase
        ]

        registered = False
        for hk in hotkey_formats:
            try:
                log_debug(f"Trying format: {hk}")
                # Try registering with suppress=False (doesn't block the key)
                keyboard.add_hotkey(
                    hk, self.hotkey_triggered, suppress=False, trigger_on_release=False
                )
                log_debug(f"Hotkey '{hk}' registered successfully")
                registered = True

                # Show success message in tray
                self.tray_icon.showMessage(
                    "Text Corrector",
                    f"Hotkey registered: {hk}",
                    QSystemTrayIcon.Information,
                    2000,
                )
                break
            except Exception as e:
                log_debug(f"Failed with format '{hk}': {e}")
                continue

        if not registered:
            error_msg = f"Failed to register hotkey '{hotkey}' with all formats"
            log_debug(error_msg)
            print(error_msg)
            self.tray_icon.showMessage(
                "Text Corrector",
                f"Failed to register hotkey: {hotkey}\n\nTry running as administrator",
                QSystemTrayIcon.Warning,
                5000,
            )

    def test_hotkey(self):
        """Test the hotkey functionality manually"""
        log_debug("Test hotkey triggered from menu")
        self.tray_icon.showMessage(
            "Text Corrector",
            "Testing hotkey... Check if window opens.",
            QSystemTrayIcon.Information,
            2000,
        )
        # Simulate what the hotkey would do
        test_text = "This is a test. The hotkey is working!"
        self.old_clipboard = pyperclip.paste()
        pyperclip.copy(test_text)
        self.trigger_correction.emit(test_text)

    def hotkey_triggered(self):
        """Handle hotkey press - copy text first, then process"""
        log_debug("HOTKEY TRIGGERED!")
        try:
            # Release modifier keys FIRST so Ctrl+C isn't corrupted
            # (Alt+Shift are still held from the hotkey combo)
            try:
                keyboard.release("alt")
                keyboard.release("shift")
            except Exception:
                pass
            time.sleep(0.15)  # Wait for OS to register key releases

            # Save current clipboard content (will restore after window closes)
            self.old_clipboard = pyperclip.paste()
            log_debug(f"Saved old clipboard (length: {len(self.old_clipboard)})")

            # Clear clipboard to detect if copy succeeds
            pyperclip.copy("")
            time.sleep(0.05)

            # Send Ctrl+C to copy selected text
            log_debug("Sending Ctrl+C")
            keyboard.send("ctrl+c")

            # Wait for clipboard to update (poll with timeout)
            selected_text = ""
            max_attempts = 20  # 1 second total (20 * 0.05s)
            for i in range(max_attempts):
                time.sleep(0.05)
                try:
                    current = pyperclip.paste()
                    if current != "":
                        selected_text = current
                        log_debug(f"Clipboard updated! Length: {len(selected_text)}")
                        break
                except Exception as clip_err:
                    log_debug(f"Clipboard read error: {clip_err}")
                    pass

            # DON'T restore clipboard yet - wait until window closes
            # Store selected text for later

            if selected_text and selected_text.strip():
                log_debug(
                    f"Emitting trigger_correction signal with text length: {len(selected_text)}"
                )
                # Emit signal to show window from main thread
                self.trigger_correction.emit(selected_text.strip())
            else:
                log_debug("No text selected")
                # Restore clipboard since no text was selected
                if hasattr(self, "old_clipboard"):
                    pyperclip.copy(self.old_clipboard)
                self.tray_icon.showMessage(
                    "Text Corrector",
                    "No text selected. Select text first, then press the hotkey.",
                    QSystemTrayIcon.Warning,
                    3000,
                )
        except Exception as e:
            error_msg = f"Hotkey error: {e}"
            log_debug(error_msg)
            print(error_msg)

    def show_correction_window(self, text):
        """Show the correction window"""
        if self.correction_window:
            self.correction_window.close()

        self.correction_window = CorrectionWindow(text, self.model_manager, self.onnx_manager, self.config)
        self.correction_window.show()
        self.correction_window.raise_()
        self.correction_window.activateWindow()

    def update_status(self, status, color=None):
        """Update tray status"""
        # Handle both single arg (from ModelManager) and two args (from ONNXManager)
        if isinstance(status, tuple):
            status, color = status
        
        self.status_action.setText(f"Status: {status}")
        
        # Determine color based on status text if not provided
        if color is None:
            if "error" in status.lower() or "failed" in status.lower():
                color = "#ef4444"
            elif "loading" in status.lower() or "starting" in status.lower():
                color = "#f59e0b"
            elif "ready" in status.lower() or "loaded" in status.lower():
                color = "#10b981"
            else:
                color = "#888"
        
        self.tray_icon.setIcon(self._create_icon(color))
        self.tray_icon.setToolTip(f"Text Corrector — {status}")

    def add_to_startup(self):
        """Add application to Windows startup"""
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE,
            )
            script_path = os.path.abspath(__file__)
            python_path = sys.executable

            # Use pythonw for no console
            pythonw_path = python_path.replace("python.exe", "pythonw.exe")
            if os.path.exists(pythonw_path):
                cmd = f'"{pythonw_path}" "{script_path}"'
            else:
                cmd = f'"{python_path}" "{script_path}"'

            winreg.SetValueEx(key, "TextCorrector", 0, winreg.REG_SZ, cmd)
            winreg.CloseKey(key)
            self.tray_icon.showMessage(
                "Text Corrector",
                "Added to Windows startup",
                QSystemTrayIcon.Information,
                3000,
            )
        except Exception as e:
            self.tray_icon.showMessage(
                "Text Corrector",
                f"Failed to add to startup: {e}",
                QSystemTrayIcon.Warning,
                3000,
            )

    def remove_from_startup(self):
        """Remove application from Windows startup"""
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE,
            )
            try:
                winreg.DeleteValue(key, "TextCorrector")
                self.tray_icon.showMessage(
                    "Text Corrector",
                    "Removed from Windows startup",
                    QSystemTrayIcon.Information,
                    3000,
                )
            except FileNotFoundError:
                self.tray_icon.showMessage(
                    "Text Corrector",
                    "Not found in startup",
                    QSystemTrayIcon.Information,
                    3000,
                )
            winreg.CloseKey(key)
        except Exception as e:
            self.tray_icon.showMessage(
                "Text Corrector",
                f"Failed: {e}",
                QSystemTrayIcon.Warning,
                3000,
            )

    def quit_app(self):
        """Clean up and quit application"""
        self.model_manager.unload_model()
        keyboard.unhook_all_hotkeys()
        self.quit()


if __name__ == "__main__":
    if hasattr(Qt, 'AA_EnableHighDpiScaling'):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, 'AA_UseHighDpiPixmaps'):
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    # Single instance check
    import socket

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 47521))
    except:
        print("Another instance is already running")
        sys.exit(1)

    app = TextCorrectorApp(sys.argv)
    sys.exit(app.exec_())
