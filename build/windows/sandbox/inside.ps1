<#
  inside.ps1 - runs inside Windows Sandbox as WDAGUtilityAccount, started by the
  .wsb LogonCommand. Nobody is at the keyboard: nothing here may wait for input,
  every external command is bounded and wrapped, and the run always ends by
  writing C:\out\done and shutting the sandbox down.

  It observes two install paths on a machine that has never seen this tool:
    Part 1  the person's path: one file, double-clicked.
    Part 2  the agent's path: the prompt in docs/DEPLOY.md, step by step.

  Everything it learns goes to C:\out (a folder on the host): result.json after
  every step, transcript.log, the redirected output of each command under logs\,
  and the screenshots.
#>

param([switch]$SkipSource, [string]$Release)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

$IN   = 'C:\in'
$OUT  = 'C:\out'
$LOGS = Join-Path $OUT 'logs'
$API  = 'http://127.0.0.1:8000'

New-Item -ItemType Directory -Force -Path $LOGS | Out-Null
try { Start-Transcript -Path (Join-Path $OUT 'transcript.log') -Force | Out-Null } catch {}

$script:Results  = New-Object System.Collections.ArrayList
$script:Secrets  = New-Object System.Collections.ArrayList
$script:Start    = Get-Date
$script:Deadline = $script:Start.AddMinutes(40)
$script:Seq      = 0
$script:Finished = $false

# A backstop, in case this script itself wedges: the sandbox closes either way.
try { & "$env:SystemRoot\System32\shutdown.exe" /s /t 3300 2>&1 | Out-Null } catch {}

# --- the record ---------------------------------------------------------------------------

function Add-Secret {
    param([string]$Value)
    if ($Value -and $Value.Trim().Length -ge 8) {
        $v = $Value.Trim()
        if (-not $script:Secrets.Contains($v)) { [void]$script:Secrets.Add($v) }
    }
}

function Protect-Text {
    param([string]$Text)
    if (-not $Text) { return $Text }
    foreach ($s in $script:Secrets) { $Text = $Text.Replace($s, '<REDACTED>') }
    return $Text
}

function Get-Tail {
    param([string]$Text, [int]$Lines = 14, [int]$Max = 2400)
    if (-not $Text) { return '' }
    $t = ($Text -replace "`r", '').TrimEnd("`n")
    if ($t -eq '') { return '' }
    $arr = $t.Split("`n")
    if ($arr.Count -gt $Lines) { $arr = $arr[($arr.Count - $Lines)..($arr.Count - 1)] }
    $s = ($arr -join "`n")
    if ($s.Length -gt $Max) { $s = '...' + $s.Substring($s.Length - $Max) }
    return (Protect-Text $s)
}

function Save-Results {
    try {
        $doc = [ordered]@{
            harness  = 'system-sentinel clean-machine test, Windows Sandbox'
            finished = $script:Finished
            elapsed  = [math]::Round(((Get-Date) - $script:Start).TotalSeconds, 1)
            steps    = @($script:Results)
        }
        [IO.File]::WriteAllText((Join-Path $OUT 'result.json'), ($doc | ConvertTo-Json -Depth 8))
    } catch { Write-Host "result.json could not be written: $_" }
}

function Add-Result {
    param([string]$step, [bool]$ok, [double]$seconds = 0, $exit = $null, [string]$note = '', [string]$tail = '')
    $script:Seq++
    $r = [ordered]@{
        n       = $script:Seq
        step    = $step
        ok      = $ok
        seconds = [math]::Round($seconds, 1)
        exit    = $exit
        note    = (Protect-Text $note)
        tail    = (Protect-Text $tail)
    }
    [void]$script:Results.Add($r)
    Write-Host ("[{0}] {1}  ok={2}  {3}s  exit={4}  :: {5}" -f $script:Seq, $step, $ok, $r.seconds, $exit, $r.note)
    Save-Results
}

function Get-BudgetLeft { return ($script:Deadline - (Get-Date)).TotalSeconds }

function Test-Budget {
    param([string]$Name)
    if ((Get-BudgetLeft) -le 30) {
        Add-Result -step $Name -ok $false -note 'skipped: the run budget was exhausted before this step'
        return $false
    }
    return $true
}

# --- running things -----------------------------------------------------------------------

function Invoke-Native {
    param([string]$File, [string]$ArgLine = '', [string]$WorkDir = $null, [int]$TimeoutSec = 300, [string]$Tag = 'cmd')

    $cap = [int][math]::Max(15, [math]::Min($TimeoutSec, (Get-BudgetLeft)))
    $id  = '{0:d2}-{1}' -f ($script:Seq + 1), ($Tag -replace '[^A-Za-z0-9\.\-]', '_')
    $so  = Join-Path $LOGS "$id.out.txt"
    $se  = Join-Path $LOGS "$id.err.txt"
    $t0  = Get-Date
    $res = [ordered]@{ exit = $null; out = ''; err = ''; seconds = 0; timedout = $false; launched = $true; error = ''; cap = $cap }

    try {
        $sp = @{ FilePath = $File; PassThru = $true; NoNewWindow = $true; RedirectStandardOutput = $so; RedirectStandardError = $se }
        if ($ArgLine -ne '') { $sp['ArgumentList'] = $ArgLine }
        if ($WorkDir) { $sp['WorkingDirectory'] = $WorkDir }
        $p = Start-Process @sp
        # Touching Handle while the process lives is what makes ExitCode readable afterwards;
        # without it Start-Process -PassThru hands back a process whose exit code is always null.
        $null = $p.Handle
        if (-not $p.WaitForExit($cap * 1000)) {
            $res.timedout = $true
            try { & "$env:SystemRoot\System32\taskkill.exe" /T /F /PID $p.Id 2>&1 | Out-Null } catch {}
            Start-Sleep -Seconds 2
        }
        Start-Sleep -Milliseconds 500
        try { $res.exit = $p.ExitCode } catch { $res.exit = $null }
    } catch {
        $res.launched = $false
        $res.error = "$_"
    }

    $res.seconds = ((Get-Date) - $t0).TotalSeconds
    if (Test-Path $so) { $res.out = [string](Get-Content -Raw -ErrorAction SilentlyContinue $so) }
    if (Test-Path $se) { $res.err = [string](Get-Content -Raw -ErrorAction SilentlyContinue $se) }
    return $res
}

function Step-Native {
    param(
        [string]$Name, [string]$File, [string]$ArgLine = '', [string]$WorkDir = $null,
        [int]$TimeoutSec = 300, [string]$Note = '', [int[]]$OkExit = @(0)
    )
    $r = Invoke-Native -File $File -ArgLine $ArgLine -WorkDir $WorkDir -TimeoutSec $TimeoutSec -Tag $Name
    $ok = ($r.launched -and -not $r.timedout -and ($OkExit -contains $r.exit))
    $n  = $Note
    if (-not $r.launched)  { $n = "$Note | did not launch: $($r.error)" }
    elseif ($r.timedout)   { $n = "$Note | TIMED OUT after $($r.cap)s with no input available; killed" }
    Add-Result -step $Name -ok $ok -seconds $r.seconds -exit $r.exit -note $n -tail (Get-Tail ($r.out + "`n" + $r.err))
    return $r
}

