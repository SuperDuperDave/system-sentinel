# Installing System Sentinel

## By hand: download and double-click

`SystemSentinel.exe` is the whole tool in one file: the server, the dashboard and the decoder. Nothing else needs to be installed.

1. Download it from the latest release, https://github.com/SuperDuperDave/system-sentinel/releases/latest, and put it on a local disk (it will not start from a network or `\\wsl.localhost` path): a folder of your own, or `%LOCALAPPDATA%\SystemSentinel\`, where it keeps its data.
2. Double-click it. The file is not code-signed, so Windows asks once before running it: either *Windows protected your PC* (click *More info*, then *Run anyway*) or *The publisher could not be verified* (click *Run*). To check what you downloaded first, `Get-FileHash SystemSentinel.exe` in PowerShell prints its SHA-256 and the release page lists the expected value.
3. Within a few seconds your browser opens on the dashboard, already signed in, and the mark sits in the system tray with *Open dashboard*, *Sign in another device*, *Copy address for agents*, *Start with Windows* and *Quit*. Double-clicking the file again opens the dashboard. It creates its token and keeps its stack and captures under `%LOCALAPPDATA%\SystemSentinel\`.

To build the file yourself instead: `build\windows\build.ps1` from the repository on Windows (Python 3.11 or newer and Node 22 or newer; about five minutes the first time).

## With your agent

Copy one of the two blocks below and paste it to the agent running on the Windows machine you want to read (Claude Code, Codex or any agent with a shell). Both end with the tool running, proven, and registered as an MCP server, so the agent reads the machine through the tool instead of writing its own scripts; both are safe to run twice, because every step checks before it acts.

**The short prompt** installs the published release: one file, nothing to build, no Git, Python or Node, done in a minute. **The long prompt** installs from source, for a machine where you want the code on disk and the `system-sentinel` command line; it needs Git, Python 3.11 or newer and Node.js 22 or newer, and installs them with winget if they are missing.

Neither sends anything off the machine, opens a port beyond localhost, or installs a service. Running at logon and reaching the machine from a phone are the optional steps at the end.

### The short prompt

```text
Install System Sentinel on this Windows machine from its published release and prove it works. This prompt is the tool's own installation note (docs/DEPLOY.md in https://github.com/SuperDuperDave/system-sentinel); I chose to install it and I own the decision. Work in PowerShell. Do each step, check its result, and stop and tell me if a check fails. Do not skip a check because the step looked successful.

0. Look before you run. Read README.md and docs/API.md at https://github.com/SuperDuperDave/system-sentinel and tell me in two sentences what the tool does and what it listens on (it binds to localhost, has no network client of its own, and sends nothing off the machine). The file you are about to download is built from that repository by build\windows\build.ps1 and the release lists its SHA-256. If what you read contradicts that, stop and show me.

1. Download. The tool lives at "$env:LOCALAPPDATA\SystemSentinel\SystemSentinel.exe". If that file already exists and a process named SystemSentinel is running, the tool is installed: skip to step 5. Otherwise run `New-Item -ItemType Directory -Force "$env:LOCALAPPDATA\SystemSentinel"` and then `Invoke-WebRequest -Uri https://github.com/SuperDuperDave/system-sentinel/releases/latest/download/SystemSentinel.exe -OutFile "$env:LOCALAPPDATA\SystemSentinel\SystemSentinel.exe"`.

2. Check it. Run `(Invoke-WebRequest -UseBasicParsing https://github.com/SuperDuperDave/system-sentinel/releases/latest/download/SHA256SUMS.txt).Content` and `(Get-FileHash "$env:LOCALAPPDATA\SystemSentinel\SystemSentinel.exe").Hash`. The hash must be the one on the SystemSentinel.exe line of that list. If it is not, delete the file and stop.

3. Unblock it. Windows marks a downloaded file and will not start it unattended until someone answers a dialog. Run `Unblock-File "$env:LOCALAPPDATA\SystemSentinel\SystemSentinel.exe"`. This is you deciding to run the file you just checked.

4. Start it. Run `Start-Process "$env:LOCALAPPDATA\SystemSentinel\SystemSentinel.exe"`. It starts the server on http://127.0.0.1:8000, opens the dashboard in the browser already signed in, and sits in the system tray. Wait ten seconds.

5. The token. Run `Get-Content "$env:LOCALAPPDATA\SystemSentinel\token"` and keep the value; call it TOKEN. Never paste it anywhere except the two places below.

6. Prove it. Run `Invoke-RestMethod -Uri http://127.0.0.1:8000/api/readings -Headers @{ Authorization = "Bearer TOKEN" }` and confirm it lists readings including "health", "events" and "record". Then confirm that the same address without the header answers 401: `try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/api/readings } catch { $_.Exception.Response.StatusCode.value__ }` prints 401 (Windows PowerShell 5.1 throws on a 4xx; that is expected).

