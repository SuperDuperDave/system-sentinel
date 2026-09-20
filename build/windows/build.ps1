<#
    Build dist\SystemSentinel.exe: one windowed file a person double-clicks.

    Run it on the Windows side, from the repository root as Windows sees it:

        powershell.exe -NoProfile -ExecutionPolicy Bypass -File build\windows\build.ps1

    It builds the dashboard if sentinel\static\index.html is missing, makes a Windows virtual
    environment at .venv-win, installs the package with its [launcher] extra and PyInstaller,
    writes the icon from the mark, and runs PyInstaller against build\windows\SystemSentinel.spec.
    Everything it creates is ignored by git.
#>
param(
    [string]$Python = "C:\Python313\python.exe"
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).ProviderPath
Push-Location $root
try {
    if (-not (Test-Path "sentinel\static\index.html")) {
        Write-Host "== dashboard"
        Push-Location "dashboard"
        try { npm ci; npm run build } finally { Pop-Location }
    }

    $venv = Join-Path $root ".venv-win\Scripts\python.exe"
    if (-not (Test-Path $venv)) {
        Write-Host "== virtual environment (.venv-win)"
        & $Python -m venv .venv-win
    }

    Write-Host "== dependencies"
    & $venv -m pip install --quiet --upgrade pip
    & $venv -m pip install --quiet -e ".[launcher]" pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

    Write-Host "== icon"
    & $venv build\windows\make_icon.py
    if ($LASTEXITCODE -ne 0) { throw "the icon could not be written" }

    Write-Host "== exe"
    $started = Get-Date
    & $venv -m PyInstaller --noconfirm --clean --distpath dist --workpath build\windows\work build\windows\SystemSentinel.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

    $exe = Get-Item "dist\SystemSentinel.exe"
    "built {0:N1} MB in {1:N0}s -> {2}" -f ($exe.Length / 1MB), ((Get-Date) - $started).TotalSeconds, $exe.FullName
}
finally {
    Pop-Location
}