# A command as an agent would type it in PowerShell: a child shell, so the shell's own
# "not recognized" error is recorded verbatim alongside the native exit code.
function Invoke-InShell {
    param([string]$Command, [int]$TimeoutSec = 120, [string]$Tag = 'shell', [string]$WorkDir = $null)
    $ps = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    # No forced exit: the child shell's own exit code is what an agent would see (1 when
    # PowerShell itself cannot find the command, the program's code when it ran).
    $inner = '$ErrorActionPreference=''Continue''; ' + $Command
    $argline = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "' + ($inner -replace '"', '\"') + '"'
    return (Invoke-Native -File $ps -ArgLine $argline -WorkDir $WorkDir -TimeoutSec $TimeoutSec -Tag $Tag)
}

function Update-PathFromRegistry {
    $m = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $u = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = (@($m, $u) | Where-Object { $_ }) -join ';'
}

# Windows Installer serializes: a second MSI started while another is running fails with 1618.
function Wait-ForInstallerIdle {
    param([int]$TimeoutSec = 180)
    $t0 = Get-Date
    while (((Get-Date) - $t0).TotalSeconds -lt $TimeoutSec) {
        $busy = @(Get-Process -Name 'msiexec', 'python-3*', 'Git-*' -ErrorAction SilentlyContinue |
                  Where-Object { $_.SessionId -ne 0 })
        if ($busy.Count -eq 0) { return }
        Start-Sleep -Seconds 3
    }
}

function Resolve-Tool {
    param([string]$Name)
    $c = Get-Command $Name -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return $c.Source }
    return $null
}

function Save-Screenshot {
    param([string]$Name)
    try {
        Add-Type -AssemblyName System.Drawing -ErrorAction Stop
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
        $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.CopyFromScreen($b.X, $b.Y, 0, 0, $bmp.Size)
        $bmp.Save((Join-Path $OUT $Name), [System.Drawing.Imaging.ImageFormat]::Png)
        $g.Dispose(); $bmp.Dispose()
        return "$Name ($($b.Width)x$($b.Height))"
    } catch {
        return "screenshot failed: $_"
    }
}

# Dialogs like "Open File - Security Warning" do not show up as a process MainWindowTitle, so
# the real top-level window list is read from user32 directly.
try {
    Add-Type -TypeDefinition @"
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public class Win32Windows {
  delegate bool EnumProc(IntPtr h, IntPtr p);
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc cb, IntPtr p);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowTextW(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] static extern int GetWindowTextLength(IntPtr h);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern IntPtr PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
  public static List<string> List() {
    var found = new List<string>();
    EnumWindows(delegate(IntPtr h, IntPtr p) {
      if (!IsWindowVisible(h)) return true;
      int len = GetWindowTextLength(h);
      if (len == 0) return true;
      var sb = new StringBuilder(len + 2);
      GetWindowTextW(h, sb, sb.Capacity);
      uint pid; GetWindowThreadProcessId(h, out pid);
      found.Add(h.ToInt64() + "\t" + pid + "\t" + sb.ToString());
      return true;
    }, IntPtr.Zero);
    return found;
  }
}
"@ -ErrorAction Stop
} catch { Write-Host "user32 window enumeration unavailable: $_" }

function Get-TopLevelWindows {
    try {
        $names = @{}
        Get-Process | ForEach-Object { $names[[string]$_.Id] = $_.ProcessName }
        $rows = @([Win32Windows]::List())
        if ($rows.Count -eq 0) { return '(no visible top-level window has a title)' }
        return (($rows | ForEach-Object {
            $f = $_.Split("`t")
            $pn = $names[$f[1]]
            if (-not $pn) { $pn = '?' }
            "$pn ($($f[1])) :: $($f[2])"
        }) -join "`n")
    } catch { return "top-level windows unavailable: $_" }
}

function Close-WindowByTitle {
    param([string]$Like)
    $closed = @()
    try {
        foreach ($row in @([Win32Windows]::List())) {
            $f = $row.Split("`t")
            if ($f[2] -like $Like) {
                [void][Win32Windows]::PostMessage([IntPtr][int64]$f[0], 0x0010, [IntPtr]::Zero, [IntPtr]::Zero)
                $closed += $f[2]
            }
        }
    } catch { }
    return ($closed -join ' | ')
}

function Get-HttpAssociation {
    try {
        $k = 'HKCU:\Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice'
        $v = (Get-ItemProperty -Path $k -Name ProgId -ErrorAction SilentlyContinue).ProgId
        if ($v) { return $v }
        return '(no http UserChoice: this account has no default browser)'
    } catch { return "unreadable: $_" }
}

function Get-WindowTitles {
    try {
        $w = Get-Process | Where-Object { $_.MainWindowTitle } |
             Select-Object ProcessName, MainWindowTitle |
             Sort-Object ProcessName
        return (($w | ForEach-Object { "$($_.ProcessName) :: $($_.MainWindowTitle)" }) -join "`n")
    } catch { return "window titles unavailable: $_" }
}

function Invoke-Api {
    param([string]$Path, [string]$Token, [int]$TimeoutSec = 20)
    $h = @{ Authorization = "Bearer $Token" }
    return Invoke-WebRequest -UseBasicParsing -Uri ($API + $Path) -Headers $h -TimeoutSec $TimeoutSec
}

# Which version is answering, or '' when nothing is. The catalog carries it, so this is the same
# question a downloaded copy asks before it decides whether it is an update.
function Get-ServedVersion {
    param([string]$Token, [int]$TimeoutSec = 10)
    try { return [string]((Invoke-Api -Path '/api/readings' -Token $Token -TimeoutSec $TimeoutSec).Content | ConvertFrom-Json).version } catch { return '' }
}

# ============================================================================================

