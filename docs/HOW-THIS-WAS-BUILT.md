# How this was built

System Sentinel is a Windows tool built by one person working with AI coding agents. The tool is the artifact; how it was worked is most of what it demonstrates. Four habits did the work, and each is visible in the source.

## Evidence over verdict

A tool that reads a computer's own record is only useful if you can tell what it saw from what it inferred, and both from what it never managed to look at. So the envelope every reading returns carries an **outcome** — `ok`, `empty`, `failed`, `unavailable`, `denied`, `timeout` — and a collection failure is never an empty result. A query that could not run says so; a log with nothing in the window says something else. The previous version of this project returned an empty list for both, which is the bug class this replaced rather than patched.

The same distinction runs through the payload and the boundary. Sections are typed `raw`, `derived`, `invariant` or `inferred`, and an inferred section carries its basis. Every reading carries the query that produced it, so any claim can be reproduced by hand. Nothing here diagnoses: a burst of corrected errors is a lead, and the reading belongs to the person or the agent holding the evidence. And what leaves is redacted by field name first, with the response listing what was removed. `sentinel/reading.py`, `sentinel/bridge.py` and `sentinel/redact.py` are where this lives.

## Subtraction over machinery

The fastest improvement is usually not a cleverer mechanism. It is removing the need for the mechanism while the capability stays identical.

The clearest case is the executable. Five symptoms had collected around double-clicking it: a windowed program that failed silently when the port was already held; no way to tell which version you had; an update that could not replace a running copy, because the running copy held the file; a *Start with Windows* shortcut pointing wherever the browser had dropped the download; and no way to remove the thing. Five fixes were available. What the symptoms had in common was that the program could be anywhere, and nowhere was home.

So one boundary changed: **the installed copy is the only copy that serves.** A start now asks four questions — am I a built executable, am I the installed copy, is something already serving here and which version is it, am I already those bytes — and answers with one of six plans: open, serve, start the installed copy, install, replace the running copy, or step back because what is running is newer. It is a pure function (`plan` in `sentinel/launcher.py`), so every branch can be asked for without an executable, a port or a machine, which is why the branches are tested rather than argued about.

The five symptoms went with it. A download installs itself, so the startup shortcut has a stable path to point at. An update is a newer file double-clicked: it asks the running copy to quit over the tool's own authenticated boundary, takes its place, and refuses to downgrade. Removal has one directory to delete. The comparison needed a version, so the version went into the file's properties and onto the catalog the server answers with — which is also how you tell the file on disk from the process that is running. And every branch returns the sentence explaining it, which is what the message box says when a windowed program cannot start. One decision in one place, instead of five guards.

## Verification on the machine, not in the head

A claim about a machine that was not checked on a machine is worse than no claim. Four phrases are kept apart here — source inspected, run locally, tested, observed on a host — and a reading is done when it has run against real Windows and its envelope has been read, including what comes back when the query fails.

Two things make that affordable. Continuous integration runs the suite on Linux through a fake bridge and again on a real Windows runner with the host tests included, so every push is checked against a Windows machine nobody here tuned, with its own event log, its own parts and its own noise; the same job builds the executable the way a person would. And `build/windows/sandbox/` makes a machine that has never seen the tool: a disposable Windows 11 in Windows Sandbox, without Python or developer tooling, running the install paths unattended and bringing back what happened. It observes the first run — the download, the checksum, the unblock, the first answer from the API — and cannot observe hardware it does not have, or how the tool behaves over weeks on a machine somebody uses. Both limits are stated rather than papered over; [CONTRIBUTING.md](../CONTRIBUTING.md#verification) says what a green run establishes and what it does not.

## Working with agents

The division of labour is stable. The person sets direction, decides taste, and owns anything involving money, accounts, external services or publishing — every release is read and published by a person. Agents survey the ground, build components to a contract, and verify on the host instead of reasoning about what Windows probably returns. A lead agent holds the shape, reviews the work and keeps the records the next session resumes from, which matters more than it sounds: the expensive failure is not a bad line of code but a session that re-derives a decision somebody already made and rejected.

Two rules keep it honest. Architecture is the agent's to decide and to explain in a paragraph; taste is not. And instructions convey judgment while code and tests enforce invariants — a rule nobody is prompted to follow will not hold, which is why the check that no committed file carries a machine name, an account name or a local path is a step in CI rather than a paragraph somewhere.

## What was rejected

- **An update check inside the tool.** *Check for updates…* opens the releases page in your browser. Anything else means the tool asking a server off this machine whether it is current, against the one promise it makes.
- **Code signing, for now.** A certificate is a purchase and an identity, and neither is decided. The consequence is stated rather than hidden: Windows asks once before running an unsigned file, and a release is verifiable three ways — the checksum, the digest GitHub computed, and a provenance attestation tying the file to the workflow run that built it ([SECURITY.md](../SECURITY.md#verifying-a-release)).
- **A separate updater.** The file installs and updates itself. A second program would be another thing to trust, to sign and to remove.
- **Anything that makes this a service.** No telemetry, no hosted version, no account, no prediction. The deliberate absences are as load-bearing as the features.

---

[CONTRIBUTING.md](../CONTRIBUTING.md) has the part of the working method a contributor needs. [SECURITY.md](../SECURITY.md) has the trust boundary and the threat model.