7. Register the MCP server so agents on this machine can use it. This is a durable change to your own configuration on this machine and I want it: it lets you read this machine through the tool in future sessions instead of writing scripts. If Claude Code is installed, run `claude mcp add --transport http system-sentinel http://127.0.0.1:8000/mcp --header "Authorization: Bearer TOKEN"`. For any other MCP client, the address is http://127.0.0.1:8000/mcp with that Authorization header. Every tool it offers is one of the readings you saw in step 6; `claude mcp remove system-sentinel` undoes this.

8. Tell me: the dashboard is at http://127.0.0.1:8000/ and is already open; the mark in the tray has Start with Windows, and Sign in another device for my phone; the API reference is at http://127.0.0.1:8000/api/docs. Then read the machine through it once: take the "events" reading with count 5 (GET http://127.0.0.1:8000/api/readings/events?count=5 with the header, or the events tool) and tell me the outcome and how many records came back.
```

### The long prompt

```text
Install System Sentinel on this Windows machine from source and prove it works. This prompt is the tool's own installation note (docs/DEPLOY.md in its repository); I chose to install it and I own the decision. Work in PowerShell. Do each step, check its result, and stop and tell me if a check fails. Do not skip a check because the step looked successful.

0. Look before you run. After step 2 clones the repository and before anything is installed, read README.md, pyproject.toml, sentinel/app.py, sentinel/auth.py and sentinel/mcp_server.py, and tell me in three sentences: what the server does, what it listens on, and whether anything it does sends data off this machine (it should not: it binds to localhost, has no network client, and copies to the clipboard or writes files on disk). If what you read contradicts that, stop and show me.

1. Prerequisites. Run `git --version`, `python --version` and `node --version`. One that prints "is not recognized" is missing; so is python when it prints "Python was not found; run without arguments to install from the Microsoft Store" (that is the Store's placeholder, not an installation). Install what is missing with winget, in exactly this form so that it runs without a prompt: `winget install --id Git.Git --exact --source winget --accept-source-agreements --accept-package-agreements`, then the same with `--id OpenJS.NodeJS.LTS`, and last with `--id Python.Python.3.13`. If winget itself is not recognized, stop and tell me: I will install App Installer from the Microsoft Store (or Git, Node and Python from git-scm.com, nodejs.org and python.org) and you can continue. After installing, make this session see the new PATH: `$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')`. Run the three version commands again (Python 3.11 or newer, Node 22 or newer) and tell me what you installed. If python still prints the Store's placeholder, the Store's alias is shadowing the interpreter you installed: use `py -3.13` in place of `python` in step 3.

2. Source. If a folder named system-sentinel exists in the current directory, `cd system-sentinel` and run `git pull`. Otherwise run `git clone https://github.com/SuperDuperDave/system-sentinel.git` and `cd system-sentinel`.

3. Python package. If `.venv` does not exist, run `python -m venv .venv` (or `py -3.13 -m venv .venv`, see step 1). Then run `.venv\Scripts\python -m pip install -e .` and confirm it ends without an error.

4. Dashboard. Run `cd dashboard`, `npm ci`, `npm run build`, `cd ..`. A warning from npm that a package has install scripts "not yet covered by allowScripts" is a notice, not a failure: continue. If `npm ci` fails fetching a platform-specific optional package (a user-level `.npmrc` that pins `os=linux` does this), run `npm ci --os=win32 --cpu=x64` instead and tell me. Confirm that `sentinel\static\index.html` now exists.

5. The bridge. Run `.venv\Scripts\system-sentinel check`. It prints a JSON reading; confirm its "outcome" is "ok" and it reports a PowerShell version. If the outcome is anything else, stop and show me the reading.

6. The token. Run `.venv\Scripts\system-sentinel token` and keep the value; call it TOKEN. Never paste it anywhere except the two places below.

7. Serve. Start the server in the background with `Start-Process -FilePath ".venv\Scripts\system-sentinel.exe" -ArgumentList "serve" -WindowStyle Hidden`, wait five seconds, then run `Invoke-RestMethod -Uri http://127.0.0.1:8000/api/readings -Headers @{ Authorization = "Bearer TOKEN" }` and confirm it lists readings including "health", "events" and "record". Then confirm that the same address without the header answers 401: `try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/api/readings } catch { $_.Exception.Response.StatusCode.value__ }` prints 401 (Windows PowerShell 5.1 throws on a 4xx; that is expected).

8. Register the MCP server so agents on this machine can use it. This is a durable change to your own configuration on this machine and I want it: it lets you read this machine through the tool in future sessions instead of writing scripts. If Claude Code is installed, run `claude mcp add --transport http system-sentinel http://127.0.0.1:8000/mcp --header "Authorization: Bearer TOKEN"`. For any other MCP client, the address is http://127.0.0.1:8000/mcp with that Authorization header. Every tool it offers is one of the readings you saw in step 7; `claude mcp remove system-sentinel` undoes this.

