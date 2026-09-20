# Installing System Sentinel

## By hand: double-click

`SystemSentinel.exe` is the whole tool in one file: the server, the dashboard and the decoder. Put it on a local disk (it will not start from a network or `\\wsl.localhost` path), double-click it, and within a few seconds your browser opens on the dashboard already signed in; the mark sits in the system tray with *Open dashboard*, *Copy address for agents*, *Start with Windows* and *Quit*. Double-clicking again opens the dashboard. It creates its token and keeps its stack and captures under `%LOCALAPPDATA%\SystemSentinel\`. Build it from the repository with `build\windows\build.ps1` (Windows Python 3.11 or newer and Node 22 or newer; about five minutes the first time), or use a release when one is published.

## With your agent

Copy the block below and paste it to the agent running on the Windows machine you want to read (Claude Code, Codex or any agent with a shell). It installs the tool from source, proves the bridge to Windows works, starts the server and registers it as an MCP server, so the agent can read the machine through the tool instead of writing its own scripts. It is safe to run twice: every step checks before it acts.

What it needs: Windows 10 or 11, Git, Python 3.11 or newer and Node.js 22 or newer. The block installs the last three with `winget` if they are missing and says so. What it does not do: it sends nothing off the machine, opens no port beyond localhost, and installs no service; running at logon and reaching the machine from a phone are the two optional steps at the end.

## The prompt

```text
Install System Sentinel on this Windows machine and prove it works. This prompt is the tool's own installation note (docs/DEPLOY.md in its repository); I chose to install it and I own the decision. Work in PowerShell. Do each step, check its result, and stop and tell me if a check fails. Do not skip a check because the step looked successful.

0. Look before you run. After step 2 clones the repository and before anything is installed, read README.md, pyproject.toml, sentinel/app.py, sentinel/auth.py and sentinel/mcp_server.py, and tell me in three sentences: what the server does, what it listens on, and whether anything it does sends data off this machine (it should not: it binds to localhost, has no network client, and copies to the clipboard or writes files on disk). If what you read contradicts that, stop and show me.

1. Prerequisites. Run `git --version`, `python --version` (3.11 or newer) and `node --version` (22 or newer). For any that is missing or too old, install it with winget (`winget install Git.Git`, `winget install Python.Python.3.13`, `winget install OpenJS.NodeJS.LTS`), open a new PowerShell so PATH is fresh, and tell me what you installed.

2. Source. If a folder named system-sentinel exists in the current directory, `cd system-sentinel` and run `git pull`. Otherwise run `git clone https://github.com/SuperDuperDave/system-sentinel.git` and `cd system-sentinel`.

3. Python package. If `.venv` does not exist, run `python -m venv .venv`. Then run `.venv\Scripts\python -m pip install -e .` and confirm it ends without an error.

4. Dashboard. Run `cd dashboard`, `npm ci`, `npm run build`, `cd ..`. If `npm ci` fails fetching a platform-specific optional package (a user-level `.npmrc` that pins `os=linux` does this), run `npm ci --os=win32 --cpu=x64` instead and tell me. Confirm that `sentinel\static\index.html` now exists.

5. The bridge. Run `.venv\Scripts\system-sentinel check`. It prints a JSON reading; confirm its "outcome" is "ok" and it reports a PowerShell version. If the outcome is anything else, stop and show me the reading.

6. The token. Run `.venv\Scripts\system-sentinel token` and keep the value; call it TOKEN. Never paste it anywhere except the two places below.

7. Serve. Start the server in the background with `Start-Process -FilePath ".venv\Scripts\system-sentinel.exe" -ArgumentList "serve" -WindowStyle Hidden`, wait five seconds, then run `Invoke-RestMethod -Uri http://127.0.0.1:8000/api/readings -Headers @{ Authorization = "Bearer TOKEN" }` and confirm it lists readings including "health", "events" and "record". Then confirm that the same address without the header answers 401: `try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/api/readings } catch { $_.Exception.Response.StatusCode.value__ }` prints 401 (Windows PowerShell 5.1 throws on a 4xx; that is expected).

8. Register the MCP server so agents on this machine can use it. This is a durable change to your own configuration on this machine and I want it: it lets you read this machine through the tool in future sessions instead of writing scripts. If Claude Code is installed, run `claude mcp add --transport http system-sentinel http://127.0.0.1:8000/mcp --header "Authorization: Bearer TOKEN"`. For any other MCP client, the address is http://127.0.0.1:8000/mcp with that Authorization header. Every tool it offers is one of the readings you saw in step 7; `claude mcp remove system-sentinel` undoes this.

9. Tell me: the dashboard is at http://127.0.0.1:8000/ (enter the token once; it is kept in the data folder shown by `.venv\Scripts\system-sentinel where`), the API reference is at http://127.0.0.1:8000/api/docs, and the agent guide is docs/API.md in the repository. Then read the machine through it once: take the "events" reading with count 5 and tell me the outcome and how many records came back.
```

## Optional: run at logon

Paste after the block above if you want the server up whenever you are logged in:

```text
Create a scheduled task that starts the server at logon: `schtasks /Create /F /TN "System Sentinel" /SC ONLOGON /RL LIMITED /TR "\"$PWD\.venv\Scripts\pythonw.exe\" -m sentinel serve"` from the repository folder, then confirm with `schtasks /Query /TN "System Sentinel"`.
```

## Optional: reach it from your phone

The server listens on localhost and every API request needs the token; reaching it from elsewhere is a transport in front of that boundary, chosen by you.

- **Tailscale (recommended).** Private by construction: only devices on your own tailnet can reach the machine, and every request still needs the token. Needs a Tailscale account. Paste: `Install Tailscale with winget (`winget install tailscale.tailscale`), sign in with `tailscale up`, then run `tailscale serve --bg 8000` and tell me the https address it prints. On my phone, on the same tailnet, that address opens the dashboard.`
- **A Cloudflare quick tunnel.** No account, but the address is public to anyone who has it and changes every time the tunnel restarts; the token is the only boundary. Suitable for a short session, not for a machine left on. Paste: `Install cloudflared with winget (`winget install Cloudflare.cloudflared`), run `cloudflared tunnel --url http://127.0.0.1:8000`, and tell me the trycloudflare.com address it prints.`

## What the prompt was tested on

Run end to end through a one-shot Claude Code agent (Sonnet) on 2026-09-20, from WSL against the Windows side of the machine the tool was built for, in a fresh folder under the Windows temp directory: Windows 11 Pro, Windows PowerShell 5.1, Git, Python 3.13 and Node 22 already installed, so the prerequisite step installed nothing. The agent read the source before installing (step 0) and reported what the server does and that nothing leaves the machine; cloned, installed, built the dashboard, proved the bridge with `check`, started the server, saw the catalog with the token and 401 without it, skipped the registration line because Claude Code is not installed on that Windows side, read the machine once through the API (`events`, outcome `ok`) and stopped the server. Two things the run taught the prompt: the 401 check must not use a PowerShell 7 flag, and a user-level `.npmrc` can break `npm ci` on Windows; both are in the steps above now.

An earlier run of the prompt without step 0 was refused by the agent as a supply-chain risk (an unfamiliar repository plus a persistent MCP registration); the prompt now says where it came from and lets the agent inspect what it will run. **This was not a clean machine.** A run on one (a fresh Windows install or Windows Sandbox) has not been made; until it has, the page should say the prompt was tested through an agent on a machine that already had the prerequisites.