try {

# What Defender was doing matters to everything below it: a timing, or a file that was never
# blocked, means one thing with real-time protection on and another with it off.
try {
    $mp = Get-MpComputerStatus -ErrorAction Stop
    $defender = "Defender RealTimeProtectionEnabled=$($mp.RealTimeProtectionEnabled), AntivirusEnabled=$($mp.AntivirusEnabled)"
} catch { $defender = "Get-MpComputerStatus did not answer, so the state of Defender on this image is unknown: $_" }

Add-Result -step '0-image' -ok $true -note ("PowerShell $($PSVersionTable.PSVersion); $((Get-CimInstance Win32_OperatingSystem).Caption) build $((Get-CimInstance Win32_OperatingSystem).BuildNumber); user $env:USERNAME; SkipSource=$($SkipSource.IsPresent); Release=$(if ($Release) { $Release } else { '(none: the staged executable)' }); $defender")

# --- the network ---------------------------------------------------------------------------

$t0 = Get-Date
$net = $false
while (((Get-Date) - $t0).TotalSeconds -lt 180) {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $iar = $c.BeginConnect('github.com', 443, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne(3000, $false)) { $c.EndConnect($iar); $net = $true }
        $c.Close()
    } catch { }
    if ($net) { break }
    Start-Sleep -Seconds 3
}
Add-Result -step 'network' -ok $net -seconds (((Get-Date) - $t0).TotalSeconds) -note $(if ($net) { 'github.com:443 answered' } else { 'github.com:443 never answered within 180s; the source path cannot run' })

# ============================================================================================
# PART 1 - the person's path: one file, double-clicked.
# ============================================================================================

$dataDir   = Join-Path $env:LOCALAPPDATA 'SystemSentinel'
$exePath   = Join-Path $dataDir 'SystemSentinel.exe'
$tokenPath = Join-Path $dataDir 'token'
$token     = $null

if ($Release) {
    # The release path, as the short prompt gives it to an agent: download, check the checksum
    # against the release's own list, unblock what Windows marked, and only then start it.
    if (Test-Budget 'p1-download') {
        $t0 = Get-Date
        $ok = $true; $note = "downloaded $Release to $exePath"
        try {
            New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
            Invoke-WebRequest -UseBasicParsing -Uri $Release -OutFile $exePath -TimeoutSec 300
            $note += " ($([math]::Round((Get-Item $exePath).Length / 1MB, 1)) MB)"
            $zone = $null
            try { $zone = Get-Content -Path $exePath -Stream 'Zone.Identifier' -Raw -ErrorAction Stop } catch {}
            $note += "; Zone.Identifier after the download: " + $(if ($zone) { ($zone -replace "`r`n", ' / ').Trim() } else { 'none (this downloader does not mark the file; a browser would)' })
        } catch { $ok = $false; $note = "download failed: $_" }
        Add-Result -step 'p1-download' -ok $ok -seconds (((Get-Date) - $t0).TotalSeconds) -note $note
    }
    if ((Test-Path $exePath) -and (Test-Budget 'p1-checksum')) {
        $t0 = Get-Date
        $ok = $false; $note = ''
        try {
            $sumsUrl = ($Release -replace '[^/]+$', 'SHA256SUMS.txt')
            $sums = (Invoke-WebRequest -UseBasicParsing -Uri $sumsUrl -TimeoutSec 60).Content
            # A file:// answer arrives as bytes, an http one as text; the list is text either way.
            if ($sums -is [byte[]]) { $sums = [Text.Encoding]::UTF8.GetString($sums) }
            $line = [string](@(([string]$sums) -split "`n") | Where-Object { $_ -match 'SystemSentinel\.exe' } | Select-Object -First 1)
            $expected = ($line -replace '\s.*$', '')
            $actual = (Get-FileHash -Algorithm SHA256 $exePath).Hash
            $ok = ($expected -and ($actual -ieq $expected.Trim()))
            $note = "SHA-256 of the download $(if ($ok) { 'matches' } else { 'DOES NOT MATCH' }) $sumsUrl (expected $($expected.Trim()), got $actual)"
            if (-not $ok) { Remove-Item -Force $exePath }
        } catch { $note = "the checksum could not be checked: $_" }
        Add-Result -step 'p1-checksum' -ok $ok -seconds (((Get-Date) - $t0).TotalSeconds) -note $note
    }
    if ((Test-Path $exePath) -and (Test-Budget 'p1-unblock')) {
        $t0 = Get-Date
        $ok = $true; $note = "Unblock-File $exePath"
        try { Unblock-File -Path $exePath -ErrorAction Stop } catch { $ok = $false; $note = "Unblock-File failed: $_" }
        Add-Result -step 'p1-unblock' -ok $ok -seconds (((Get-Date) - $t0).TotalSeconds) -note $note
        # Kept for the mark-of-the-web observation at the end, which runs after the data directory is gone.
        try { Copy-Item $exePath (Join-Path $env:TEMP 'SystemSentinel.downloaded.exe') -Force } catch {}
    }
} elseif (Test-Budget 'p1-copy') {
    $t0 = Get-Date
    $ok = $true; $note = "copied C:\in\SystemSentinel.exe to $exePath"
    try {
        New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
        Copy-Item (Join-Path $IN 'SystemSentinel.exe') $exePath -Force
        $note += " ($([math]::Round((Get-Item $exePath).Length / 1MB, 1)) MB)"
    } catch { $ok = $false; $note = "copy failed: $_" }
    Add-Result -step 'p1-copy' -ok $ok -seconds (((Get-Date) - $t0).TotalSeconds) -note $note
}

$launchStart = $null
if ((Test-Path $exePath) -and (Test-Budget 'p1-first-200')) {
    $launchStart = Get-Date
    try { Start-Process -FilePath $exePath } catch { Add-Result -step 'p1-start' -ok $false -note "Start-Process failed: $_" }

    $tokenSeen = $null
    $first200  = $null
    $lastErr   = ''
    while (((Get-Date) - $launchStart).TotalSeconds -lt 90) {
        if (-not $token -and (Test-Path $tokenPath)) {
            try {
                $raw = (Get-Content -Raw -ErrorAction SilentlyContinue $tokenPath)
                if ($raw -and $raw.Trim()) {
                    $token = $raw.Trim()
                    Add-Secret $token
                    $tokenSeen = ((Get-Date) - $launchStart).TotalSeconds
                }
            } catch { }
        }
        if ($token) {
            try {
                $r = Invoke-Api -Path '/api/readings' -Token $token -TimeoutSec 5
                if ($r.StatusCode -eq 200) { $first200 = ((Get-Date) - $launchStart).TotalSeconds; break }
            } catch { $lastErr = "$_" }
        }
        Start-Sleep -Milliseconds 400
    }

    $note = if ($first200) {
        "token file appeared {0:n1}s after launch; first HTTP 200 from /api/readings {1:n1}s after launch" -f $tokenSeen, $first200
    } elseif ($token) {
        "token file appeared {0:n1}s after launch but /api/readings never answered 200 within 90s; last error: {1}" -f $tokenSeen, $lastErr
    } else {
        "no token file at $tokenPath within 90s of launch"
    }
    Add-Result -step 'p1-first-200' -ok ($first200 -ne $null) -seconds (((Get-Date) - $launchStart).TotalSeconds) -note $note
}

