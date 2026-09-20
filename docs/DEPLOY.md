# Installing System Sentinel with your agent

Copy the block below and paste it to the agent running on the Windows machine you want to read (Claude Code, Codex or any agent with a shell). It installs the tool, proves the bridge to Windows works, starts the server and registers it as an MCP server, so the agent can read the machine through the tool instead of writing its own scripts. It is safe to run twice: every step checks before it acts.

What it needs: Windows 10 or 11, Git, Python 3.11 or newer and Node.js 22 or newer. The block installs the last three with `winget` if they are missing and says so. What it does not do: it sends nothing off the machine, opens no port beyond localhost, and installs no service; running at logon and reaching the machine from a phone are the two optional steps at the end.

## The prompt

```text
Install System Sentinel on this Windows machine and prove it works. Work in PowerShell. Do each step, check its result, and stop and tell me if a check fails. Do not skip a check because the step looked successful.

1. Prerequisites. Run `git --version`, `python --version` (3.11 or newer) and `node --version` (22 or newer). For any that is missing or too old, install it with winget (`winget install Git.Git`, `winget install Python.Python.3.13`, `winget install OpenJS.NodeJS.LTS`), open a new PowerShell so PATH is fresh, and tell me what you installed.

2. Source. If a folder named system-sentinel exists in the current directory, `cd system-sentinel` and run `git pull`. Otherwise run `git clone https://github.com/SuperDuperDave/system-sentinel.git` and `cd system-sentinel`.

3. Python package. If `.venv` does not exist, run `python -m venv .venv`. Then run `.venv\Scripts\python -m pip install -e .` and confirm it ends without an error.

4. Dashboard. Run `cd dashboard`, `npm ci`, `npm run build`, `cd ..`. Confirm that `sentinel\static\index.html` now exists.

5. The bridge. Run `.venv\Scripts\system-sentinel check`. It prints a JSON reading; confirm its "outcome" is "ok" and it reports a PowerShell version. If the outcome is anything else, stop and show me the reading.

6. The token. Run `.venv\Scripts\system-sentinel token` and keep the value; call it TOKEN. Never paste it anywhere except the two places below.

7. Serve. Start the server in the background with `Start-Process -FilePath ".venv\Scripts\system-sentinel.exe" -ArgumentList "serve" -WindowStyle Hidden`, wait five seconds, then run `Invoke-RestMethod -Uri http://127.0.0.1:8000/api/readings -Headers @{ Authorization = "Bearer TOKEN" }` and confirm it lists readings including "health", "events" and "record". Then confirm `Invoke-WebRequest http://127.0.0.1:8000/api/readings -SkipHttpErrorCheck` without the header answers 401.

8. Register the MCP server so agents on this machine can use it. If Claude Code is installed, run `claude mcp add --transport http system-sentinel http://127.0.0.1:8000/mcp --header "Authorization: Bearer TOKEN"`. For any other MCP client, the address is http://127.0.0.1:8000/mcp with that Authorization header.

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

Recorded when the prompt is run end to end through an agent on a clean machine; until then this section is empty and the prompt is not published.
