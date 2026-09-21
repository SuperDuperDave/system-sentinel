<#
  run.ps1 - the host side of the clean-machine test.

  Stages the built executable and inside.ps1 into a folder under %TEMP%, writes a
  Windows Sandbox configuration that maps that folder in read-only and a results
  folder writable, and starts the sandbox. The sandbox runs inside.ps1 at logon,
  writes everything it learns into the results folder, and shuts itself down.

  Nothing is installed on this machine and nothing outside the temp folder is
  touched. Run it from WSL with:

      powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(wslpath -w run.ps1)"
#>

[CmdletBinding()]
param(
    [switch]$SkipSource,
    [string]$Release,
    [string]$ExePath,
    [string]$SandboxRoot = (Join-Path $env:TEMP 'sentinel-sandbox')
)

$ErrorActionPreference = 'Stop'

# Started from WSL, the current directory is a \\wsl.localhost UNC path that child processes
# cannot use. Everything below is computed from $PSScriptRoot, so move somewhere local first.
Set-Location -LiteralPath $env:TEMP

$sandboxExe = Join-Path $env:SystemRoot 'System32\WindowsSandbox.exe'
if (-not (Test-Path $sandboxExe)) {
    throw "Windows Sandbox is not installed: $sandboxExe is missing. Enabling the feature needs elevation and a restart; this script will not do that."
}
if (Get-Process -Name 'WindowsSandbox', 'WindowsSandboxClient' -ErrorAction SilentlyContinue) {
    throw 'A Windows Sandbox is already running. Only one can run at a time; close it and run this again.'
}

if (-not $ExePath) {
    $repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
    $ExePath = Join-Path $repo 'dist\SystemSentinel.exe'
}
if (-not (Test-Path $ExePath) -and -not $Release) {
    throw "No built executable at $ExePath. Build it with build\windows\build.ps1, pass -ExePath, or pass -Release with the URL of a published SystemSentinel.exe."
}

$inDir  = Join-Path $SandboxRoot 'in'
$outDir = Join-Path $SandboxRoot 'out'
$wsb    = Join-Path $SandboxRoot 'sentinel.wsb'

New-Item -ItemType Directory -Force -Path $inDir  | Out-Null
if (Test-Path $outDir) { Remove-Item -Recurse -Force $outDir }
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $inDir 'SystemSentinel.exe')
if (-not $Release) {
    Copy-Item $ExePath (Join-Path $inDir 'SystemSentinel.exe') -Force
    $staged = Get-Item (Join-Path $inDir 'SystemSentinel.exe')
    if ($staged.Length -ne (Get-Item $ExePath).Length) {
        throw 'The executable did not copy completely into the staging folder.'
    }
}
Copy-Item (Join-Path $PSScriptRoot 'inside.ps1') (Join-Path $inDir 'inside.ps1') -Force

# The sandbox runs inside.ps1 as WDAGUtilityAccount the moment the desktop is up. Networking is
# "Default", which is the documented way to say enabled; "Enable" is not a value the schema
# accepts, and an unparseable .wsb fails with a dialog nobody is here to dismiss.
$logon = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\in\inside.ps1'
if ($SkipSource) { $logon += ' -SkipSource' }
if ($Release) { $logon += " -Release $Release" }

$xml = @"
<Configuration>
  <MemoryInMB>8192</MemoryInMB>
  <Networking>Default</Networking>
  <MappedFolders>
    <MappedFolder>
      <HostFolder>$inDir</HostFolder>
      <SandboxFolder>C:\in</SandboxFolder>
      <ReadOnly>true</ReadOnly>
    </MappedFolder>
    <MappedFolder>
      <HostFolder>$outDir</HostFolder>
      <SandboxFolder>C:\out</SandboxFolder>
      <ReadOnly>false</ReadOnly>
    </MappedFolder>
  </MappedFolders>
  <LogonCommand>
    <Command>$logon</Command>
  </LogonCommand>
</Configuration>
"@

[IO.File]::WriteAllText($wsb, $xml, (New-Object System.Text.UTF8Encoding($false)))

# Parse it here rather than letting the sandbox refuse it silently.
[xml](Get-Content -Raw $wsb) | Out-Null

Start-Process -FilePath $sandboxExe -ArgumentList "`"$wsb`""

[pscustomobject]@{
    configuration = $wsb
    staged        = $inDir
    results       = $outDir
    watch         = (Join-Path $outDir 'result.json')
    finishedWhen  = (Join-Path $outDir 'done')
    skipSource    = [bool]$SkipSource
    release       = $(if ($Release) { $Release } else { '(none: the staged executable)' })
} | Format-List
