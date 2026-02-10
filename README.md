# TextCorrector

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Python: 3.12](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![Platform: Windows](https://img.shields.io/badge/Platform-Windows-lightgrey.svg)]()

TextCorrector is a high-performance, **100% private**, local AI-powered writing assistant for Windows. Refine your grammar, spelling, and tone instantly without ever sending your data to the cloud.

---

## ✨ Features

- 🔒 **Total Privacy**: All processing happens locally on your machine.
- ⚡ **Instant Correction**: Use the global `Alt + Shift + T` hotkey from any Windows application.
- 🧠 **Smart Refinement**: Leverages quantized GGUF models via `llama.cpp` for state-of-the-art results.
- 🎨 **Tone Control**: Easily adjust instructions to make your text more professional, casual, or creative.
- ⚙️ **Resource Efficient**: Configurable auto-unload settings to free up GPU memory when idle.

---

## 🚀 Quick Start Guide

Setting up TextCorrector takes less than 5 minutes.

### 1. Download & Extract
- Download the project as a [ZIP file](https://github.com/AmrZriek/TextCorrector/archive/refs/heads/main.zip) (or via the **Code** button).
- Extract the contents to a folder on your computer (e.g., `Desktop\TextCorrector`).

### 2. Install Python
- **Requirement**: Python 3.12 or higher.
- **Download**: [Windows Installer](https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe).
- **CRITICAL**: Ensure you check the box **"Add Python to PATH"** during installation.

### 3. Load the AI Engine (Models)
The application requires a GGUF model file to function. For the best balance of speed and quality, we recommend **Unsloth Dynamic (UD)** quants.

#### Recommended Models
| Model Name | Download Link (GGUF) | VRAM Usage | Best For |
| :--- | :--- | :--- | :--- |
| **Ministral 3 3B** | [Unsloth Ministral 3 3B](https://huggingface.co/unsloth/Ministral-3-3B-Instruct-2512-GGUF) | ~4-6 GB | Best quality for specialized correction. |
| **Granite 4.0 1B** | [Unsloth Granite 4.0 1B](https://huggingface.co/unsloth/granite-4.0-h-1b-GGUF) | ~1.5-2 GB | Fast and reliable for most PCs. |
| **Granite 4.0 350M** | [Unsloth Granite 4.0 350M](https://huggingface.co/unsloth/granite-4.0-h-350m-GGUF) | ~0.5-1 GB | Extremely light for older hardware. |

> [!TIP]
> **Quantization Advice**: When choosing a file, look for **`Q8_0`** or **`XL`** (Dynamic) quants for the best quality-to-size ratio. Avoid "Thinking" models (like DeepSeek R1) as they are incompatible with the interface.

---

## 🛠️ Operating Instructions

1.  **Launch**: Double-click **`run.bat`**. 
    > *Note: If Windows SmartScreen appears, click "More info" > "Run anyway".*
2.  **Initialize**: Right-click the application icon in your **System Tray** and select your loaded model.
3.  **Correct**: Highlight any text in your document/browser and press **`Alt + Shift + T`**.
4.  **Confirm**: Review the correction and click **Accept & Paste** to replace the original text.

---

## 📊 Technical Specifications

| Requirement | Recommended | Minimum |
| :--- | :--- | :--- |
| **OS** | Windows 10/11 | Windows 10 |
| **Python** | 3.12+ | 3.12 |
| **Storage** | 1GB+ (incl. models) | 500MB+ |
| **Graphics** | NVIDIA GPU (CUDA 12.4) | Integrated / CPU |

---

## ⚖️ License & Privacy

### Privacy Commitment
Your text data is processed exclusively on your local hardware. No logs or snippets are transmitted to external servers, and no internet connection is required after the initial setup.

### Legal
This project is licensed under the **GNU General Public License v3.0**. See the [LICENSE](LICENSE) file for details. Unauthorized commercial resale of this software is strictly prohibited.
