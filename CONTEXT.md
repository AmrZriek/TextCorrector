# TextCorrector - Comprehensive Project Context

## Project Overview

TextCorrector is a Windows system tray application that provides instant AI-powered text correction using local LLM models via llama.cpp. It allows users to select text in any application, press a global hotkey (Alt+T), and receive corrected/refined text through a sleek PyQt5 interface.

**Key Philosophy**: 100% local processing - no data ever leaves the user's computer, making it ideal for sensitive documents.

---

## Architecture

### Tech Stack
- **Backend**: llama.cpp server (b8117, CUDA 12.4 build)
- **Frontend**: Python 3.12 + PyQt5
- **API**: OpenAI-compatible `/v1/chat/completions`
- **Communication**: HTTP localhost (port 8080 default)
- **GPU Acceleration**: CUDA via ggml-cuda.dll (Updated to b8117)

### File Structure
```
TextCorrector/
├── text_corrector.py          # Main application (~1700 lines)
├── run.bat                    # Windows launcher with auto-elevation
├── requirements.txt           # Python dependencies
├── README.md                  # Beginner-friendly Quick Start guide
├── GITHUB_SETUP.md            # Beginner's guide for non-coders
├── CONTEXT.md                # Technical reference for AI agents (This file)
├── config.json               # User settings (auto-generated)
├── app_debug.log             # Application logs (runtime)
├── server_log.txt            # llama-server stdout (runtime)
├── llama_cpp/                # llama.cpp binaries (~40 files, ~200MB)
│   ├── llama-server.exe      # Main inference server
│   ├── ggml-cuda.dll         # CUDA backend
│   ├── cublas64_12.dll       # CUDA BLAS
│   ├── cudart64_12.dll       # CUDA Runtime
│   └── ... (CPU backends, other utils)
└── *.gguf                    # User-provided AI models (not in repo)
```

---

## Core Components

### 1. ConfigManager (lines 149-210)
**Purpose**: Persistent configuration management using JSON

**Key Methods**:
- `load_config()`: Loads from config.json or creates defaults
- `save_config()`: Writes settings to disk
- `_auto_detect_model()`: Discovers .gguf files on startup
- `add_recent_model()`: Maintains recent models list (max 10)
- `_accepting` flag: Management of clipboard/paste flow cleanup in `CorrectionWindow`

**Default Configuration**:
```python
DEFAULT_CONFIG = {
    "llama_server_path": "llama_cpp/llama-server.exe",
    "model_path": "",           # Auto-detected on first run
    "server_host": "127.0.0.1",
    "server_port": 8080,
    "hotkey": "alt+t",
    "keep_model_loaded": True,
    "idle_timeout_seconds": 300,
    "context_size": 4096,
    "gpu_layers": 99,           # Full GPU offload
    "recent_models": [],
    "temperature": 0.0,         # Deterministic (changed from 0.1)
    "top_k": 40,
    "top_p": 0.95,
    "min_p": 0.05,
    "frequency_penalty": 0.0,
    "presence_penalty": 0.0,
    "repeat_penalty": 1.0,
}
```

### 2. ModelManager (lines 584-1069)
**Purpose**: Manages llama.cpp server lifecycle and inference

**Key Features**:
- Process management (start/stop llama-server.exe)
- Port conflict resolution (kills existing servers)
- Health checking with 180s timeout
- Auto-unload on idle (configurable)
- Thread-safe operations (using threading.Lock)

**Server Startup Process** (lines 687-780):
1. Kill existing llama-server processes
2. Build command with model path, context size, GPU layers
3. Add `--no-warmup` for faster startup
4. Start process with hidden window
5. Wait for health endpoint (up to 180s)
6. Handle CUDA fitting errors

**API Communication**:
- Uses OpenAI-compatible `/v1/chat/completions` endpoint
- Sends structured messages array with system/user/assistant roles
- Supports temperature, top_k, top_p, min_p, and frequency/presence/repetition penalties
- 120s timeout for generation

### 3. SettingsDialog (lines 266-582)
**Purpose**: Configuration UI with live validation

**Features**:
- Model file browser (.gguf filter)
- Recent models dropdown
- Numeric validation for all parameters
- Custom system prompt editor
- Glassmorphism dark theme styling
- Emits `settings_changed` signal on save

