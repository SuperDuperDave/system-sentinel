# The clean-machine test

Both ways of installing System Sentinel are written for a machine that has never
seen it. The machine this was built on is not that machine: it has Git, Python,
Node and a built executable already, so nothing here can tell you what a person
or an agent actually meets the first time. This harness makes a machine that has
never seen it, runs both paths on it unattended, and brings back what happened.

It uses Windows Sandbox: a disposable Windows 11 that starts from the host's own
image, has no Store and no developer tooling, and is discarded when it closes.

## Running it

From WSL, against the built executable in `dist/`:

```
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(wslpath -w run.ps1)"
```

`run.ps1` stages `dist\SystemSentinel.exe` and `inside.ps1` into `%TEMP%\sentinel-sandbox\in`,
clears `%TEMP%\sentinel-sandbox\out`, writes `sentinel.wsb` next to them, and starts
the sandbox. The sandbox maps `in` read-only at `C:\in` and `out` writable at `C:\out`,
and runs `inside.ps1` at logon. Watch `out\result.json`; the run is over when
`out\done` appears, and the sandbox closes itself a few seconds later.

- `-SkipSource` runs only the person's path, which takes a couple of minutes
  instead of the better part of an hour.
- `-ExePath <path>` tests an executable from somewhere other than `dist\`.
- `-Release <url>` tests a published executable instead of a staged one: the
  sandbox downloads it from that URL, checks its SHA-256 against the
  `SHA256SUMS.txt` beside it, unblocks it and only then starts it, which is the
  short prompt's own sequence. `-SkipSource -Release <url>` is the release check.

Nobody needs to be at the keyboard. Nothing inside waits for input: every command
is given a deadline and killed if it overruns, the whole run has a budget, and a
shutdown is scheduled the moment the script starts so the sandbox cannot sit on
the desktop if the script itself wedges.

## What it observes

**The person's path.** Copy the one file onto the clean machine, start it, and
measure the thing that decides whether the first impression is good: how many
seconds pass between the double-click and the first `200` from the API. Then the
`health` and `events` readings through the token the tool made for itself, the
launcher's own log, the windows that are open, and a screenshot of whatever the
browser is showing. Then the mark of the web: the same file given the
`Zone.Identifier` stream a download would carry, launched, photographed, and
whatever SmartScreen says recorded in Windows's own words along with how that
image has SmartScreen configured.

**The agent's path.** `docs/DEPLOY.md`'s prompt, step by step, each step a result
with its exit code and the last lines of what it printed. Before anything is
installed it records what `git --version`, `python --version` and `node --version`
print and exit with on a clean image, and what PowerShell resolves each name to.
Then whether winget exists at all, what bootstrapping it takes, whether the
prompt's `winget install` lines run unattended exactly as written or need the
agreement flags the prompt does not mention, and whether PATH has to be rebuilt
from the registry before the new tools answer. Then clone, venv, editable
install, `npm ci`, `npm run build`, `check`, the token, serve, the catalog, the
401, and one reading of the machine.

Everything lands in the results folder: `result.json` rewritten after every step,
`transcript.log`, the redirected output of every command under `logs\`, the saved
reading bodies, and the screenshots.

## What it cannot observe

- **A judgment is not a step.** The prompt's step 0 asks the agent to read the
  source and say what it found; step 8 registers the tool with an MCP client. A
  harness can run neither: one has no exit code, the other has no client on this
  image. Both are recorded as not run, with the reason.
- **Some dialogs have no name.** The window list is read from user32, which is
  how a dialog that belongs to no visible process is still caught. But a modern
  XAML dialog can carry no window text at all: it shows up in the screenshot and
  nowhere else, and the harness cannot close it. Read the screenshots; do not
  trust the window list to be the whole story.
- **The sandbox is not every machine.** It starts from this host's image and
  patch level, it has no Store, and its SmartScreen and Defender configuration is
  its own. A dialog that does not appear here may still appear on a real desktop;
  the run records the configuration alongside the observation so the reading can
  be made honestly.
- **It is not a fresh Windows install.** A machine bought last week has winget, a
  Store, an OEM's software and a person's own PATH. The sandbox is cleaner than
  that, which makes it a good floor and a bad average.
- **One run is one run.** Every number it reports is what this machine, this
  image and this network did once.
- **A download is not a browser download.** With `-Release` the file arrives
  through PowerShell, which does not mark it as from the internet the way a
  browser does; the mark-of-the-web step imitates that mark on a copy so what
  Windows says before an unsigned download runs is still observed.

## What it leaves behind

On the host: `%TEMP%\sentinel-sandbox`, holding the staged copies, the `.wsb` and
the results. Nothing is installed, no service is registered, no port is opened,
and no file outside that folder is written. Delete the folder and the test is
gone. Everything else happened inside a sandbox that no longer exists.

The results contain no token: the harness redacts every secret it has seen from
every line it records, and it never writes a token's value even when a step's job
is to prove one was made.
