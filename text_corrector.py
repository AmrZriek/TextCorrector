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
from pynput import keyboard as pynput_keyboard
from pynput.keyboard import Key, Listener, Controller
import queue
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
    QMessageBox,
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

_log_lock = threading.Lock()

def log(msg: str):
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with _log_lock:
            with open(DEBUG_LOG, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def _model_size_billions(model_path: str) -> float | None:
    """Parse the parameter count in billions from a GGUF filename.

    Examples:
        'qwen2.5-3b-instruct-q4_k_m.gguf'     → 3.0
        'gemma-4-E2B-it-UD-Q4_K_XL.gguf'      → 2.0
        'gemma3-270m-grammar-q8_0.gguf'       → 0.27
        'Llama-3.2-1B-Instruct-Q4_K_M.gguf'   → 1.0
        'phi-mini-3.8b-Q4.gguf'               → 3.8

    Returns None if no size marker is found. Used for UI-side sanity warnings
    — a 270M model will produce tokenizer garbage in patch mode, and we want
    to warn the user upfront rather than after a bad correction.
    """
    if not model_path:
        return None
    name = os.path.basename(model_path).lower()
    # Match patterns like "3b", "2.5b", "E2B" (effective 2B), "270m", "1.5m"
    # E-prefix is used by Google's "effective" size branding (E2B = ~2B effective)
    m = re.search(r"(?:^|[^a-z0-9])e?(\d+(?:\.\d+)?)([bm])(?:[^a-z]|$)", name)
    if not m:
        return None
    value = float(m.group(1))
    unit = m.group(2)
    return value if unit == "b" else value / 1000.0


# Models smaller than this won't reliably follow the patch-JSON format and
# will produce tokenizer garbage or few-shot echoes. The number is empirical —
# Gemma 2B works, a 270M grammar model does not.
_MIN_RELIABLE_MODEL_B = 1.0


def _find_shipped_llama_server() -> str:
    """Locate a llama-server binary shipped alongside the app.

    Release ZIPs extract to a folder containing TextCorrector.exe plus a
    sibling directory like `llama-b8728-bin-win-cuda-12.4-x64/` that holds
    `llama-server.exe`. Users shouldn't have to point Settings at it manually —
    if we can find it next to the app, auto-use it. Searched locations, in
    priority order:
      1. Legacy `llama_cpp/` folder (pre-v4 release layout)
      2. Any sibling folder matching `llama*` containing the server binary
    Returns an empty string if nothing is found.
    """
    # Legacy location first — if someone upgrades in place, keep their setup
    legacy = LLAMA_CPP_DIR / SERVER_EXE
    if legacy.exists():
        return str(legacy)
    # Scan SCRIPT_DIR for any folder that looks like an unpacked llama.cpp build
    try:
        for entry in SCRIPT_DIR.iterdir():
            if entry.is_dir() and "llama" in entry.name.lower():
                candidate = entry / SERVER_EXE
                if candidate.exists():
                    return str(candidate)
    except Exception:
        pass
    return ""


_COMPILED_THINKING_PATTERNS = [
    re.compile(r"<think>.*?</think>", re.DOTALL),
    re.compile(r"<thinking>.*?</thinking>", re.DOTALL),
    re.compile(r"<reasoning>.*?</reasoning>", re.DOTALL),
]

_COMPILED_UNCLOSED_PATTERNS = [
    re.compile(r"<think>.*", re.DOTALL),
    re.compile(r"<thinking>.*", re.DOTALL),
    re.compile(r"<reasoning>.*", re.DOTALL),
]

_PREAMBLE_PATTERNS = [
    r"^(?:Here(?:\'s| is) the corrected (?:text|version)[:\.]?\s*\n?)",
    r"^(?:Sure[,!]? [Hh]ere(?:\'s| is) the corrected (?:text|version)[:\.]?\s*\n?)",
    r"^(?:Corrected (?:text|version)[:\.]?\s*\n?)",
    r"^(?:The corrected (?:text|version)[:\.]?\s*\n?)",
    r"^(?:I(?:\'ve| have) corrected the (?:text|text for you)[:\.]?\s*\n?)",
    r"^(?:Below is the corrected (?:text|version)[:\.]?\s*\n?)",
    r"^(?:This is the corrected (?:text|version)[:\.]?\s*\n?)",
    r"^(?:I\'ve proofread and refined the text[:\.]?\s*\n?)",
    r"^(?:I\'ve made the following corrections[:\.]?\s*\n?)",
    r"^\*\*Corrected(?: text)?\*\*[:\.]?\s*\n?",
    r"^#+\s*Corrected(?: text)?[:\.]?\s*\n?",
    r"^[-*]{3,}\s*\n?",
    r"^(?:Here are the corrections?[:\.]?\s*\n?)",
    r"^(?:The refined (?:text|version)[:\.]?\s*\n?)",
    r"^(?:I\'ve reviewed and corrected[:\.]?\s*\n?)",
    r"^(?:I\'ve proofread (?:and refined )?your text[:\.]?\s*\n?)",
    r"^(?:Here is the refined (?:text|version)[:\.]?\s*\n?)",
    r"^(?:The text has been corrected[:\.]?\s*\n?)",
    r"^(?:Your text,? corrected[:\.]?\s*\n?)",
]
_COMPILED_PREAMBLES = [re.compile(p, re.IGNORECASE) for p in _PREAMBLE_PATTERNS]


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

    cleaned = text
    # Remove various thinking block formats (including multiline content)
    for pattern in _COMPILED_THINKING_PATTERNS:
        cleaned = pattern.sub("", cleaned)

    # Also handle unclosed thinking tags (model may not close them)
    for pattern in _COMPILED_UNCLOSED_PATTERNS:
        cleaned = pattern.sub("", cleaned)

    return cleaned.strip()


def strip_meta_commentary(text: str, original: str = "") -> str:
    """Strip common meta-commentary prefixes that models add."""
    if not text:
        return text
    cleaned = text
    for pattern in _COMPILED_PREAMBLES:
        cleaned = pattern.sub("", cleaned)
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


def _is_corrupt_output(raw: str) -> bool:
    """Detect tokenizer-garbage output from undersized/incompatible models.

    Logged examples from a 270M model:
        'samsung\\x7freleased a new phone'
        'samsung[UNK_BYTE_0xe29681▁released]released'
        'The[UNK_BYTE_0xe29681▁phone]phone[UNK_BYTE_0xe29681▁was]was...'

    These show raw BPE/SentencePiece artifacts leaking through. Treating them
    as "valid corrections" is worse than returning the original text, since
    they silently corrupt the user's clipboard paste.
    """
    if not raw:
        return False
    # Known tokenizer artifact markers
    if "[UNK_BYTE_" in raw:
        return True
    # DEL / NAK / SOH / other C0 control chars (except \n\t\r)
    if any(ord(c) < 0x20 and c not in "\n\t\r" for c in raw):
        return True
    if "\x7f" in raw:
        return True
    # Multiple ▁ (SentencePiece word marker U+2581) means the tokenizer's
    # internal representation is leaking, not real output
    if raw.count("\u2581") >= 2:
        return True
    return False


# Few-shot example outputs that small models occasionally echo verbatim instead
# of actually processing the user's text. If the model returns ONLY one of
# these (ignoring whitespace/case) for arbitrary input, we reject it.
_FEWSHOT_ECHOES = {
    "i don't know if it's gonna work.",
    "i dont know if its gonna work",
    "the project was delayed because of bad weather.",
    "the project were delayed because of bad weather",
    "samsung released a new phone",
    "samsung released a new phone.",
}


def _is_fewshot_echo(raw: str, original: str) -> bool:
    """Return True if `raw` is a verbatim few-shot example output unrelated to
    the user's actual input. Tiny models (<1B params) frequently do this when
    they fail to follow the instruction — they just regurgitate the last
    assistant message from the prompt.
    """
    if not raw:
        return False
    normalized = raw.strip().lower()
    if normalized not in _FEWSHOT_ECHOES:
        return False
    # If the user's input happens to actually match the example, it's not an
    # echo — it's a legitimate correction. Compare loosely to avoid false
    # positives on inputs that are close to but not exactly the example.
    orig_normalized = original.strip().lower()
    # Any meaningful word overlap means it could be genuine
    orig_words = set(re.findall(r"\w+", orig_normalized))
    echo_words = set(re.findall(r"\w+", normalized))
    if orig_words and echo_words:
        overlap_ratio = len(orig_words & echo_words) / len(orig_words | echo_words)
        if overlap_ratio > 0.5:
            return False
    return True
    return True


_CONTRACTIONS_MAP = {
    r"(?<![a-zA-Z])dont(?![a-zA-Z])": "don't",
    r"(?<![a-zA-Z])doesnt(?![a-zA-Z])": "doesn't",
    r"(?<![a-zA-Z])didnt(?![a-zA-Z])": "didn't",
    r"(?<![a-zA-Z])cant(?![a-zA-Z])": "can't",
    r"(?<![a-zA-Z])couldnt(?![a-zA-Z])": "couldn't",
    r"(?<![a-zA-Z])wouldnt(?![a-zA-Z])": "wouldn't",
    r"(?<![a-zA-Z])shouldnt(?![a-zA-Z])": "shouldn't",
    r"(?<![a-zA-Z])wont(?![a-zA-Z])": "won't",
    r"(?<![a-zA-Z])wasnt(?![a-zA-Z])": "wasn't",
    r"(?<![a-zA-Z])werent(?![a-zA-Z])": "weren't",
    r"(?<![a-zA-Z])isnt(?![a-zA-Z])": "isn't",
    r"(?<![a-zA-Z])arent(?![a-zA-Z])": "aren't",
    r"(?<![a-zA-Z])hasnt(?![a-zA-Z])": "hasn't",
    r"(?<![a-zA-Z])havent(?![a-zA-Z])": "haven't",
    r"(?<![a-zA-Z])hadnt(?![a-zA-Z])": "hadn't",
    r"(?<![a-zA-Z])Im(?![a-zA-Z])": "I'm",
    r"(?<![a-zA-Z])Ive(?![a-zA-Z])": "I've",
    r"(?<![a-zA-Z])Id(?![a-zA-Z])": "I'd",
    r"(?<![a-zA-Z])Ill(?![a-zA-Z])": "I'll",
    r"(?<![a-zA-Z])youre(?![a-zA-Z])": "you're",
    r"(?<![a-zA-Z])theyre(?![a-zA-Z])": "they're",
    r"(?<![a-zA-Z])were(?![a-zA-Z])(?=\s+(?:going|gonna|not|still|just|also|always|never|almost|about))": "we're",
    r"(?<![a-zA-Z])hes(?![a-zA-Z])": "he's",
    r"(?<![a-zA-Z])shes(?![a-zA-Z])": "she's",
    r"(?<![a-zA-Z])thats(?![a-zA-Z])": "that's",
    r"(?<![a-zA-Z])whats(?![a-zA-Z])": "what's",
    r"(?<![a-zA-Z])lets(?![a-zA-Z])(?=\s+(?:\w))": "let's",
    r"(?<![a-zA-Z])theres(?![a-zA-Z])": "there's",
}
_COMPILED_CONTRACTIONS = [(re.compile(p, re.IGNORECASE), r) for p, r in _CONTRACTIONS_MAP.items()]
_I_PATTERN = re.compile(r"(?<![a-zA-Z])i(?![a-zA-Z'])")
_CAP_PATTERN = re.compile(r'([.?!]\s+)([a-z])')


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

    # Post-process: catch common fixes the LLM may have missed
    post_applied = 0

    # 1. Standalone lowercase 'i' → 'I' (when LLM missed it)
    if _I_PATTERN.search(result):
        result = _I_PATTERN.sub("I", result)
        post_applied += 1
        log("[PATCH] Post-fix: standalone 'i' → 'I'")

    # 2. Capitalize first letter of first sentence if it's lowercase
    if result and result[0].islower():
        result = result[0].upper() + result[1:]
        post_applied += 1
        log("[PATCH] Post-fix: capitalized first letter")

    # 3. Fix common contractions missing apostrophes
    for c_pat, repl in _COMPILED_CONTRACTIONS:
        if c_pat.search(result):
            # Preserve original case for the replacement where sensible
            def _contraction_repl(m, _repl=repl):
                # If original was all uppercase, uppercase the replacement
                if m.group().isupper():
                    return _repl.upper()
                # If original started uppercase (and replacement does too), keep it
                if m.group()[0].isupper():
                    return _repl[0].upper() + _repl[1:]
                return _repl
            result = c_pat.sub(_contraction_repl, result)
            post_applied += 1
            log(f"[PATCH] Post-fix: contraction '{c_pat.pattern}' → '{repl}'")

    # 4. Capitalize first word after sentence-ending punctuation (.?!)
    def _cap_after_sentence(m):
        return m.group(1) + m.group(2).upper()
    if _CAP_PATTERN.search(result):
        result = _CAP_PATTERN.sub(_cap_after_sentence, result)
        post_applied += 1
        log("[PATCH] Post-fix: capitalized after sentence-ending punctuation")

    # 5. Restore trailing punctuation if stripped
    if original and original[-1] in ".?!":
        if not result.endswith(original[-1]):
            # If the result ends with some other punctuation, don't double up
            if result and result[-1] not in ".?!":
                result += original[-1]
                post_applied += 1
                log(f"[PATCH] Post-fix: restored trailing '{original[-1]}'")

    if post_applied:
        log(f"[PATCH] Post-processing applied {post_applied} additional fix(es)")

    log(f"[PATCH] Applied {patches_applied}/{len(patches)} patches successfully")
    return result


def _chunk_text_by_sentences(text: str, max_words: int) -> list[tuple[str, str]]:
    """Split text at sentence/paragraph boundaries into chunks of ≤ max_words.

    Why chunking is needed:
        Long texts can overflow the LLM context window (e.g. 4096 tokens).
        When input consumes most of the context, there aren't enough tokens left
        for the patch JSON output — causing truncated/missing corrections,
        especially toward the end of the text. By splitting into chunks that each
        fit comfortably, every portion of the text gets a full correction pass.

    Returns a list of (chunk_text, trailing_separator) tuples.
    The separator preserves original whitespace/newlines between chunks so the
    corrected text can be reassembled without altering formatting:
        ''.join(corrected + sep for corrected, sep in results)
    """
    import re

    if len(text.split()) <= max_words:
        return [(text, "")]

    # Split at sentence-ending punctuation followed by whitespace, OR at newlines.
    # The capturing group keeps separators in the result so we can preserve them
    # when reassembling. Sentence boundaries are chosen because they're natural
    # correction boundaries — errors within a sentence don't depend on the next one.
    parts = re.split(r"((?<=[.!?])\s+|\n+)", text)

    # re.split with a capturing group alternates: [text, sep, text, sep, ..., text]
    # Pair them up into (sentence_text, separator_after) tuples
    sentences: list[tuple[str, str]] = []
    for i in range(0, len(parts), 2):
        sent = parts[i]
        sep = parts[i + 1] if i + 1 < len(parts) else ""
        sentences.append((sent, sep))

    # Greedily pack sentences into chunks without exceeding max_words.
    # cur_sep tracks the separator between the last sentence in the current chunk
    # and the next sentence — this becomes the inter-chunk separator if we split here.
    chunks: list[tuple[str, str]] = []
    cur_text = ""
    cur_sep = ""
    cur_words = 0

    for sent, sep in sentences:
        wc = len(sent.split())
        candidate = cur_text + cur_sep + sent if cur_text else sent
        candidate_words = cur_words + wc

        if candidate_words > max_words and cur_text:
            # Adding this sentence would exceed budget — finalize current chunk
            chunks.append((cur_text, cur_sep))
            cur_text = sent
            cur_sep = sep
            cur_words = wc
        else:
            cur_text = candidate
            cur_sep = sep
            cur_words = candidate_words

    if cur_text:
        # Last chunk gets empty separator (nothing follows it)
        chunks.append((cur_text, ""))

    return chunks


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

    # Salvage valid objects from truncated or malformed output
    salvaged = []
    # Match anything that looks like a JSON object { ... }
    # non-greedy to avoid matching the whole array as one object
    for match in re.finditer(r'\{[^{}]*\}', cleaned):
        try:
            obj = json.loads(match.group(0))
            if isinstance(obj, dict) and "old" in obj and "new" in obj:
                salvaged.append(obj)
        except Exception:
            pass

    if salvaged:
        return salvaged

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
    "context_size": 12800,
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
            # pynput doesn't need unhooking — just ignore global hotkey

    def focusOutEvent(self, e):
        if self._recording:
            self._recording = False
            self.setStyleSheet(self._IDLE)
            self._refresh()
            # pynput doesn't need re-registration
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
    # Fires after load if the model is too small to reliably follow the patch
    # prompt format. Parent app surfaces this as a tray message so users don't
    # silently get garbage corrections.
    model_warning = pyqtSignal(str)

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
        # Actual context size as reported by llama-server's /props endpoint
        # after load. This may differ from cfg["context_size"] when the model's
        # metadata caps n_ctx lower than the user-requested value (common with
        # older GGUFs). None until the first successful load.
        self.actual_ctx_size: int | None = None

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
    def load_model(self, force_cpu: bool = False) -> bool:
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

        # Resolve llama-server path. The shipped build has `llama-server` inside
        # a sibling folder like `llama-b8728-bin-win-cuda-12.4-x64/`, not the
        # legacy `llama_cpp/` dir. Scan SCRIPT_DIR for any `llama*/llama-server`
        # so the app is plug-and-play for users who just unzipped the release.
        server_path = self.cfg.get("llama_server_path", "")
        if not server_path or not Path(server_path).exists():
            server_path = _find_shipped_llama_server()
            if server_path:
                log(f"[{self.label}] Auto-detected llama-server: {server_path}")
                # Persist so the auto-detect only happens once
                self.cfg.set("llama_server_path", server_path)
            else:
                self.loading = False
                self.status_changed.emit("llama-server not found")
                return False

        gpu_detected = has_nvidia()
        log(f"[{self.label}] GPU detection: has_nvidia()={gpu_detected}")
        gpu_layers = 0 if force_cpu else self.cfg.get("gpu_layers", 99)
        if force_cpu:
            log(f"[{self.label}] force_cpu=True — overriding gpu_layers to 0")
        elif not gpu_detected and gpu_layers > 0:
            log(f"[{self.label}] nvidia-smi not found but gpu_layers={gpu_layers} from config — attempting GPU (error recovery will retry CPU on failure)")
        log(f"[{self.label}] Using gpu_layers={gpu_layers}")
        ctx = self.cfg.get("context_size", 4096)
        host = self.cfg.get("server_host", "127.0.0.1")
        port = self.cfg.get("server_port", 8080)

        # Pass all sampling defaults on the CLI too. llama-server uses these as
        # fallbacks when a request omits a given field, and some endpoints (e.g.
        # /completion from non-SDK callers) only honor CLI values. The per-request
        # payloads still override these when set — this just prevents hardcoded
        # server defaults from masking user settings.
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
            "--temp", str(self.cfg.get("temperature", 0.1)),
            "--top-k", str(self.cfg.get("top_k", 40)),
            "--top-p", str(self.cfg.get("top_p", 0.95)),
            "--min-p", str(self.cfg.get("min_p", 0.05)),
            "--repeat-penalty", str(self.cfg.get("repeat_penalty", 1.0)),
            "--frequency-penalty", str(self.cfg.get("frequency_penalty", 0.0)),
            "--presence-penalty", str(self.cfg.get("presence_penalty", 0.0)),
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

            # Ask the server for the *actual* loaded context size. The user's
            # requested --ctx-size is a ceiling, not a guarantee — some GGUFs
            # cap n_ctx lower in their metadata. Chunking math must use the
            # real value or we'll overflow and the model drops tail tokens.
            try:
                pr = requests.get(self._base_url() + "/props", timeout=3)
                if pr.ok:
                    jp = pr.json()
                    # llama.cpp exposes n_ctx either at the top level or under
                    # default_generation_settings depending on server version
                    n_ctx = (
                        jp.get("default_generation_settings", {}).get("n_ctx")
                        or jp.get("n_ctx")
                    )
                    if isinstance(n_ctx, int) and n_ctx > 0:
                        self.actual_ctx_size = n_ctx
                        log(f"[{self.label}] /props reports n_ctx={n_ctx}")
            except Exception as e:
                log(f"[{self.label}] /props fetch failed (non-fatal): {e}")

            # Warn if the model is too small for reliable patch-mode output.
            # Tiny models (<1B) produce tokenizer garbage or echo few-shot
            # examples verbatim — the echo-guard will catch it at correction
            # time, but a heads-up at load time is friendlier than a silent
            # "try a larger model" error after the user's first attempt.
            size_b = _model_size_billions(model_path)
            if size_b is not None and size_b < _MIN_RELIABLE_MODEL_B:
                warn = (
                    f"'{name}' is ~{size_b:g}B parameters. Models smaller than "
                    f"~1B may produce garbled or echoed output. Recommended: "
                    f"Gemma 4 E2B or larger."
                )
                log(f"[{self.label}] WARNING: {warn}")
                self.model_warning.emit(warn)
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
                return self.load_model(force_cpu=True)
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
        max_passes: int = 3,
    ) -> str | None:
        """Return corrected text by asking the LLM for a JSON patch list.

        The LLM outputs only the words that need changing, dramatically reducing
        output tokens. Falls back to None on any parsing error so the caller
        can use correct_text() instead.

        Parameters:
            text: The text to correct.
            system: Complete system prompt (caller builds it per strength level).
            examples: Few-shot message pairs (caller builds per strength level).
            max_passes: Max number of refinement passes per chunk.
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
                "- ONLY INCLUDE WORDS THAT CONTAIN ERRORS (spelling, grammar, punctuation, missing apostrophes).\n"
                "- DO NOT INCLUDE CORRECT WORDS. If a word is already correct, ignore it completely.\n"
                "- Keep each patch short: 1-3 words max for old/new.\n"
                "- If the text is perfectly fine, output an empty array: []\n"
                "- Output ONLY the JSON array and absolutely nothing else.\n"
            )

        # Build message prefix (system + examples) WITHOUT user text yet.
        msg_prefix = [{"role": "system", "content": system}]
        if examples:
            msg_prefix.extend(examples)

        word_count = len(text.split())
        # Ignore actual_ctx_size because some GGUF metadata underreports (e.g. 4096)
        # when the model can comfortably handle the user's config (e.g. 12800).
        ctx_size = self.cfg.get("context_size", 12800)

        prefix_text = system + " ".join(
            m.get("content", "") for m in (examples or [])
        )
        
        # We estimate the token overhead of the prompt here.
        # Previously we used 1.6 as a conservative ratio of words to tokens,
        # but to be completely safe against context window overflow (HTTP 500), 
        # we now use 2.5 to overestimate input and leave more room for output.
        overhead = int(len(prefix_text.split()) * 2.5) + 100

        # Enforce a smaller max chunk size so the LLM never struggles with
        # massive walls of text or runs out of output tokens for patches.
        # Cap chunk words to max 400 (~1.5 pages) to give plenty of output budget
        max_chunk_words = min(max(int((ctx_size - overhead - 1000) / 2.5), 50), 400)

        if word_count > max_chunk_words:
            chunks = _chunk_text_by_sentences(text, max_chunk_words)
            log(
                f"[{self.label}] Text too long ({word_count} words), "
                f"chunking into {len(chunks)} parts (max ~{max_chunk_words} words each)"
            )
        else:
            chunks = [(text, "")]

        # ── Process each chunk ─────────────────────────────────────────────
        corrected_parts: list[tuple[str, str]] = []
        any_changed = False
        total_chunks = len(chunks)

        for chunk_idx, (chunk_text, separator) in enumerate(chunks):
            if total_chunks > 1:
                self.status_changed.emit(
                    f"Correcting… ({chunk_idx + 1}/{total_chunks})"
                )

            current_chunk = chunk_text
            chunk_changed_in_any_pass = False
            chunk_success = False

            for pass_num in range(1, max_passes + 1):
                messages = msg_prefix + [{"role": "user", "content": current_chunk}]

                cw = len(current_chunk.split())
                
                # Estimate the input tokens conservatively
                est_input = int(cw * 2.5) + overhead
                
                # The remaining budget for output tokens.
                # If we request too many tokens (prompt + max_tokens > n_ctx), 
                # llama-server returns an HTTP 500 error ("Context size has been exceeded").
                max_output = max(ctx_size - est_input, 1536)
                
                # Ask for enough tokens to comfortably fit patches (about 4 tokens per word max).
                # Bounded by max_output to avoid 500 errors, and capped at 2048 to prevent 
                # models from going off-rails and generating thousands of no-op tokens.
                patch_tokens = min(max(cw * 4, 1536), max_output, 4096)

                payload = {
                    "messages": messages,
                    "max_tokens": patch_tokens,
                    "temperature": 0.0,
                    "top_k": self.cfg.get("top_k", 40),
                    "top_p": self.cfg.get("top_p", 0.95),
                    "min_p": self.cfg.get("min_p", 0.05),
                    "repeat_penalty": self.cfg.get("repeat_penalty", 1.0),
                    "frequency_penalty": self.cfg.get("frequency_penalty", 0.0),
                    "presence_penalty": self.cfg.get("presence_penalty", 0.0),
                    "stream": False,
                    "think": False,
                }

                try:
                    log(
                        f"[{self.label}] PATCH POST chunk {chunk_idx + 1}/{total_chunks} pass {pass_num} "
                        f"payload={json.dumps(payload)[:300]}"
                    )
                    r = requests.post(self._chat_url(), json=payload, timeout=120)
                    if not r.ok:
                        log(f"[{self.label}] HTTP {r.status_code} — body: {r.text[:500]}")
                    r.raise_for_status()
                    resp = r.json()
                    log(f"[{self.label}] Patch raw response: {json.dumps(resp)[:500]}")

                    raw, finish_reason = _extract_content_from_response(resp)
                    log(
                        f"[{self.label}] Patch raw content: {repr(raw[:300])}, "
                        f"finish_reason={finish_reason}"
                    )

                    if not raw and finish_reason == "length":
                        log(
                            f"[{self.label}] finish_reason=length — "
                            f"chunk {chunk_idx + 1} pass {pass_num} produced no output"
                        )
                        break

                    if _is_corrupt_output(raw):
                        log(
                            f"[{self.label}] Corrupt patch output from "
                            f"chunk {chunk_idx + 1} pass {pass_num}: {repr(raw[:120])}"
                        )
                        break

                    patches = _extract_patches_from_response(raw)

                    if patches is None:
                        log(f"[{self.label}] No valid patches from chunk {chunk_idx + 1} pass {pass_num}")
                        break

                    # We successfully got some patches (even if empty)
                    chunk_success = True

                    real_patches = [
                        p
                        for p in patches
                        if p.get("old", "").strip() != p.get("new", "").strip()
                    ]

                    if not real_patches:
                        log(f"[{self.label}] Chunk {chunk_idx + 1} pass {pass_num}: no corrections needed")
                        break

                    log(
                        f"[{self.label}] Chunk {chunk_idx + 1} pass {pass_num}: "
                        f"{len(real_patches)} patches"
                    )
                    result = _apply_patches(current_chunk, real_patches)
                    log(f"[{self.label}] Chunk {chunk_idx + 1} pass {pass_num} result: {repr(result[:200])}")

                    if result == current_chunk:
                        break

                    prev_words = current_chunk.split()
                    new_words = result.split()
                    changes = abs(len(prev_words) - len(new_words)) + sum(
                        1 for a, b in zip(prev_words, new_words) if a != b
                    )

                    current_chunk = result
                    chunk_changed_in_any_pass = True

                    # Smart pass termination to avoid wasting 40s+ on no-op passes
                    # If the model starts returning mostly no-op patches, it's done finding real errors.
                    no_op_count = len(patches) - len(real_patches)
                    if patches and no_op_count >= len(real_patches):
                        log(
                            f"[{self.label}] Chunk {chunk_idx + 1} pass {pass_num} produced "
                            f"mostly no-ops ({no_op_count} ignored vs {len(real_patches)} applied) — skipping further passes"
                        )
                        break

                    # Terminate when the pass was a light edit. On short/medium
                    # text (≤ 150 words), the first pass usually resolves all
                    # real errors — additional passes tend to introduce stylistic
                    # tweaks the user didn't ask for (and add 1–3s each). On
                    # longer text, allow pass 2 to run so the model can catch
                    # what pass 1 deprioritized.
                    if changes < 3 and (cw <= 150 or pass_num >= 2):
                        log(
                            f"[{self.label}] Chunk {chunk_idx + 1} pass {pass_num} was light "
                            f"({changes} edits, {cw} words) — skipping further passes"
                        )
                        break

                except Exception as e:
                    log(
                        f"[{self.label}] correct_text_patch error "
                        f"(chunk {chunk_idx + 1} pass {pass_num}): {e}"
                    )
                    if total_chunks == 1 and not chunk_success:
                        self.status_changed.emit(f"Error: {str(e)[:50]}")
                    break

            if not chunk_success and total_chunks == 1:
                return None

            if chunk_changed_in_any_pass:
                any_changed = True

            corrected_parts.append((current_chunk, separator))

        # ── Reassemble corrected chunks ────────────────────────────────────
        final = "".join(part + sep for part, sep in corrected_parts)

        if not any_changed:
            if total_chunks == 1 and not chunk_success:
                log(f"[{self.label}] No patches applied — falling back to full text")
                return None
            log(f"[{self.label}] No corrections needed across {total_chunks} chunks")
            return text

        self.last_used = datetime.now()
        self.status_changed.emit("Ready")
        return final

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
        # Include all sampling params the user configured. Previously min_p,
        # repeat_penalty, frequency_penalty, and presence_penalty were missing,
        # so changing them in settings had no effect on streaming chat output.
        payload = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": self.cfg.get("temperature", 0.3),
            "top_k": self.cfg.get("top_k", 40),
            "top_p": self.cfg.get("top_p", 0.95),
            "min_p": self.cfg.get("min_p", 0.05),
            "repeat_penalty": self.cfg.get("repeat_penalty", 1.0),
            "frequency_penalty": self.cfg.get("frequency_penalty", 0.0),
            "presence_penalty": self.cfg.get("presence_penalty", 0.0),
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
        # Clamp dimensions to the current screen so the dialog never opens
        # taller than the display (observed on 1366×768 / 1440×900 laptops
        # where the default 820 px height pushed buttons off-screen).
        # Minimum shrinks too — a 680 px minimum on a 720 px-tall screen is
        # unusable once the taskbar eats some space.
        screen = QApplication.primaryScreen()
        sr = screen.availableGeometry() if screen else None
        if sr:
            max_h = int(sr.height() * 0.9)
            min_h = min(680, int(sr.height() * 0.8))
            max_w = int(sr.width() * 0.9)
            min_w = min(580, int(sr.width() * 0.85))
            self.setMinimumSize(min_w, min_h)
            self.resize(min(680, max_w), min(820, max_h))
        else:
            self.setMinimumSize(580, 680)
            self.resize(680, 820)
        self._build_ui()
        self._load()
        # Re-center on the screen after UI is built so the dialog can never
        # land with half of it outside the visible area
        if sr:
            geo = self.frameGeometry()
            geo.moveCenter(sr.center())
            self.move(geo.topLeft())

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
        # Set window icon so the taskbar entry shows our logo instead of a blank icon
        logo_path = SCRIPT_DIR / "logo.png"
        if logo_path.exists():
            self.setWindowIcon(QIcon(str(logo_path)))
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
                # Pick prompt complexity by model size. Small models (<1B) drown
                # in the detailed rule list below and drift off-task; a stripped
                # prompt plus the JSON-schema grammar constraint (in
                # correct_text_patch) is enough to keep them producing valid
                # patches. Larger models benefit from the richer rules — they
                # can actually act on the nuances (e.g. "its" vs "it's").
                _ac_path = (
                    self.cfg.get("model_path", "")
                    if self.cfg.get("ac_same_as_chat", True)
                    else self.cfg.get("ac_model_path", "")
                )
                _size_b = _model_size_billions(_ac_path)
                _is_tiny = _size_b is not None and _size_b < _MIN_RELIABLE_MODEL_B

                patch_system = (
                    "You are an elite, meticulous copyeditor. Review the user's text word by word.\n"
                    "Find and fix typos, spelling mistakes (e.g. 'graphisc' -> 'graphic'), clearly wrong capitalization, and missing terminal punctuation. For a missing end-of-sentence mark, add whichever fits the meaning: '?' for questions, '!' for exclamations, '.' otherwise — never force a period.\n"
                    "NEVER change numbers, dates, URLs, code, or specific values — copy them exactly as written (e.g. do not round 0.0735 to 0.074).\n"
                    "NEVER alter intentional styling: preserve ALL CAPS words, initialisms (NASA, USA), and Title Case exactly as the user wrote them. Only fix capitalization that is clearly a typing mistake (e.g. 'i' as a pronoun, or a lowercase word at the start of a sentence).\n"
                    "Output your corrections as a JSON array of replacements. Do NOT include text that is already correct."
                )

                # Inject custom instructions into patch prompt if set
                if custom_sys:
                    patch_system += f"\n\nAdditional instructions:\n{custom_sys}"
                
                patch_examples = [
                    {"role": "user", "content": "the project were delayed because of bad wether"},
                    {"role": "assistant", "content": '[\n  {"old": "the project were", "new": "The project was"},\n  {"old": "wether", "new": "weather."}\n]'},
                    {"role": "user", "content": "i dont know if its gona work"},
                    {"role": "assistant", "content": '[\n  {"old": "i dont", "new": "I don\'t"},\n  {"old": "its gona", "new": "it\'s gonna"},\n  {"old": "work", "new": "work."}\n]'},
                    {"role": "user", "content": "heavy motion graphisc design fast paced"},
                    {"role": "assistant", "content": '[\n  {"old": "graphisc", "new": "graphic"},\n  {"old": "fast paced", "new": "fast-paced"}\n]'},
                    {"role": "user", "content": "can you beleive they came late agian"},
                    {"role": "assistant", "content": '[\n  {"old": "can you beleive", "new": "Can you believe"},\n  {"old": "came late agian", "new": "came late again?"}\n]'},
                    {"role": "user", "content": "thats AMAZING i cant wait"},
                    {"role": "assistant", "content": '[\n  {"old": "thats AMAZING i cant", "new": "That\'s AMAZING! I can\'t"},\n  {"old": "wait", "new": "wait!"}\n]'},
                    {"role": "user", "content": "the p-value is 0.0735 which isnt below 0.05"},
                    {"role": "assistant", "content": '[\n  {"old": "the p-value", "new": "The p-value"},\n  {"old": "isnt", "new": "isn\'t"},\n  {"old": "0.05", "new": "0.05."}\n]'}
                ]
                # Multi-pass patch correction is now handled per-chunk inside
                # correct_text_patch. This dramatically reduces the number of
                # requests since converged chunks don't get sent again.
                MAX_PATCH_PASSES = 3
                word_count = len(text.split())

                log(f"[CW] Smart Fix (max passes: {MAX_PATCH_PASSES})")
                result = self.ac_model.correct_text_patch(
                    text, system=patch_system, examples=patch_examples, max_passes=MAX_PATCH_PASSES
                )
                
                if result is None:
                    # Patch extraction/request failed completely — break out to full-text fallback
                    patch_failed = True
                else:
                    patch_failed = False
                    current_text = result

                if not patch_failed:
                    if current_text == text:
                        self._correction_ready.emit(text, "Already correct")
                    else:
                        self._correction_ready.emit(
                            current_text, "Smart Fix (patch)"
                        )
                    return
                # Patch failed — fall back to full-text with aggressive prompt
                log("[CW] Patch failed, falling back to full-text…")
                _base_full = (
                    "You are a text correction engine.\n"
                    "OUTPUT ONLY the corrected text — no labels, no preamble, no explanations.\n"
                    "Fix ALL errors: spelling, grammar, capitalization, punctuation, apostrophes.\n"
                    "Preserve formatting, line breaks, and original tone.\n"
                    "If the text is already correct, return it unchanged."
                )
                full_system = (
                    f"{_base_full}\n\nAdditional instructions:\n{custom_sys}"
                    if custom_sys else _base_full
                )
                full_examples = [
                    {"role": "user", "content": _EX1_INPUT},
                    {"role": "assistant", "content": "The project was delayed because of bad weather."},
                    {"role": "user", "content": _EX2_INPUT},
                    {"role": "assistant", "content": "I don't know if it's gonna work."},
                ]
                result = self.ac_model.correct_text(text, system=full_system, examples=full_examples)
                # Guard against two failure modes seen with undersized models:
                #   1. Tokenizer garbage ([UNK_BYTE_...], control chars, leaking ▁)
                #   2. Regurgitating a few-shot example output verbatim
                # Both corrupt the user's text silently if pasted — return the
                # original with a clear warning instead.
                if result is not None and result.strip():
                    if _is_corrupt_output(result):
                        log(f"[CW] Corrupt output detected, rejecting: {repr(result[:100])}")
                        self._correction_ready.emit(
                            text, "Model output invalid — try a larger model"
                        )
                    elif _is_fewshot_echo(result, text):
                        log(f"[CW] Few-shot echo detected, rejecting: {repr(result[:100])}")
                        self._correction_ready.emit(
                            text, "Model echoed example — try a larger model"
                        )
                    else:
                        self._correction_ready.emit(result, "Smart Fix (full-text)")
                else:
                    self._correction_ready.emit(text, "No changes (model error)")

            else:
                # Conservative mode (default): full-text, typos and obvious errors only
                _base_conservative = (
                    "You are a text correction engine.\n"
                    "OUTPUT ONLY the corrected text — no labels, no preamble, no explanations.\n"
                    "ONLY fix clear misspellings and obvious typos.\n"
                    "Do NOT change grammar, capitalization, punctuation, style, or word choice.\n"
                    "Preserve everything else exactly as written.\n"
                    "If the text has no typos, return it unchanged."
                )
                full_system = (
                    f"{_base_conservative}\n\nAdditional instructions:\n{custom_sys}"
                    if custom_sys else _base_conservative
                )
                full_examples = [
                    {"role": "user", "content": _EX1_INPUT},
                    {"role": "assistant", "content": "the project were delayed because of bad weather"},
                    {"role": "user", "content": _EX2_INPUT},
                    {"role": "assistant", "content": "i dont know if its gonna work"},
                ]
                log("[CW] Conservative mode: full-text, typos only")
                result = self.ac_model.correct_text(text, system=full_system, examples=full_examples)
                # Same guards as Smart Fix fallback — reject corrupt / echoed output
                if result is not None and result.strip():
                    if _is_corrupt_output(result):
                        log(f"[CW] Corrupt output detected, rejecting: {repr(result[:100])}")
                        self._correction_ready.emit(
                            text, "Model output invalid — try a larger model"
                        )
                    elif _is_fewshot_echo(result, text):
                        log(f"[CW] Few-shot echo detected, rejecting: {repr(result[:100])}")
                        self._correction_ready.emit(
                            text, "Model echoed example — try a larger model"
                        )
                    else:
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
        # On the FIRST chat turn, embed the text directly in the user message
        # (not via a fake prefilled assistant reply). Small models (<2B)
        # frequently ignored the prefill trick and replied "Please provide the
        # text." — because from their point of view the user just asked a
        # question with no content attached. Putting the text in the actual
        # user turn is the format every instruction-tuned model handles.
        # On subsequent turns, the prior assistant reply is in-context already,
        # so we just append the new instruction.
        if not self.chat_history:
            self.chat_history = [{"role": "system", "content": system}]
            self.chat_history.append({
                "role": "user",
                "content": f"Task: {msg}\n\nText:\n{self.corrected}",
            })
        else:
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
        log(f"[APP] Boot — gpu_layePP] Boot — keep_model_loaded: {self.cfg.get('keep_model_loaded', True)}")
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
        # Hotkey re-entrancy guard — holding the keys or rapid repeat presses
        # used to spawn overlapping _hotkey_fired threads, each firing its own
        # "no text selected" notification in a feedback loop. This lock ensures
        # only one hotkey flow runs at a time.
        self._hotkey_busy = threading.Lock()
        self._last_empty_notify_ts = 0.0
        
        # pynput hotkey system
        self._pynput_listener: Listener | None = None
        self._current_keys: set = set()
        self._hotkey_triggered = False
        self._hotkey_queue: queue.Queue = queue.Queue()
        self._hotkey_timer: QTimer | None = None
        self._hotkey_keys: set = set()

        self._trigger.connect(self._show_window)
        self._notify.connect(self._show_notify)
        self.ac_model.status_changed.connect(self._on_ac_status)
        self.chat_model.status_changed.connect(self._on_chat_status)
        self.ac_model.model_loaded.connect(lambda: self._set_tray_icon("#3b82f6"))
        self.chat_model.model_loaded.connect(lambda: self._set_tray_icon("#a78bfa"))
        self.chat_model.model_unloaded.connect(lambda: self._set_tray_icon("#475569"))
        # Surface tiny-model warnings to the user once, at load time
        self.ac_model.model_warning.connect(self._show_model_warning)
        self.chat_model.model_warning.connect(self._show_model_warning)

        self._build_tray()
        self._register_hotkey()

        self._idle_timer = QTimer()
        self._idle_timer.timeout.connect(self.chat_model.check_idle)
        self._idle_timer.timeout.connect(self.ac_model.check_idle)
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

        # First-run setup: if no model is configured, prompt the user to
        # either download the recommended one or browse for an existing file.
        # Non-technical users who just unzipped the release would otherwise see
        # a silent tray icon and have no idea what to do next.
        if not self.cfg.get("model_path", ""):
            QTimer.singleShot(800, self._show_first_run)

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
        """Register global hotkey using pynput with Qt-safe event ferrying."""
        # Stop existing listener
        if self._pynput_listener:
            try:
                self._pynput_listener.stop()
            except Exception:
                pass
            self._pynput_listener = None
        
        # Stop existing timer
        if self._hotkey_timer:
            try:
                self._hotkey_timer.stop()
            except Exception:
                pass
        
        hk = self.cfg.get("hotkey", "ctrl+shift+space").lower().strip()
        
        # Parse hotkey string to pynput key objects
        self._hotkey_keys = set()
        key_map = {
            'ctrl': Key.ctrl_l, 'shift': Key.shift_l, 'alt': Key.alt_l,
            'space': Key.space, 'enter': Key.enter, 'tab': Key.tab,
            'backspace': Key.backspace, 'delete': Key.delete,
            'home': Key.home, 'end': Key.end, 'pageup': Key.page_up,
            'pagedown': Key.page_down, 'up': Key.up, 'down': Key.down,
            'left': Key.left, 'right': Key.right,
            'f1': Key.f1, 'f2': Key.f2, 'f3': Key.f3, 'f4': Key.f4,
            'f5': Key.f5, 'f6': Key.f6, 'f7': Key.f7, 'f8': Key.f8,
            'f9': Key.f9, 'f10': Key.f10, 'f11': Key.f11, 'f12': Key.f12,
        }
        
        for part in hk.split('+'):
            part = part.strip()
            if part in key_map:
                self._hotkey_keys.add(key_map[part])
            elif len(part) == 1:
                self._hotkey_keys.add(pynput_keyboard.KeyCode.from_char(part))
        
        if not self._hotkey_keys:
            log("[Hotkey] Failed to parse hotkey")
            return
        
        log(f"[Hotkey] pynput registering: {hk}")
        
        def on_press(key):
            self._current_keys.add(key)
            if self._hotkey_keys.issubset(self._current_keys):
                if not self._hotkey_triggered:
                    self._hotkey_triggered = True
                    # Put event in queue — DO NOT touch Qt from this thread
                    try:
                        self._hotkey_queue.put_nowait("trigger")
                    except queue.Full:
                        pass
        
        def on_release(key):
            self._current_keys.discard(key)
            if key in self._hotkey_keys:
                self._hotkey_triggered = False
        
        try:
            self._pynput_listener = Listener(on_press=on_press, on_release=on_release)
            self._pynput_listener.start()
            
            # Poll queue from main Qt thread every 50ms
            self._hotkey_timer = QTimer(self)
            self._hotkey_timer.timeout.connect(self._check_hotkey_queue)
            self._hotkey_timer.start(50)
            
        except Exception as e:
            log(f"[Hotkey] pynput failed: {e}")
            self.tray.showMessage(
                "TextCorrector",
                f"Could not register hotkey '{hk}'. Try running as administrator.",
                QSystemTrayIcon.MessageIcon.Warning,
                4000,
            )
    
    def _check_hotkey_queue(self):
        """Called every 50ms from main Qt thread — safe to call Qt methods."""
        try:
            while True:
                event = self._hotkey_queue.get_nowait()
                if event == "trigger":
                    self._hotkey_fired()
        except queue.Empty:
            pass

    def _safe_paste(self, retries=5, delay=0.03) -> str:
        for i in range(retries):
            try:
                return pyperclip.paste()
            except Exception as e:
                if i == retries - 1:
                    log(f"[Clipboard] paste failed: {e}")
                    return ""
                time.sleep(delay)
        return ""

    def _safe_copy(self, text: str, retries=5, delay=0.03):
        for i in range(retries):
            try:
                pyperclip.copy(text)
                return
            except Exception as e:
                if i == retries - 1:
                    log(f"[Clipboard] copy failed: {e}")
                    return
                time.sleep(delay)

    def _hotkey_fired(self):
        """Called from main Qt thread via queue polling."""
        # Re-entrancy guard
        if not self._hotkey_busy.acquire(blocking=False):
            log("[Hotkey] Fired but already busy — ignoring")
            return
        
        # Run actual work in background thread so Qt stays responsive
        threading.Thread(target=self._hotkey_worker, daemon=True).start()

    def _hotkey_worker(self):
        try:
            # If window already open, just focus it
            if self._window and self._window.isVisible():
                log("[Hotkey] Window already open — focusing")
                try:
                    # Use invokeMethod to ensure thread safety
                    self._window.raise_()
                    self._window.activateWindow()
                except Exception:
                    pass
                return

            # Small delay for natural key release
            time.sleep(0.05)

            self._old_clip = self._safe_paste()
            self._safe_copy("")
            time.sleep(0.03)
            
            # Use pynput Controller for Ctrl+C — guaranteed cleanup
            ctrl = Controller()
            with ctrl.pressed(Key.ctrl):
                ctrl.press('c')
                ctrl.release('c')

            # Poll clipboard
            selected = ""
            for _ in range(10):
                time.sleep(0.03)
                clip = self._safe_paste()
                if clip:
                    selected = clip
                    break

            if selected.strip():
                self._trigger.emit(selected.strip())
            else:
                if self._old_clip:
                    self._safe_copy(self._old_clip)
                now = time.monotonic()
                if now - self._last_empty_notify_ts > 3.0:
                    self._last_empty_notify_ts = now
                    self._notify.emit(
                        "No text selected. Select text first, then press the hotkey.",
                        "info",
                    )
                else:
                    log("[Hotkey] Empty selection — throttled")
        except Exception as e:
            log(f"[Hotkey] Error: {e}")
        finally:
            self._hotkey_busy.release()

    def _show_model_warning(self, msg: str):
        # Longer duration (6s) than standard notifications — this is a sticky
        # heads-up, not a quick confirmation, and users need time to read it
        self.tray.showMessage(
            "TextCorrector — Model warning",
            msg,
            QSystemTrayIcon.MessageIcon.Warning,
            6000,
        )

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
        self._safe_copy(text)
        time.sleep(0.15)
        # Use pynput to paste
        ctrl = Controller()
        with ctrl.pressed(Key.ctrl):
            ctrl.press('v')
            ctrl.release('v')
        time.sleep(0.1)
        if self._old_clip and self._old_clip != text:
            clip_to_restore = self._old_clip
            QTimer.singleShot(500, lambda: self._safe_copy(clip_to_restore))

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

    def _show_first_run(self):
        # Bail if the user already chose a model while the timer was pending
        # (e.g. via settings or tray menu), or if they've dismissed this
        # welcome before and the flag was set.
        if self.cfg.get("model_path", ""):
            return

        box = QMessageBox()
        box.setWindowTitle("Welcome to TextCorrector")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText("TextCorrector needs a language model to work.")
        dl_name = "download_model.bat" if WINDOWS else "download_model.sh"
        box.setInformativeText(
            "You can:\n\n"
            f"  • Download the recommended model (~1.8 GB) — runs {dl_name} in a terminal\n"
            "  • Browse to an existing .gguf file you already have\n"
            "  • Skip for now and configure from Settings later"
        )
        dl_btn = box.addButton("Download recommended", QMessageBox.ButtonRole.AcceptRole)
        br_btn = box.addButton("Browse existing…", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Skip", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(dl_btn)
        box.exec()

        clicked = box.clickedButton()
        if clicked is dl_btn:
            self._run_download_script()
        elif clicked is br_btn:
            self._browse_model()

    def _run_download_script(self):
        """Launch the bundled download_model script in a visible terminal so
        the user can watch progress. Falls back to opening the release folder
        if the script is missing (e.g. dev launch)."""
        script = SCRIPT_DIR / ("download_model.bat" if WINDOWS else "download_model.sh")
        if not script.exists():
            # Dev mode or corrupted unzip — just reveal the folder so the user
            # can grab the model manually.
            self.tray.showMessage(
                "TextCorrector",
                f"Download script not found at {script.name}. Please download a GGUF model manually.",
                QSystemTrayIcon.MessageIcon.Warning,
                5000,
            )
            return
        try:
            if WINDOWS:
                # start "" opens the .bat in its own console window so the user
                # sees curl's progress bar instead of a silent background fetch
                subprocess.Popen(
                    ["cmd", "/c", "start", "", str(script)],
                    cwd=str(SCRIPT_DIR),
                )
            else:
                subprocess.Popen(["bash", str(script)], cwd=str(SCRIPT_DIR))
        except Exception as e:
            log(f"[FirstRun] Failed to launch download script: {e}")
            self.tray.showMessage(
                "TextCorrector",
                f"Could not launch {script.name}: {e}",
                QSystemTrayIcon.MessageIcon.Warning,
                5000,
            )

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
        if self._pynput_listener:
            try:
                self._pynput_listener.stop()
            except Exception:
                pass
        if self._hotkey_timer:
            try:
                self._hotkey_timer.stop()
            except Exception:
                pass
        self.ac_model.unload_model()
        self.chat_model.unload_model()
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
