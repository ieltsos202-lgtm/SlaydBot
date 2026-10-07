$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
while ($true) {
    & "$PSScriptRoot\.venv\Scripts\python.exe" "$PSScriptRoot\main.py"
    Write-Host "Bot to'xtadi, 5 soniyadan so'ng qayta ishga tushadi..."
    Start-Sleep -Seconds 5
}