9. Tell me: the dashboard is at http://127.0.0.1:8000/ (enter the token once; it is kept in the data folder shown by `.venv\Scripts\system-sentinel where`), the API reference is at http://127.0.0.1:8000/api/docs, and the agent guide is docs/API.md in the repository. Then read the machine through it once: take the "events" reading with count 5 and tell me the outcome and how many records came back.
```

## Optional: run at logon

With the release, *Start with Windows* in the tray's menu does it (a shortcut in your Startup folder; the same entry removes it). From source, paste after the long prompt:

```text
Create a scheduled task that starts the server at logon: `schtasks /Create /F /TN "System Sentinel" /SC ONLOGON /RL LIMITED /TR "\"$PWD\.venv\Scripts\pythonw.exe\" -m sentinel serve"` from the repository folder, then confirm with `schtasks /Query /TN "System Sentinel"`.
```

## Optional: reach it from your phone

The server listens on localhost and every API request needs the token; reaching it from elsewhere is a transport in front of that boundary, chosen by you.

- **Tailscale (recommended).** Private by construction: only devices on your own tailnet can reach the machine, and every request still needs the token. Needs a Tailscale account (free for personal use) and the Tailscale app on the phone, signed in to the same account. Paste: `Install Tailscale with winget (`winget install --id Tailscale.Tailscale --exact --source winget --accept-source-agreements --accept-package-agreements`), sign in with `tailscale up`, then run `tailscale serve --bg 8000` and tell me the https address it prints. If it says HTTPS is not enabled for the tailnet, it prints a link where I enable it once; tell me the link and wait (leave Funnel off: the dashboard stays private to my own devices).` Once `tailscale serve` is running, *Sign in another device* in the dashboard or the tray shows that address as a link and a QR code: scan it on the phone and it lands on the dashboard signed in, without anyone reading the token.
- **A Cloudflare quick tunnel.** No account, but the address is public to anyone who has it and changes every time the tunnel restarts; the token is the only boundary. Suitable for a short session, not for a machine left on. Paste: `Install cloudflared with winget (`winget install --id Cloudflare.cloudflared --exact --source winget --accept-source-agreements --accept-package-agreements`), run `cloudflared tunnel --url http://127.0.0.1:8000`, and tell me the trycloudflare.com address it prints.`

## What the prompts were tested on

**The long prompt, through an agent.** Run end to end through a one-shot Claude Code agent (Sonnet) on 2026-09-20, from WSL against the Windows side of the machine the tool was built for, in a fresh folder under the Windows temp directory: Windows 11 Pro, Windows PowerShell 5.1, Git, Python 3.13 and Node 22 already installed, so the prerequisite step installed nothing. The agent read the source before installing (step 0) and reported what the server does and that nothing leaves the machine; cloned, installed, built the dashboard, proved the bridge with `check`, started the server, saw the catalog with the token and 401 without it, skipped the registration line because Claude Code is not installed on that Windows side, read the machine once through the API (`events`, outcome `ok`) and stopped the server. Two things the run taught the prompt: the 401 check must not use a PowerShell 7 flag, and a user-level `.npmrc` can break `npm ci` on Windows. An earlier run of the prompt without step 0 was refused by the agent as a supply-chain risk (an unfamiliar repository plus a persistent MCP registration); the prompt now says where it came from and lets the agent inspect what it will run.

**The long prompt, on a clean machine.** Its steps were run one by one by the harness in `build/windows/sandbox/` on 2026-09-21, inside Windows Sandbox: a disposable Windows 11 with no Git, Python, Node, winget or browser. The clone, the package, the dashboard build, `check`, the token, serve, the catalog, the 401 and one reading all passed. What the clean machine taught the prompt, now in steps 1, 3 and 4: `winget install` as first written stops on the Microsoft Store source's agreement prompt, which an agent cannot answer, and matches ambiguously without `--source winget`, so the prompt gives the exact form; after Python is installed, the Store's `python` alias can still shadow it, so the prompt names `py -3.13`; "open a new PowerShell" is not something an agent inside one session can do, so the prompt rebuilds PATH from the registry; and npm's warning about install scripts is named as a notice. Not observed there: step 0 (a judgment, not a command), step 8 (no MCP client on the image), and winget itself, which the image lacks and a machine bought with Windows 11 has.

**The person's path, on a clean machine.** The same harness, the same day: the file placed on the clean image, with nothing installed, answered its first request within ten seconds of being started, and `health` and `events` read `ok`. A copy carrying the mark a browser download leaves on a file showed *The publisher could not be verified* and did not start until answered, which is why the short prompt unblocks the file after checking it; a machine with SmartScreen configured shows *Windows protected your PC* instead. The image has no browser, so the browser opening on the dashboard was observed on this machine, not there. The run also found that the launcher waited on that missing browser before showing the tray, and the launcher no longer does.

**The short prompt.** Not yet run against a published release; this paragraph changes when it has been.