### 4. CorrectionWindow (lines 1071-1694)
**Purpose**: Main correction interface with diff highlighting

**UI Components**:
- Header with status indicator and settings button
- Original text display (read-only)
- Corrected text editor with diff highlighting
- Chat refinement interface
- Action buttons (Accept, Copy, Reset, Cancel)

**Diff Highlighting** (lines 1490-1520):
- Uses `difflib.SequenceMatcher` to compare word-by-word
- Highlights changes in green (rgba(74, 222, 128, 0.2))
- Shows unchanged text normally
- Falls back to plain text if identical

**Window Behavior**:
- Frameless window with custom drag implementation
- Stays on top (`Qt.WindowStaysOnTopHint`)
- Positions near cursor on show
- Escape key to close, Enter to accept

### 5. SystemTrayManager (lines 1696-2077)
**Purpose**: System tray integration and menu management

**Features**:
- Context menu with model selection
- Dynamic model submenu (auto-discovers .gguf files)
- Status indicator (colored icon)
- Auto-start on login option
- First-run configuration dialog

**Model Menu** (lines 1777-1840):
- Discovers models from app directory and recent list
- Deduplicates using normalized paths
- Shows checkmark for current model
- Case-insensitive path comparison (Windows fix)
- Emits signal to switch models

---

## Prompting Strategy

### Main Correction Prompt (lines 854-915)
Uses **Few-Shot Prompting** with 4 examples:

```python
messages = [
    {
        "role": "system",
        "content": (
            "You are a text correction engine...\n"
            "CRITICAL RULES - VIOLATING THESE IS AN ERROR:\n"
            "1. Output ONLY the corrected text...\n"
            "2. NEVER start with phrases...\n"
            "3. NEVER wrap output...\n"
            "4. If text is perfect, return unchanged\n"
            "5. Fix spelling, grammar...\n"
            "6. PRESERVE ALL LINE BREAKS...\n"
            "7. Maintain original formatting..."
        )
    },
    # Example 1: Grammar error
    {"role": "user", "content": "the project were delayed..."},
    {"role": "assistant", "content": "The project was delayed..."},
    
    # Example 2: Spelling error
    {"role": "user", "content": "i dont know if its gona work"},
    {"role": "assistant", "content": "I don't know if it's going to work."},
    
    # Example 3: Perfect text (returns unchanged)
    {"role": "user", "content": "Hello, how are you doing today?"},
    {"role": "assistant", "content": "Hello, how are you doing today?"},
    
    # Example 4: Multi-line email (formatting example)
    {"role": "user", "content": "Dear John,\n\nHow are you?\n\nI hope your doing well."},
    {"role": "assistant", "content": "Dear John,\n\nHow are you?\n\nI hope you're doing well."},
    
    # Current request
    {"role": "user", "content": text},
    {"role": "assistant", "content": ""}  # Forces completion
]
```

### Chat Refinement Prompt (lines 1596-1619)
```python
{
    "role": "system",
    "content": (
        "You are a text editing assistant..."
        "Apply the requested changes and output ONLY the modified text..."
        "PRESERVE ALL LINE BREAKS AND PARAGRAPH SPACING..."
    )
}
```

---

## Post-Processing Pipeline

### 1. strip_thinking_tokens() (lines 72-103)
**Purpose**: Remove thinking/reasoning blocks from model output

**Handles Multiple Formats**:
- `<think>...</think>` (Qwen3, DeepSeek)
- `<thinking>...</thinking>`
- `<reasoning>...</reasoning>`
- Both closed and unclosed tags

**Implementation**:
```python
# Pattern matching with DOTALL flag for multiline
thinking_patterns = [
    (r"<think>.*?</think>", re.DOTALL),
    (r"<thinking>.*?</thinking>", re.DOTALL),
    (r"<reasoning>.*?</reasoning>", re.DOTALL),
]

# Also handles unclosed tags
unclosed_patterns = [r"<think>.*", r"<thinking>.*", r"<reasoning>.*"]
```

### 2. strip_meta_commentary() (lines 106-147)
**Purpose**: Remove conversational preambles

