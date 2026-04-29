# release.ps1 - TextCorrector release flow.
# Usage:  .\release.ps1                  -> uses v3.1.1
#         .\release.ps1 -Version 3.1.2   -> custom version
[CmdletBinding()]
param(
    [string]$Version = "3.1.1",
    [string]$Message = "fix: rewrite hotkey to standard pattern; F9 default; Win32 clipboard + SendInput; HotkeyEdit accepts standalone keys"
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "==> Verifying clean working tree" -ForegroundColor Cyan
$gitStatus = git status --porcelain
if ($gitStatus) {
    Write-Host "Pending changes detected - staging selected files only." -ForegroundColor Yellow
}

Write-Host "==> Running tests" -ForegroundColor Cyan
& .\venv\Scripts\python.exe -m pytest tests/ -v
if ($LASTEXITCODE -ne 0) { throw "Tests failed (exit $LASTEXITCODE) - aborting release" }

Write-Host "==> Staging files" -ForegroundColor Cyan
git add text_corrector.py build.py requirements.txt AGENT_CONTEXT.md .gitignore release.ps1
if (Test-Path graphify-out\graph.json) {
    git add graphify-out\graph.json graphify-out\graph.html graphify-out\GRAPH_REPORT.md
    if (Test-Path graphify-out\.graphify_incremental.json) {
        git add graphify-out\.graphify_incremental.json
    }
}

Write-Host "==> Committing" -ForegroundColor Cyan
$body = @"
$Message

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
"@
git commit -m $body
if ($LASTEXITCODE -ne 0) { throw "git commit failed (exit $LASTEXITCODE)" }

Write-Host "==> Pushing to origin/main" -ForegroundColor Cyan
git push origin main
if ($LASTEXITCODE -ne 0) { throw "git push failed (exit $LASTEXITCODE)" }

Write-Host "==> Building release v$Version" -ForegroundColor Cyan
& .\venv\Scripts\python.exe build.py --version $Version
if ($LASTEXITCODE -ne 0) { throw "build.py failed (exit $LASTEXITCODE)" }

$zip = Get-ChildItem dist -Filter "TextCorrector_${Version}_*.zip" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $zip) { throw "Release ZIP not found in dist/" }
$sizeMb = [math]::Round($zip.Length / 1MB, 1)
Write-Host "    Found: $($zip.FullName) ($sizeMb MB)" -ForegroundColor Green

Write-Host "==> Tagging v$Version" -ForegroundColor Cyan
git tag -a "v$Version" -m "Release v$Version"
git push origin "v$Version"
if ($LASTEXITCODE -ne 0) { throw "git push tag failed (exit $LASTEXITCODE)" }

Write-Host "==> Creating GitHub release" -ForegroundColor Cyan
$notes = @"
## What's new in v$Version

**Hotkey system rewritten to the standard global-tool pattern:**
- Default hotkey changed to **F9** (was Ctrl+Shift+Space)
- Removed ``suppress=True`` (was blocking Ctrl system-wide)
- Removed ``trigger_on_release=True`` (was missing short presses)
- Direct Win32 ``SendInput`` for Ctrl+C/V on Windows (no more stuck Ctrl)
- Direct Win32 clipboard read/write for full Unicode support (math symbols, emojis)
- Settings hotkey recorder now accepts standalone keys (F1-F12, Pause, Insert, etc.)

**Tests added** under ``tests/`` covering the recorder, input synthesis, and clipboard round-trip.
"@
gh release create "v$Version" $zip.FullName --title "TextCorrector v$Version" --notes $notes
if ($LASTEXITCODE -ne 0) { throw "gh release create failed (exit $LASTEXITCODE)" }

Write-Host ""
Write-Host "Done. https://github.com/AmrZriek/TextCorrector/releases/tag/v$Version" -ForegroundColor Green