if ($token -and (Test-Budget 'p1-readings')) {
    $t0 = Get-Date
    $ok = $true; $note = ''
    foreach ($pair in @(@('/api/readings/health', 'part1-health.json'), @('/api/readings/events?count=5', 'part1-events.json'))) {
        try {
            $r = Invoke-Api -Path $pair[0] -Token $token -TimeoutSec 45
            [IO.File]::WriteAllText((Join-Path $OUT $pair[1]), $r.Content)
            $j = $r.Content | ConvertFrom-Json
            $count = 0
            $count = $j.count
            $note += "$($pair[0]) -> $($r.StatusCode), outcome=$($j.outcome), records=$count; "
        } catch { $ok = $false; $note += "$($pair[0]) -> FAILED: $_; " }
    }
    Add-Result -step 'p1-readings' -ok $ok -seconds (((Get-Date) - $t0).TotalSeconds) -note $note
}

if (Test-Path (Join-Path $dataDir 'launcher.log')) {
    $log = [string](Get-Content -Raw -ErrorAction SilentlyContinue (Join-Path $dataDir 'launcher.log'))
    try { [IO.File]::WriteAllText((Join-Path $OUT 'part1-launcher.log'), (Protect-Text $log)) } catch {}
    Add-Result -step 'p1-launcher-log' -ok $true -note "launcher.log has $(@(($log -replace "`r",'').Split("`n") | Where-Object { $_ }).Count) lines; copied to C:\out\part1-launcher.log" -tail (Get-Tail $log 20)
} else {
    Add-Result -step 'p1-launcher-log' -ok $false -note "no launcher.log in $dataDir"
}

Start-Sleep -Seconds 5
$titles = Get-TopLevelWindows
$assoc  = Get-HttpAssociation
$shot   = Save-Screenshot '1-dashboard.png'
Add-Result -step 'p1-screenshot' -ok ($shot -notlike 'screenshot failed*') `
    -note "$shot; the http protocol on this account is handled by: $assoc" -tail (Get-Tail $titles 24)

$dismissed = Close-WindowByTitle '*open this*link*'
if ($dismissed) {
    Add-Result -step 'p1-browser-dialog' -ok $false -note "a modal dialog was on screen instead of the dashboard and was closed by the harness: $dismissed"
}

# --- the update: a newer copy arrives while the downloaded one is serving ---------------------
# Only when there is something to update from and something to update to: a release was
# downloaded and is answering, and another executable is staged beside it in C:\in. That staged
# file stands in for the next download, started the way a person starts one - from wherever it
# landed, not from the data directory.

$staged = Join-Path $IN 'SystemSentinel.exe'
if ($Release -and (Test-Path $staged) -and $token -and (Test-Budget 'update-start')) {
    $t0 = Get-Date

    $before = Get-ServedVersion -Token $token -TimeoutSec 20
    $stagedVersion = ''
    try { $stagedVersion = [string](Get-Item $staged).VersionInfo.ProductVersion } catch {}
    $stagedVersion = $stagedVersion.Trim()

    if ($stagedVersion -and $before -eq $stagedVersion) {
        # The same version cannot update itself; say so rather than report a failure to change.
        Add-Result -step 'update-start' -ok $true -note "not attempted: the staged executable carries the same version as the copy already serving ($before), so there is nothing to update; stage a newer build to observe one"
    } else {
        $installed = Join-Path $dataDir 'SystemSentinel.exe'
        $oldProcesses = @()
        try { $oldProcesses = @(Get-CimInstance Win32_Process -Filter "Name='SystemSentinel.exe'" | Where-Object { $_.ExecutablePath -eq $installed }) } catch {}
        $launched = $true
        try { Start-Process -FilePath $staged } catch { $launched = $false }
        Add-Result -step 'update-start' -ok $launched -seconds (((Get-Date) - $t0).TotalSeconds) `
            -note ("the copy already serving reports version $(if ($before) { $before } else { '(unreadable)' }); the staged executable in C:\in carries $(if ($stagedVersion) { $stagedVersion } else { 'no version in its file properties' }); it was started from where it sits, as a download would be")

        $t0 = Get-Date
        $after = $before
        # Not $deadline: that name is the whole run's budget, and PowerShell would have it back.
        $updateUntil = (Get-Date).AddSeconds(90)
        while ((Get-Date) -lt $updateUntil) {
            Start-Sleep -Seconds 2
            $now = Get-ServedVersion -Token $token -TimeoutSec 5
            if ($now -and $now -ne $before) { $after = $now; break }
        }

        # When nothing changed, say why rather than leaving a bare failure: a copy whose version
        # predates the quit route cannot be asked to stop, and the person has to quit it themselves.
        $why = ''
        if ($after -eq $before) {
            $code = 'no answer'
            try {
                $q = Invoke-WebRequest -UseBasicParsing -Method Post -Uri "$API/api/quit" -Headers @{ Authorization = "Bearer $token" } -TimeoutSec 10
                $code = $q.StatusCode
            } catch { try { $code = $_.Exception.Response.StatusCode.value__ } catch { $code = 'no answer' } }
            $why = "; asked afterwards, POST /api/quit against the copy that was serving answers $code (404 or 405 means that version has no quit route at all, so a newer copy cannot ask it to stop and the person has to quit it themselves)"
        }

        $sameFile = $false
        try { $sameFile = ((Get-FileHash -Algorithm SHA256 $installed).Hash -ieq (Get-FileHash -Algorithm SHA256 $staged).Hash) } catch {}
        $afterProcesses = @()
        try { $afterProcesses = @(Get-CimInstance Win32_Process -Filter "Name='SystemSentinel.exe'" | Where-Object { $_.ExecutablePath -eq $installed }) } catch {}
        $oldRemaining = @($afterProcesses | Where-Object {
            $current = $_
            @($oldProcesses | Where-Object { $_.ProcessId -eq $current.ProcessId -and $_.CreationDate -eq $current.CreationDate }).Count -gt 0
        })
        $windows = Get-TopLevelWindows
        $shot = Save-Screenshot '1b-update.png'
        Add-Result -step 'update-took-over' -ok (($after -ne $before) -and $stagedVersion -and ($after -eq $stagedVersion) -and $sameFile -and ($oldProcesses.Count -gt 0) -and ($oldRemaining.Count -eq 0)) -seconds (((Get-Date) - $t0).TotalSeconds) `
            -note ("the server now reports $(if ($after) { $after } else { '(nothing answered)' }), where it reported $(if ($before) { $before } else { '(unreadable)' }); the installed copy is the staged executable byte for byte: $sameFile; previous installed processes still alive: $($oldRemaining.Count)$why; $shot") `
            -tail (Get-Tail $windows 24)

        $closed = Close-WindowByTitle '*System Sentinel*'
        if ($closed) {
            Add-Result -step 'update-dialog' -ok $false -note "a message box was on screen rather than a silent update, and the harness closed it: $closed"
        }
    }
} elseif ($Release -and (Test-Path $staged)) {
    Add-Result -step 'update-start' -ok $false -note 'not attempted: the downloaded release never answered, so there was nothing to update'
}

