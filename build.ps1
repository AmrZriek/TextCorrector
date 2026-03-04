$ErrorActionPreference = "Stop"
cd "c:\Users\Amrzr\Desktop\AI Software\Other\TextCorrector"

# Fix potential ExecutionPolicy issue temporarily for this process
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

# Install pyinstaller directly using the venv pip
& .\venv\Scripts\pip.exe install pyinstaller

# Build the executable using PyInstaller in --onedir mode
& .\venv\Scripts\pyinstaller.exe --noconfirm --windowed --name TextCorrector text_corrector.py

# Create final distribution folder
$DistDir = "TextCorrector_Release"
if (Test-Path $DistDir) { Remove-Item -Recurse -Force $DistDir }
New-Item -ItemType Directory -Path $DistDir | Out-Null

# Copy the built app from dist
Write-Host "Copying built executables..."
Copy-Item -Path "dist\TextCorrector\*" -Destination $DistDir -Recurse

# Copy llama_cpp (engine)
Write-Host "Copying llama_cpp..."
Copy-Item -Path "llama_cpp" -Destination "$DistDir\llama_cpp" -Recurse

# Copy the specific model requested
Write-Host "Copying model file..."
Copy-Item -Path "E:\LLM\models\unsloth\Qwen3.5-2B-GGUF\Qwen3.5-2B-UD-Q4_K_XL.gguf" -Destination "$DistDir\"

# Clean up build artifacts
Write-Host "Cleaning up build artifacts..."
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }
if (Test-Path "TextCorrector.exe.spec") { Remove-Item -Force "TextCorrector.exe.spec" }

Write-Host "Build complete! App is ready in folder: $DistDir"