**Patterns Stripped** (19 total):
- "Here's the corrected text..."
- "Sure, here is..."
- "Corrected version:"
- "I've proofread and refined..."
- Markdown formatting (**Corrected**, # Headers)
- Quote wrapping ("..." or '...')
- Code blocks (```...```)

### 3. contains_meta_commentary() (lines 151-195)
**Purpose**: Detect if output is still conversational

**Detection Methods**:
- Regex patterns for conversational prefixes
- Question mark detection
- Multiple short sentences analysis
- Returns True if retry needed

### 4. Auto-Retry Mechanism (lines 948-999)
**Trigger**: If `contains_meta_commentary()` returns True

**Retry Strategy**:
- Ultra-strict single-turn prompt
- Temperature 0.0 (deterministic)
- Top_p 0.1 (very focused)
- Explicit formatting instructions

---

## Feature Notes: Thinking Models
**Status**: BROKEN (Experimental)
 
**Status Note**: While the application now includes advanced token stripping and increased token budgets, "Thinking" models (like Qwen3, DeepSeek-R1) still exhibit unpredictable behavior and formatting issues that break the core experience.
 
**Models Affected**:
- ❌ Qwen 3 (Reasoning models)
- ❌ DeepSeek-R1 (Thinking tokens)
- ❌ Any model with `<think>` or `<reasoning>` capability
 
**Known Issues**:
- Internal reasoning can leak into the final output despite stripping.
- High token usage can lead to timeouts.
- Formatting preservation is inconsistent during long reasoning steps.
 
**Recommendation**: Stick to optimized "Instruct" or "Dynamic" non-thinking models for production use.

### Model Selection Glitches (INTERMITTENT)
**Symptoms**:
- Multiple models show checkmark in tray menu
- Model switch from Settings doesn't trigger reload
- "Model already loaded" errors incorrectly

**Root Causes**:
- Case-sensitivity in path comparison (Windows paths can differ in case)
- Race conditions in menu rebuilding
- Path normalization issues (forward vs backward slashes)

**Implemented Fixes**:
- Case-insensitive comparison using `.lower()` (lines 1808-1810)
- Normalized path deduplication (lines 1787-1795)
- Auto-reload on settings change (lines 1897-1898)

**Workarounds**:
- If multiple checkmarks: Restart the app
- If switch fails: Use "Unload Model" first, then select new model
- Delete config.json if settings corrupted

### Formatting Preservation (PARTIAL)
**Issue**: AI occasionally removes blank lines or extra whitespace

**Mitigation**:
- Explicit instructions in prompts (rules 6 & 7)
- Multi-line example in few-shot prompting
- Formatting preservation in retry prompt

**Not Perfect**: Small models (270M) occasionally ignore formatting instructions

### Performance with Small Models
**Issue**: Models under 1B parameters occasionally:
- Output conversational text despite instructions
- Miss obvious grammar errors
- Require retry mechanism

**Mitigation**:
- Auto-retry with stricter prompt
- Temperature 0.0 for determinism
- Frequency penalty to reduce loops

**Recommendation**: Use 2B+ parameter models for best results

---

## Version History

### v1.0.0 (2026-02-20)
**Stable Release - Infrastructure Update**

**Major Fixes & Enhancements**:
- **llama.cpp Upgrade**: Upgraded core engine to **b8117** (CUDA 12.4).
- **Accept and Paste Fix**: Resolved race condition where clipboard was cleared before paste could execute.
- **Thinking Model Improvements**: Increased `max_tokens` cap to 8192 and refined stripping (though still experimental).
- **Hotkeys**: Standardized global hotkey to **Alt + T**.
- **Conversational Filter**: Fixed false-positives on question marks in corrected text.
- **Stability**: Improved process cleanup and port management.

**Initial Features**:
- Full system tray integration
- Settings dialog with live validation
- Model switching via tray menu
- Chat refinement interface
- Diff highlighting
- CUDA acceleration
- Auto-unload on idle
- Global hotkey support

---

## Development Notes

### Adding New Features

**New Settings Parameter**:
1. Add to `DEFAULT_CONFIG` dict (line 121)
2. Add UI control in `SettingsDialog.setup_ui()` (line 276)
3. Load value in `load_settings()` (line 441)
4. Save in `save_settings()` (line 553)
5. Access via `config.get("param_name", default)`

**New Post-Processing Step**:
1. Add function after `strip_meta_commentary()` (line 106)
2. Call it in `correct_text()` after line 942
3. Call it in `chat_with_model()` after line 1061
4. Update docstrings

**New Model Format Support**:
1. Add pattern to `strip_thinking_tokens()` (line 72)
2. Add to both closed and unclosed pattern lists
3. Test with actual model output

### Testing Checklist

**Basic Functionality**:
- [ ] App starts and shows tray icon
- [ ] Model auto-detects on first run
- [ ] Hotkey triggers correction window
- [ ] Text correction works
- [ ] Accept & Paste replaces original text
- [ ] Copy button works

**Model Switching**:
- [ ] Can switch models from tray menu
- [ ] Only one model shows checkmark
- [ ] Settings dialog triggers reload
- [ ] Unload model works

**Edge Cases**:
- [ ] Long text (context limit handling)
- [ ] Multi-line text with blank lines
- [ ] Perfect text (no corrections needed)
- [ ] Single word corrections
- [ ] Very short text (1-2 words)

**Error Handling**:
- [ ] Server fails to start
- [ ] Model file not found
- [ ] Context limit exceeded
- [ ] Connection timeout

---

## Dependencies

### Required Python Packages
```
PyQt5>=5.15.0
keyboard>=0.13.5
pyperclip>=1.8.0
requests>=2.25.0
psutil>=5.8.0
```

### System Requirements
- Windows 10/11
- Python 3.12+
- NVIDIA GPU with CUDA 12.4 (optional but recommended)
- 8GB+ RAM (16GB recommended)
- 2GB+ VRAM for small models, 6GB+ for medium models

### Included Binaries
- llama.cpp b8117 (CUDA 12.4 build)
- All required CUDA DLLs
- CPU backends for various architectures

---

## Security Considerations

**Local-Only Processing**:
- No network connections except localhost
- No data sent to external servers
- No telemetry or analytics
- Models run entirely on user's GPU

**Administrator Privileges**:
- Required for global hotkey to work in all apps
- `run.bat` auto-elevates via PowerShell
- Can run without admin but hotkey may not work in some apps

**File Permissions**:
- Creates config.json in app directory
- Creates log files (app_debug.log, server_log.txt)
- Needs write permissions to app folder

---

## Performance Tuning

### For Speed
- Use smaller models (270M-3B)
- Enable "Keep Model Loaded"
- Increase GPU layers (99 = all)
- Reduce context size if not needed

### For Quality
- Use larger models (3B-7B)
- Keep temperature at 0.0
- Increase frequency penalty if repetitive (0.5-1.0)
- Use models with strong instruction following

### For VRAM Constraints
- Reduce GPU layers (try 20-30)
- Use CPU offloading
- Reduce context size (2048 or 4096)
- Use smaller quantization (Q4_K_M instead of Q8)

---

## Future Improvements

### Potential Enhancements
1. **Model Gallery**: Built-in model downloader from HuggingFace
2. **Multiple Profiles**: Different settings for different use cases
3. **Batch Processing**: Correct multiple files at once
4. **History**: Save recent corrections
5. **Custom Templates**: User-defined correction styles
6. **Plugin System**: Support for different AI backends

### Technical Debt
1. **psutil**: Currently optional, should be required for better process management
2. **Type Hints**: Add full typing for better IDE support
3. **Tests**: No unit tests currently
4. **Logging**: Mix of print and file logging, should unify
5. **Error Handling**: Some areas use bare except clauses

---

## Quick Reference

### Keyboard Shortcuts
- `Alt+T` - Trigger correction
- `Enter` - Accept and paste
- `Escape` - Close window

### File Locations
- **Config**: `config.json`
- **Logs**: `app_debug.log`, `server_log.txt`
- **Models**: Any `.gguf` in app directory
- **Server**: `llama_cpp/llama-server.exe`

### Debug Commands
```bash
# Check if server is running
curl http://localhost:8080/health

# View server logs
type server_log.txt

# View app logs
type app_debug.log

# Kill stuck server
taskkill /f /im llama-server.exe
```

### Recommended Models (Verified Working)
1. **Granite 4.0 Micro** - Ultra-fast and accurate high-speed correction.
2. **Granite 4.0 1B** - Extremely fast and lightweight.
3. **Granite 4.0 350M** - Ultra-lightweight for basic correction.
 
---
 
**Last Updated**: 2026-02-20
**Version**: 1.0.0
**Author**: TextCorrector Contributors