# Free port 8000 and leave the machine clean of the tool's state, so Part 2 exercises the
# agent's path on a data directory that does not exist yet.
try { Get-Process -Name SystemSentinel -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue } catch {}
Start-Sleep -Seconds 3
$gone = @(Get-Process -Name SystemSentinel -ErrorAction SilentlyContinue).Count
try { Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $dataDir } catch {}
try { Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $env:USERPROFILE 'Downloads\SystemSentinel.exe') } catch {}
Add-Result -step 'p1-teardown' -ok ($gone -eq 0) -note "SystemSentinel processes still running after stop: $gone; data directory removed so Part 2 starts with no token"

if ($SkipSource) {
    Add-Result -step 'part-2' -ok $true -note 'skipped: run.ps1 was given -SkipSource, so only the person''s path ran'
} else {

# ============================================================================================
# PART 2 - the agent's path: docs/DEPLOY.md's prompt, step by step.
# ============================================================================================

Add-Result -step '2-step-0' -ok $true -note 'not run: step 0 asks the agent to read the source and report what it finds. It is a judgment the agent makes, not a command with an exit code, so a harness cannot observe it. Nothing in it touches the machine.'

# --- step 1: prerequisites -------------------------------------------------------------------

foreach ($probe in @(@('git', 'git --version'), @('python', 'python --version'), @('node', 'node --version'))) {
    if (-not (Test-Budget "2-1-before-$($probe[0])")) { continue }
    $r = Invoke-InShell -Command $probe[1] -TimeoutSec 60 -Tag "before-$($probe[0])"
    $src = Resolve-Tool $probe[0]
    $body = "STDOUT>>" + $r.out + "<<STDOUT STDERR>>" + $r.err + "<<STDERR"
    Add-Result -step "2-1-before-$($probe[0])" -ok (($r.exit -eq 0) -and ($src -ne $null)) -seconds $r.seconds -exit $r.exit `
        -note "on the clean image, `"$($probe[1])`" exits $($r.exit); Get-Command resolves to: $(if ($src) { $src } else { 'nothing' })" `
        -tail (Get-Tail $body 20 3000)
}

# --- winget ------------------------------------------------------------------------------------

$winget = Resolve-Tool 'winget'
$wingetWasMissing = ($winget -eq $null)
Add-Result -step '2-1-winget-present' -ok (-not $wingetWasMissing) -note $(if ($wingetWasMissing) { 'winget is NOT on this image. The prompt tells the agent to install prerequisites with winget; on an image without the Store there is nothing to run.' } else { "winget found at $winget" })

if ($wingetWasMissing -and (Test-Budget '2-1-winget-bootstrap')) {
    $t0 = Get-Date
    $boot = ''
    $ok = $true
    $stage = Join-Path $env:TEMP 'winget-bootstrap'
    New-Item -ItemType Directory -Force -Path $stage | Out-Null

    # One release, not three scattered links. winget's own release ships the exact framework
    # versions it depends on; the aka.ms VCLibs link and the Microsoft.UI.Xaml NuGet package that
    # every bootstrap recipe names are older than what a current App Installer will accept, and
    # Add-AppxPackage refuses the bundle with 0x80073CF3 naming the framework it wanted.
    $bundle = $null
    $depsZip = $null
    try {
        $rel = Invoke-RestMethod -Uri 'https://api.github.com/repos/microsoft/winget-cli/releases/latest' -Headers @{ 'User-Agent' = 'system-sentinel-sandbox' } -TimeoutSec 60
        $boot += "winget-cli release $($rel.tag_name); "
        foreach ($want in @('DesktopAppInstaller_Dependencies.zip', 'Microsoft.DesktopAppInstaller_8wekyb3d8bbwe.msixbundle')) {
            $a = $rel.assets | Where-Object { $_.name -eq $want } | Select-Object -First 1
            if (-not $a) { $ok = $false; $boot += "$want is not in the release; "; continue }
            $dest = Join-Path $stage $want
            Invoke-WebRequest -UseBasicParsing -Uri $a.browser_download_url -OutFile $dest -TimeoutSec 600
            $boot += "downloaded $want ($([math]::Round((Get-Item $dest).Length / 1MB, 1)) MB); "
            if ($want -like '*.zip') { $depsZip = $dest } else { $bundle = $dest }
        }
    } catch { $ok = $false; $boot += "the winget release could not be fetched: $_; " }

    if ($depsZip) {
        try {
            $x = Join-Path $stage 'deps'
            Expand-Archive -Path $depsZip -DestinationPath $x -Force
            foreach ($appx in @(Get-ChildItem -Path (Join-Path $x 'x64') -Filter *.appx -ErrorAction SilentlyContinue)) {
                try { Add-AppxPackage -Path $appx.FullName -ErrorAction Stop; $boot += "$($appx.Name): ok; " }
                catch { $ok = $false; $boot += "$($appx.Name) FAILED: $_; " }
            }
        } catch { $ok = $false; $boot += "the dependencies could not be unpacked: $_; " }
    }

    if ($bundle) {
        try { Add-AppxPackage -Path $bundle -ErrorAction Stop; $boot += 'App Installer: ok; ' }
        catch { $ok = $false; $boot += "App Installer FAILED: $_; " }
    }

    $winget = Resolve-Tool 'winget'
    $viaPath = ($winget -ne $null)
    if (-not $winget) {
        $p = Get-AppxPackage Microsoft.DesktopAppInstaller -ErrorAction SilentlyContinue | Select-Object -First 1
        foreach ($cand in @((Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\winget.exe'), $(if ($p) { Join-Path $p.InstallLocation 'winget.exe' } else { $null }))) {
            if ($cand -and (Test-Path $cand)) { $winget = $cand; break }
        }
    }
    $boot += "after bootstrap, winget on PATH: $viaPath; winget resolved to: $(if ($winget) { $winget } else { 'nothing' })"
    Add-Result -step '2-1-winget-bootstrap' -ok ($ok -and $winget -ne $null) -seconds (((Get-Date) - $t0).TotalSeconds) -note $boot
}

# --- the three packages the prompt names ---------------------------------------------------

# The prompt writes `winget install Git.Git`. Whether that runs unattended is one of the things
# this test exists to find out, so the bare form is tried first, and only what it needs is added:
# the agreement flags, then a pinned source. Whichever form works is reused for the rest.
$script:WingetForm = $null

function Get-WingetArgs {
    param([string]$Id, [string]$Form)
    switch ($Form) {
        'plain'  { return "install $Id" }
        'flags'  { return "install $Id --accept-source-agreements --accept-package-agreements" }
        'source' { return "install --id $Id --exact --source winget --accept-source-agreements --accept-package-agreements" }
    }
}

function Get-WingetNote {
    param([string]$Form)
    switch ($Form) {
        'plain'  { return "the prompt's command, exactly as written" }
        'flags'  { return 'with the agreement flags the prompt does not mention' }
        'source' { return 'pinned to the winget source, which the prompt does not mention either' }
    }
}

function Install-Prereq {
    param([string]$Label, [string]$Id)
    if (-not $winget) {
        Add-Result -step "2-1-install-$Label" -ok $false -note 'not attempted: no winget on this image'
        return
    }
    if (-not (Test-Budget "2-1-install-$Label")) { return }

    $order = @('plain', 'flags', 'source')
    if ($script:WingetForm) { $order = @($script:WingetForm) + @($order | Where-Object { $_ -ne $script:WingetForm }) }

    foreach ($form in $order) {
        Wait-ForInstallerIdle
        $a = Get-WingetArgs -Id $Id -Form $form
        $r = Step-Native -Name "2-1-install-$Label-$form" -File $winget -ArgLine $a `
             -TimeoutSec $(if ($form -eq 'plain') { 200 } else { 900 }) `
             -Note "$(Get-WingetNote $form): winget $a" -OkExit @(0, -1978335135)
        if ($r.launched -and -not $r.timedout -and (@(0, -1978335135) -contains $r.exit)) {
            $script:WingetForm = $form
            return
        }
        # A timeout says the installer was slow, not that the command was wrong: the other forms
        # would be no faster, and the next package must not start while Windows Installer is busy.
        if ($r.timedout) { $script:WingetForm = $form; return }
    }
}

# The prompt names these three; the order is the harness's own. Python goes last because its
# installer does not exit inside Windows Sandbox, and Windows Installer refuses a second MSI
# (1618) while the first is still held open.
Install-Prereq -Label 'git'    -Id 'Git.Git'
Install-Prereq -Label 'node'   -Id 'OpenJS.NodeJS.LTS'
Install-Prereq -Label 'python' -Id 'Python.Python.3.13'

# --- the harness's own fallback, when winget could not be had at all -------------------------
# Not what the prompt says. It exists only so that a missing winget does not cost us the rest of
# the observation: steps 2 to 9 still need git, python and node from somewhere.

if (-not $winget) {
    Add-Result -step '2-1-fallback' -ok $true -note "no winget on this image and none could be bootstrapped, so the harness installs the three prerequisites from their vendors' own installers instead. An agent following the prompt would be stopped here."
    $stage = Join-Path $env:TEMP 'prereq'
    New-Item -ItemType Directory -Force -Path $stage | Out-Null

    if (Test-Budget '2-1-fallback-git') {
        try {
            $rel = Invoke-RestMethod -Uri 'https://api.github.com/repos/git-for-windows/git/releases/latest' -Headers @{ 'User-Agent' = 'system-sentinel-sandbox' } -TimeoutSec 60
            $asset = $rel.assets | Where-Object { $_.name -like '*-64-bit.exe' -and $_.name -notlike '*Portable*' } | Select-Object -First 1
            $f = Join-Path $stage $asset.name
            Invoke-WebRequest -UseBasicParsing -Uri $asset.browser_download_url -OutFile $f -TimeoutSec 420
            Step-Native -Name '2-1-fallback-git' -File $f -ArgLine '/VERYSILENT /NORESTART /NOCANCEL /SP-' -TimeoutSec 600 -Note "Git for Windows: $($asset.name)" | Out-Null
            Wait-ForInstallerIdle
        } catch { Add-Result -step '2-1-fallback-git' -ok $false -note "failed: $_" }
    }

    if (Test-Budget '2-1-fallback-python') {
        try {
            $f = Join-Path $stage 'python-amd64.exe'
            Invoke-WebRequest -UseBasicParsing -Uri 'https://www.python.org/ftp/python/3.13.7/python-3.13.7-amd64.exe' -OutFile $f -TimeoutSec 420
            Step-Native -Name '2-1-fallback-python' -File $f -ArgLine '/quiet InstallAllUsers=0 PrependPath=1 Include_test=0' -TimeoutSec 900 -Note 'python.org installer, per-user, PrependPath=1' | Out-Null
            Wait-ForInstallerIdle
        } catch { Add-Result -step '2-1-fallback-python' -ok $false -note "failed: $_" }
    }

    if (Test-Budget '2-1-fallback-node') {
        try {
            $idx = Invoke-RestMethod -Uri 'https://nodejs.org/dist/index.json' -TimeoutSec 60
            $v = ($idx | Where-Object { $_.lts -is [string] -and $_.version -like 'v22.*' } | Select-Object -First 1).version
            $f = Join-Path $stage "node-$v-x64.msi"
            Invoke-WebRequest -UseBasicParsing -Uri "https://nodejs.org/dist/$v/node-$v-x64.msi" -OutFile $f -TimeoutSec 420
            Step-Native -Name '2-1-fallback-node' -File "$env:SystemRoot\System32\msiexec.exe" -ArgLine "/i `"$f`" /qn /norestart" -TimeoutSec 420 -Note "Node.js LTS $v from nodejs.org" | Out-Null
        } catch { Add-Result -step '2-1-fallback-node' -ok $false -note "failed: $_" }
    }
}

# --- PATH, the way a new shell would see it -------------------------------------------------

$before = $env:Path
Update-PathFromRegistry
$changed = ($before -ne $env:Path)
Add-Result -step '2-1-path-refresh' -ok $true -note "PATH rebuilt from the Machine and User registry values the way a new shell would; it $(if ($changed) { 'changed' } else { 'did not change' }). The prompt says `"open a new PowerShell so PATH is fresh`", which an agent inside one session cannot do."

$pyExe = $null
foreach ($probe in @(@('git', 'git --version'), @('python', 'python --version'), @('node', 'node --version'))) {
    if (-not (Test-Budget "2-1-after-$($probe[0])")) { continue }
    $r = Invoke-InShell -Command $probe[1] -TimeoutSec 60 -Tag "after-$($probe[0])"
    $src = Resolve-Tool $probe[0]
    Add-Result -step "2-1-after-$($probe[0])" -ok (($r.exit -eq 0) -and ($src -ne $null)) -seconds $r.seconds -exit $r.exit `
        -note "after installing and refreshing PATH, `"$($probe[1])`" exits $($r.exit); resolves to: $(if ($src) { $src } else { 'nothing' })" `
        -tail (Get-Tail ("STDOUT>>" + $r.out + "<<STDOUT STDERR>>" + $r.err + "<<STDERR") 20 3000)
    if ($probe[0] -eq 'python' -and $r.exit -eq 0 -and $r.out -match 'Python 3') { $pyExe = $src }
}

if (-not $pyExe) {
    foreach ($cand in @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'),
        (Join-Path $env:ProgramFiles 'Python313\python.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Python313\python.exe')
    )) {
        if ($cand -and (Test-Path $cand)) { $pyExe = $cand; break }
    }
    if ($pyExe) {
        Add-Result -step '2-1-python-fallback' -ok $true -note "`"python`" did not answer as Python, so the harness located the interpreter itself at $pyExe. An agent following the prompt literally would be stuck here."
    }
}

# --- step 2: source ---------------------------------------------------------------------------

$repo = Join-Path $env:USERPROFILE 'system-sentinel'
$gitExe = Resolve-Tool 'git'
if ($gitExe -and (Test-Budget '2-2-clone')) {
    Step-Native -Name '2-2-clone' -File $gitExe -ArgLine 'clone https://github.com/SuperDuperDave/system-sentinel.git' `
        -WorkDir $env:USERPROFILE -TimeoutSec 300 -Note 'git clone into the user profile, as the prompt says' | Out-Null
} else {
    Add-Result -step '2-2-clone' -ok $false -note 'not attempted: no git on PATH'
}
$haveRepo = Test-Path (Join-Path $repo 'pyproject.toml')

# --- step 3: the python package ---------------------------------------------------------------

$venvPy = Join-Path $repo '.venv\Scripts\python.exe'
if ($haveRepo -and $pyExe -and (Test-Budget '2-3-venv')) {
    Step-Native -Name '2-3-venv' -File $pyExe -ArgLine '-m venv .venv' -WorkDir $repo -TimeoutSec 240 -Note 'python -m venv .venv' | Out-Null
    if (Test-Path $venvPy) {
        Step-Native -Name '2-3-pip-install' -File $venvPy -ArgLine '-m pip install -e .' -WorkDir $repo -TimeoutSec 600 `
            -Note '.venv\Scripts\python -m pip install -e .' | Out-Null
    } else {
        Add-Result -step '2-3-pip-install' -ok $false -note "no interpreter at $venvPy after venv creation"
    }
} else {
    Add-Result -step '2-3-venv' -ok $false -note "not attempted: repo present=$haveRepo, python located=$($pyExe -ne $null)"
}

# --- step 4: the dashboard ---------------------------------------------------------------------

# Get-Command npm finds npm.ps1 first on current Node, and a PowerShell script is not something
# Start-Process can launch; the shim an agent's `npm ci` ends up running is npm.cmd.
$npm = $null
foreach ($cand in @('npm.cmd', 'npm.exe', 'npm')) {
    $c = Get-Command $cand -ErrorAction SilentlyContinue |
         Where-Object { $_.Source -like '*.cmd' -or $_.Source -like '*.exe' } | Select-Object -First 1
    if ($c) { $npm = $c.Source; break }
}
if ($haveRepo -and $npm -and (Test-Budget '2-4-npm-ci')) {
    $dash = Join-Path $repo 'dashboard'
    $userNpmrc = Test-Path (Join-Path $env:USERPROFILE '.npmrc')
    Add-Result -step '2-4-npmrc' -ok $true -note "npm resolves to $npm; a user-level .npmrc exists: $userNpmrc (the prompt's `"--os=win32 --cpu=x64`" workaround is for that file)"
    $ci = Step-Native -Name '2-4-npm-ci' -File $npm -ArgLine 'ci' -WorkDir $dash -TimeoutSec 600 -Note 'npm ci in dashboard/'
    if ($ci.exit -eq 0) {
        Step-Native -Name '2-4-npm-build' -File $npm -ArgLine 'run build' -WorkDir $dash -TimeoutSec 600 -Note 'npm run build' | Out-Null
    }
    $idx = Join-Path $repo 'sentinel\static\index.html'
    Add-Result -step '2-4-static' -ok (Test-Path $idx) -note "sentinel\static\index.html exists: $(Test-Path $idx)"
} else {
    Add-Result -step '2-4-npm-ci' -ok $false -note "not attempted: repo present=$haveRepo, npm found=$($npm -ne $null)"
}

# --- step 5: the bridge -------------------------------------------------------------------------

$cli = Join-Path $repo '.venv\Scripts\system-sentinel.exe'
if ((Test-Path $cli) -and (Test-Budget '2-5-check')) {
    $r = Step-Native -Name '2-5-check' -File $cli -ArgLine 'check' -WorkDir $repo -TimeoutSec 180 -Note '.venv\Scripts\system-sentinel check'
    $outcome = 'unparsed'
    try { $outcome = ($r.out | ConvertFrom-Json).outcome } catch {}
    Add-Result -step '2-5-check-outcome' -ok ($outcome -eq 'ok') -note "the reading's outcome is: $outcome"
} else {
    Add-Result -step '2-5-check' -ok $false -note "not attempted: no CLI at $cli"
}

# --- step 6: the token ---------------------------------------------------------------------------

$token2 = $null
if ((Test-Path $cli) -and (Test-Budget '2-6-token')) {
    $r = Invoke-Native -File $cli -ArgLine 'token' -WorkDir $repo -TimeoutSec 120 -Tag 'token'
    if ($r.out -and $r.out.Trim()) { $token2 = $r.out.Trim(); Add-Secret $token2 }
    Add-Result -step '2-6-token' -ok ($token2 -ne $null -and $token2.Length -ge 16) -seconds $r.seconds -exit $r.exit `
        -note "a token was produced: $($token2 -ne $null); it is $(if ($token2) { $token2.Length } else { 0 }) characters. The value is never recorded."
} else {
    Add-Result -step '2-6-token' -ok $false -note "not attempted: no CLI at $cli"
}

# --- step 7: serve, the catalog, and the 401 ------------------------------------------------------

if ($token2 -and (Test-Budget '2-7-serve')) {
    $t0 = Get-Date
    try { Start-Process -FilePath $cli -ArgumentList 'serve' -WindowStyle Hidden -WorkingDirectory $repo } catch {}
    Start-Sleep -Seconds 5
    $names = ''
    $ok = $false
    try {
        $body = Invoke-RestMethod -Uri "$API/api/readings" -Headers @{ Authorization = "Bearer $token2" } -TimeoutSec 20
        $names = (@($body.readings | ForEach-Object { $_.name }) -join ', ')
        if (-not $names) { $names = (@($body | ForEach-Object { $_.name }) -join ', ') }
        $ok = ($names -match 'health' -and $names -match 'events' -and $names -match 'record')
    } catch { $names = "FAILED: $_" }
    Add-Result -step '2-7-serve' -ok $ok -seconds (((Get-Date) - $t0).TotalSeconds) -note "five seconds after Start-Process, /api/readings lists: $names"

    $code = $null
    $t0 = Get-Date
    try { Invoke-WebRequest -UseBasicParsing "$API/api/readings" -TimeoutSec 20 } catch { $code = $_.Exception.Response.StatusCode.value__ }
    Add-Result -step '2-7-401' -ok ($code -eq 401) -seconds (((Get-Date) - $t0).TotalSeconds) `
        -note "the prompt's 401 check, run exactly as written, printed: $code"
} else {
    Add-Result -step '2-7-serve' -ok $false -note 'not attempted: no token from step 6'
}

Add-Result -step '2-8-mcp' -ok $true -note 'not run: step 8 registers the MCP server with `claude mcp add`. Claude Code is not installed on this image and installing it is not part of the prompt, so there is no client to register with.'

# --- step 9: read the machine once -----------------------------------------------------------------

if ($token2 -and (Test-Budget '2-9-events')) {
    $t0 = Get-Date
    $note = ''
    $ok = $false
    try {
        $r = Invoke-Api -Path '/api/readings/events?count=5' -Token $token2 -TimeoutSec 60
        [IO.File]::WriteAllText((Join-Path $OUT 'part2-events.json'), $r.Content)
        $j = $r.Content | ConvertFrom-Json
        $count = 0
        $count = $j.count
        $note = "outcome=$($j.outcome), records=$count"
        $ok = ($j.outcome -eq 'ok' -or $j.outcome -eq 'empty')
    } catch { $note = "FAILED: $_" }
    Add-Result -step '2-9-events' -ok $ok -seconds (((Get-Date) - $t0).TotalSeconds) -note $note
}

try { Get-Process -Name 'system-sentinel' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue } catch {}
try { Get-Process -Name 'pythonw', 'python' -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$repo*" } | Stop-Process -Force -ErrorAction SilentlyContinue } catch {}
Add-Result -step '2-stop' -ok $true -note 'the server started in step 7 was stopped'

}  # end -not SkipSource

# --- the mark of the web ------------------------------------------------------------------
# Deliberately last. ShellExecute on a downloaded, unsigned binary opens a modal warning and
# does not return until someone answers it, so the call is made by a child process this script
# abandons; a run that ends here has already recorded everything else.

function Invoke-MotwObservation {
    if (-not (Test-Budget 'motw')) { return }
    $t0    = Get-Date
    $dlDir = Join-Path $env:USERPROFILE 'Downloads'
    $dl    = Join-Path $dlDir 'SystemSentinel.exe'
    $note  = ''
    $ok    = $true

    try {
        New-Item -ItemType Directory -Force -Path $dlDir | Out-Null
        $source = if (Test-Path (Join-Path $IN 'SystemSentinel.exe')) { Join-Path $IN 'SystemSentinel.exe' } else { Join-Path $env:TEMP 'SystemSentinel.downloaded.exe' }
        Copy-Item $source $dl -Force
        Set-Content -Path $dl -Stream 'Zone.Identifier' -Value "[ZoneTransfer]", "ZoneId=3"
        $zone = ((Get-Content -Path $dl -Stream 'Zone.Identifier' -Raw) -replace "`r`n", ' / ').Trim()
        $note += "Zone.Identifier reads back as: $zone; "
    } catch { $ok = $false; $note += "the mark of the web could not be written: $_; " }

    try {
        $ss1 = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer' -Name SmartScreenEnabled -ErrorAction SilentlyContinue).SmartScreenEnabled
        $ss2 = (Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\System' -Name EnableSmartScreen -ErrorAction SilentlyContinue).EnableSmartScreen
        $ss3 = (Get-ItemProperty 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\AppHost' -Name EnableWebContentEvaluation -ErrorAction SilentlyContinue).EnableWebContentEvaluation
        $note += "SmartScreen config on this image: Explorer\SmartScreenEnabled=$(if ($ss1) { $ss1 } else { '(unset)' }), policy EnableSmartScreen=$(if ($ss2) { $ss2 } else { '(unset)' }), AppHost EnableWebContentEvaluation=$(if ($ss3 -ne $null) { $ss3 } else { '(unset)' }); "
    } catch { }

    $before = @(Get-Process -Name SystemSentinel -ErrorAction SilentlyContinue).Count

    $shim = Join-Path $env:TEMP 'open-marked.ps1'
    Set-Content -Path $shim -Value "Start-Process -FilePath '$dl'" -Encoding ASCII
    $child = $null
    try {
        $child = Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
                 -ArgumentList "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$shim`"" -PassThru -WindowStyle Hidden
    } catch { $ok = $false; $note += "the detached launch failed: $_; " }

    Start-Sleep -Seconds 8
    $titles  = Get-TopLevelWindows
    $shot    = Save-Screenshot '2-smartscreen.png'
    $smart   = @(Get-Process -Name smartscreen -ErrorAction SilentlyContinue)
    $after   = @(Get-Process -Name SystemSentinel -ErrorAction SilentlyContinue).Count
    $held    = ($child -ne $null -and -not $child.HasExited)

    $dialogs = @($titles -split "`n" | Where-Object { $_ -like '*Security Warning*' -or $_ -like '*protected your PC*' -or $_ -like '*SmartScreen*' })
    $note += "smartscreen processes: $($smart.Count)"
    if ($smart.Count -gt 0) { $note += " (titles: $((($smart | ForEach-Object { $_.MainWindowTitle }) -join ' | ')))" }
    $note += "; the launch call is still waiting for an answer: $held"
    $note += "; SystemSentinel processes before the launch: $before, eight seconds after: $after"
    if ($dialogs.Count -gt 0) { $note += "; dialog on screen: $($dialogs -join ' | ')" }
    else { $note += '; no security dialog was found among the visible windows' }
    $note += "; $shot"

    Add-Result -step 'motw' -ok $ok -seconds (((Get-Date) - $t0).TotalSeconds) -note $note -tail (Get-Tail $titles 24)

    $closed = Close-WindowByTitle '*Security Warning*'
    $closed = ($closed, (Close-WindowByTitle '*protected your PC*') | Where-Object { $_ }) -join ' | '
    foreach ($p in $smart) { try { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } catch { } }
    if ($child) { try { Stop-Process -Id $child.Id -Force -ErrorAction SilentlyContinue } catch { } }
    try { Get-Process -Name SystemSentinel -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue } catch { }
    Add-Result -step 'motw-cleanup' -ok $true -note "dialogs closed: $(if ($closed) { $closed } else { 'none' })"
}

Invoke-MotwObservation

Add-Result -step 'screenshot-end' -ok $true -note (Save-Screenshot '3-end.png') -tail (Get-Tail (Get-TopLevelWindows) 24)

} catch {
    try { Add-Result -step 'harness-exception' -ok $false -note "the harness itself threw: $_" } catch {}
} finally {
    $script:Finished = $true
    Save-Results
    try { [IO.File]::WriteAllText((Join-Path $OUT 'done'), ((Get-Date).ToString('o'))) } catch {}
    try { Stop-Transcript | Out-Null } catch {}
    Start-Sleep -Seconds 20
    try { & "$env:SystemRoot\System32\shutdown.exe" /a 2>&1 | Out-Null } catch {}
    try { & "$env:SystemRoot\System32\shutdown.exe" /s /t 0 2>&1 | Out-Null } catch {}
}
