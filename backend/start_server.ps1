$ErrorActionPreference = "Stop"

function Print-Logo {
    Write-Host "   _____  __  __  _____  _______  ______  __  __ " -ForegroundColor Cyan
    Write-Host "  / ____||  \/  |/ ____||__   __||  ____||  \/  |" -ForegroundColor Cyan
    Write-Host " | (___  | \  / || (___     | |   | |__   | \  / |" -ForegroundColor Cyan
    Write-Host "  \___ \ | |\/| | \___ \    | |   |  __|  | |\/| |" -ForegroundColor Cyan
    Write-Host "  ____) || |  | | ____) |   | |   | |____ | |  | |" -ForegroundColor Cyan
    Write-Host " |_____/ |_|  |_||_____/  _ |_| _ |______||_|  |_|" -ForegroundColor Cyan
    Write-Host "   _____  ______  _   _  _______  _____  _   _  ______  _      " -ForegroundColor Blue
    Write-Host "  / ____||  ____|| \ | ||__   __||_   _|| \ | ||  ____|| |     " -ForegroundColor Blue
    Write-Host " | (___  | |__   |  \| |   | |     | |  |  \| || |__   | |     " -ForegroundColor Blue
    Write-Host "  \___ \ |  __|  | .   |   | |     | |  | .   | |  __|  | |     " -ForegroundColor Blue
    Write-Host "  ____) || |____ | |\  |   | |    _| |_ | |\  || |____ | |____ " -ForegroundColor Blue
    Write-Host " |_____/ |______||_| \_|   |_|   |_____||_| \_||______||______|" -ForegroundColor Blue
    Write-Host ""
    Write-Host " :: SYSTEM SENTINEL WATCHDOG :: " -ForegroundColor DarkGray
    Write-Host ""
}

Clear-Host
Print-Logo

$ScriptDir = $PSScriptRoot
Set-Location $ScriptDir

# 1. Check Execution Environment
if (Test-Path ".\venv\bin\activate") {
    Write-Host "[*] Linux Virtual Environment detected." -ForegroundColor Cyan
    Write-Host "    Delegating execution to WSL..." -ForegroundColor DarkGray
    
    # 2. Convert UNC Path to WSL Path
    # e.g. \\wsl.localhost\Ubuntu\home\user\repo -> /home/user/repo
    $WslPath = $ScriptDir
    if ($ScriptDir -match "^\\\\wsl\.localhost\\[^\\]+(.*)") {
        $WslPath = $Matches[1].Replace('\', '/')
    }
    
    Write-Host "    Target: $WslPath" -ForegroundColor DarkGray
    Write-Host ""
    
    try {
        # Execute inside WSL
        # We assume the default distro is correct.
        wsl.exe bash -c "cd '$WslPath' && source venv/bin/activate && python -m uvicorn main:app --port 8001 --reload"
    } catch {
        Write-Host "[!] WSL EXECUTION FAILED: $($_.Exception.Message)" -ForegroundColor Red
    }
    
} elseif (Test-Path ".\venv\Scripts\Activate.ps1") {
    # Windows Native
    Write-Host "[*] Windows Virtual Environment detected." -ForegroundColor Green
    & ".\venv\Scripts\Activate.ps1"
    
    # Check Port
    if (Get-NetTCPConnection -LocalPort 8001 -ErrorAction SilentlyContinue) {
        Write-Host "[!] Port 8001 is busy." -ForegroundColor Orange
    }

    Write-Host "[*] Starting Uvicorn Server..." -ForegroundColor Green
    Write-Host "    http://localhost:8001" -ForegroundColor Gray
    Write-Host ""
    
    try {
        python -m uvicorn main:app --port 8001 --reload
    } catch {
        Write-Host "[!] CRITICAL ERROR: $($_.Exception.Message)" -ForegroundColor Red
    }
} else {
    Write-Host "[!] No compatible venv found." -ForegroundColor Red
    Write-Host "    Make sure you have created a virtual environment."
}

Write-Host ""
Write-Host "Server process has ended." -ForegroundColor Yellow
Read-Host "Press Enter to close this window..."
