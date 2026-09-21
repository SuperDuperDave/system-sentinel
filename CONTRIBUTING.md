# Contributing

System Sentinel is worked by a person and an agent together; [AGENTS.md](AGENTS.md) is the working agreement and the routing table, and it applies to you too. The short version:

- **One boundary.** Nothing reads the machine except through a reading registered in `sentinel/reading.py`'s catalog and taken through `sentinel/bridge.py`. A new capability is a new reading, and it becomes a route, an OpenAPI entry and an MCP tool by being registered. [docs/API.md](docs/API.md) is the contract; change it in the same change.
- **Evidence, not verdicts.** A reading's outcome says whether the machine was observed. Sections are `raw`, `derived`, `invariant` or `inferred`, and an inferred section carries its basis. Nothing in the tool diagnoses; it shows leads.
- **Nothing private on the wire by default.** Serial numbers, the computer name, user names, MAC addresses and profile paths are removed by `sentinel/redact.py`. If a new reading carries something of that kind, declare it in the reading's `private` list and make sure the policy removes it; add a rule to the policy, not to the reading.
- **Verify on the machine.** `./.venv/bin/python -m pytest` runs the unit tests through a fake bridge and the host tests against the real Windows event log where there is one. A reading is done when it has run on a real machine and its envelope was inspected, including the failure path. `npm run build` in `dashboard/` type-checks and builds the dashboard; check a view on glass at phone width (390) and desktop (1440) before calling it done. GitHub Actions runs the same commands on every push, the host tests included, on a Windows machine that is not the one this was built for; [AGENTS.md](AGENTS.md#verification) says what a green run establishes and what it does not.
- **The identity is owned.** Colors, faces and the mark come from [docs/design/IDENTITY-DIRECTIONS-2026-09-20.md](docs/design/IDENTITY-DIRECTIONS-2026-09-20.md); the phosphor is light only, never text.

Pull requests are welcome. Say what was checked and how: source inspected, run locally, tested, and observed on a host are four different claims.
