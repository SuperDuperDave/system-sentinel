<!--
The release notes, as a template. .github/workflows/release.yml renders it on a tag: it strips
this comment and replaces {{VERSION}} with the version the source states, {{TAG}} with the tag
being released, and {{SHA256}} with the checksum of the executable it just built. Nothing else is
substituted, so anything a particular release needs to say is edited here before the tag is
pushed, and the draft release is editable afterwards.

"Tested on" below says only what the workflow itself establishes on every commit. A clean-machine
run is a claim about one release and nobody can make it true from here: add the sentence when the
run has been made, and leave it out when it has not.
-->
A stethoscope for a Windows computer. It gathers the record the machine keeps of itself, shows it to a person in one place, and hands it to a local agent as tools. One boundary, three clients: the dashboard on the desk, a phone on your private network, and the agent beside it.

## New in {{VERSION}}

**The machine's record is easier to follow.** Crashes keeps the selected stop and its evidence together. Record widens as you ask for older rows and keeps your place. Changes gathers Windows Update, device configuration and installer records before a chosen moment. Signals now counts composed unplanned stops once, with each source's reach visible.

**Hardware errors have both logs.** Hardware errors reads the System WHEA-Logger and the separate Kernel-WHEA channel. Safe CPER header facts show severity and whether Windows reported an earlier-session error after a restart. Each source has its own outcome and retention reach. The screen makes the exact returned fields and CPER bytes available on demand; default redacted answers withhold those bytes. The record is evidence to investigate, not a cause verdict.

**See what is using the machine.** Performance keeps local numeric history across restarts and shows fresh process pressure beside the exact returned rows. Diagnostics maps returned memory modules and PCIe groups. Memory and Power now say when an independent query failed, rather than turning that gap into zero modules, no wake devices or mains power.

**Update from 1.1.0 in the app.** Choose *Check for updates…* from the tray's version menu. From a source install, run `system-sentinel update`. The tool checks only when asked, verifies the downloaded file before starting it, and preserves the token, stack and prompts. An installed 1.0.1 still needs one manual download to gain this action.

## Install

**By hand.** Download `SystemSentinel.exe` below, put it on a local disk and double-click it. The file is not code-signed, so Windows asks once before running it: either *Windows protected your PC* (click *More info*, then *Run anyway*) or *The publisher could not be verified* (click *Run*). Within a few seconds your browser opens on the dashboard, already signed in, and the mark sits in the system tray. Details in [the install note](https://github.com/SuperDuperDave/system-sentinel/blob/{{TAG}}/docs/DEPLOY.md).

**With your agent.** Paste [the short prompt](https://github.com/SuperDuperDave/system-sentinel/blob/{{TAG}}/docs/DEPLOY.md#the-short-prompt) to Claude Code, Codex or any agent with a shell on the machine you want to read. It downloads this file, checks its SHA-256 against the list below, starts it, proves it answers, and registers the tool so the agent reads the machine through it instead of writing scripts. [The long prompt](https://github.com/SuperDuperDave/system-sentinel/blob/{{TAG}}/docs/DEPLOY.md#the-long-prompt) installs from source instead.

**Check the download.** SHA-256 of `SystemSentinel.exe`:

```
{{SHA256}}
```

`Get-FileHash SystemSentinel.exe` prints yours. `SHA256SUMS.txt` beside it carries the same value, and the file is attested: `gh attestation verify SystemSentinel.exe --repo SuperDuperDave/system-sentinel` checks it against the workflow run that built it.

## What it needs

Windows 10 or 11, 64-bit. Nothing else: the server, the dashboard and the decoder are inside the file. Tailscale only if you want to reach it from your phone.

## What it does not do

It sends no readings, token, captures or handoffs off the machine, listens on localhost only, installs no service and asks for no account. A person-triggered update check contacts GitHub for release metadata and an accepted download; there is no background poll. Reaching the app from elsewhere is a transport you put in front of it, and every request still needs the token.

## For agents

The API is documented for an agent to read without the source: [docs/API.md](https://github.com/SuperDuperDave/system-sentinel/blob/{{TAG}}/docs/API.md). Every reading is one MCP tool; the outcome of each says whether the machine was observed, and a collection failure is never an empty result.

## Tested on

Every commit is checked on a Windows machine that is not the one this was built for: the whole suite runs there, host tests included, against that machine's own event log, and this executable is built there the way a person would build it. What that establishes, and what it does not, is in [the contributing note](https://github.com/SuperDuperDave/system-sentinel/blob/{{TAG}}/CONTRIBUTING.md#verification). What the tool has been run through by hand, on a clean image and through an agent, is kept in [the install note](https://github.com/SuperDuperDave/system-sentinel/blob/main/docs/DEPLOY.md#what-the-prompts-were-tested-on) and grows as later runs are made. The file is not code-signed, so Windows asks once before running it.
