# release.ps1 - TextCorrector release flow.
# Usage:  .\release.ps1                  -> uses v3.1.1
#         .\release.ps1 -Version 3.1.2   -> custom version
[CmdletBinding()]
param(
    [string]$Version = "3.2.0",
    [string]$Message = "feat: replace llama.cpp updater with full application auto-updater"
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

**Full Application Auto-Updater:**
- TextCorrector can now seamlessly update itself when new versions are released.
- Instead of just checking for \`llama.cpp\` updates, the app now monitors GitHub for full TextCorrector releases.
- Updates are one-click directly from the system tray menu—no more manual downloads or ZIP extractions.
- Your existing \`config.json\` and downloaded AI models are strictly preserved during the update process.
"@
gh release create "v$Version" $zip.FullName --title "TextCorrector v$Version" --notes $notes
if ($LASTEXITCODE -ne 0) { throw "gh release create failed (exit $LASTEXITCODE)" }

Write-Host ""
Write-Host "Done. https://github.com/AmrZriek/TextCorrector/releases/tag/v$Version" -ForegroundColor Green
