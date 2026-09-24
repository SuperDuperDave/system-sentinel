# Agent answer size

The reading envelope carries exact source evidence, its limits, and the collection method. An MCP reading returns that envelope as both JSON text and structured content. This table measures the serialized result, not what a particular client places in a model's context. These numbers are a shape baseline, not a client limit or a diagnosis of truncation.

Regenerate from public synthetic inputs in a source checkout with test dependencies installed. The command needs the repository root on Python's import path because the synthetic bridge is in `tests/`:

```sh
PYTHONPATH=. .venv/bin/python scripts/measure_agent_answers.py
```

In Windows PowerShell, use `$env:PYTHONPATH = '.'` and then `.\.venv\Scripts\python.exe scripts\measure_agent_answers.py`. The screen fixture supplies Health, Events, Record, Faults, Storms, WHEA and the Power input to Signals. Crash uses the test Crash records and dump inventory, with the source reach set for each requested stop count. Signals also uses synthetic Hardware from the system test, PCIe, Constraints and Reliability from the diagnostics test, and Events from the screen fixture; the script refuses a failed input even when the Signals envelope itself says `ok`. Its `record` anchor is the current UTC instant, and measured duration fields can vary, so byte counts can shift slightly between runs. The last row is 2,000 generated System records with a 512-character synthetic message payload and a 512-byte binary value represented as a plain hex string in `Properties`, matching the event collector's projected shape. The script uses a temporary home and reads no machine state. A test runs the eight default rows in CI to catch fixture or reading drift; the large generated row runs only when the measurement command is invoked.

Measured with source version 1.9.18 on 2026-09-24:

| Synthetic reading | Outcome | Count | MCP text bytes | MCP result bytes | Sections bytes | Method bytes |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| health | ok | — | 950 | 2,126 | 530 | 199 |
| crash | ok | 5 | 40,685 | 84,851 | 27,213 | 13,228 |
| events | ok | 20 | 15,829 | 32,761 | 11,197 | 4,327 |
| record | ok | 50 | 26,769 | 55,836 | 21,897 | 4,578 |
| faults | ok | 7 | 19,207 | 40,361 | 14,244 | 4,682 |
| storms | ok | 40 | 18,207 | 37,236 | 12,046 | 5,738 |
| whea | ok | 30 | 35,649 | 74,007 | 28,503 | 6,913 |
| signals | ok | 11 | 6,617 | 14,004 | 5,687 | 710 |
| record: 2,000 generated rows | ok | 2,000 | 3,434,742 | 6,917,821 | 3,429,868 | 4,588 |

For the heavy row, raw `Message` values account for 1,064,000 serialized bytes and raw `Properties` values for 2,056,000. Those fields dominate the 3,429,868-byte sections payload; the 4,588-byte method is a small part of this case. MCP result bytes include the text and structured representations plus their JSON wrapper, so they must not be interpreted as model-context bytes. The source can be requested with smaller counts or a narrower window; the raw answer itself is still complete for the request made.

The envelope now places outcome, count, errors, warnings and redaction notes ahead of sections and method in text and structured content. That preserves the useful prefix of a long answer when someone reads it from the top, but does not reduce its size. A projection must keep source coverage and omission explicit and preserve the alignment between raw records, decoded entries and cross-references.

## Claude Code client probes

On 2026-09-24, Claude Code 2.1.280 with Opus 5.5 and requested high effort called an isolated stdio MCP server serving the *exact structured answer objects* from the synthetic 1.9.18 rows. The server exposed only one answer tool. The client returned every tested default reading inline. Parsed `tool_result` JSON equaled the server's structured answer for every case. The bytes shown here are what appeared in Claude Code's structured tool result; they can differ from the MCP text bytes above because the text and structured representations serialize Unicode differently. An initial 1.9.18 probe accidentally pointed the server to system Python instead of the virtual environment and had no connected MCP tool; these results come from the corrected run with the server confirmed connected.

| Exact synthetic default | Claude Code result bytes | Delivery | Parsed content | Serialized bytes |
| --- | ---: | --- | --- | --- |
| Health | 950 | Inline | Exact | Exact |
| Crash, 5 stops | 40,604 | Inline | Exact | Exact |
| Events | 15,829 | Inline | Exact | Exact |
| Record, 50 events | 26,769 | Inline | Exact | Exact |
| Faults | 19,207 | Inline | Exact | Exact |
| Storms | 18,201 | Inline | Exact | Re-serialized |
| WHEA | 35,649 | Inline | Exact | Exact |
| Signals | 6,617 | Inline | Exact | Exact |

The byte hashes also matched the server's serialized structured content in seven cases. Storms was semantically identical but its client result serialized three integral `2.0`, `6.0` and `15.0` values as `2`, `6` and `15`, making it six bytes shorter. The 81-byte Crash difference between the text column and the client result comes from the source's ASCII escaped Unicode versus the structured content's UTF-8 serialization, not missing evidence. This is a comparison of parsed answers, not proof that every client preserves original byte serialization. The synthetic Crash source contained only 13 System and 4 Application event rows; requesting one or two stops still produced about 37–38 KiB of raw evidence because changing the result count did not remove those sparse source rows. A smaller count is therefore not a general guarantee of a smaller answer.

In the 1.9.18 run, Claude Code requested all eight exact answers in the recommended order in one session. It made eight tool calls and received all eight complete parsed answers inline, with no saved-file notice. Its final response could cite the first Health answer (`ok`, no count) and last Signals answer (`ok`, 11 signals). The eight MCP text bodies total 163,913 bytes, including 40,375 bytes of methods; the structured tool results total 163,826 bytes. The client reported 79,831 cache-creation input tokens over nine turns. That is client accounting, not a direct measure of how thoroughly the model reasoned over every record. A separate 1.9.17 sequential probe likewise kept all eight inline and reported different cache usage, so neither probe establishes a stable context cost. The sequential result says more about this default path than isolated calls, but does not establish behavior for larger counts or another client.

An earlier probe tested generated Sentinel-shaped payloads rather than these exact default readings:

That server supplied JSON text and `structuredContent` with different `T_` and `S_` markers; it logged the SHA-256 of each representation. Each invocation used only the temporary server and its single answer tool. Both probes tested the client path, not System Sentinel's HTTP transport or any machine reading.

| Synthetic case | Text / structured bytes | Claude Code `tool_result` | Verified result |
| --- | ---: | --- | --- |
| 8 KiB target, identical representations | 7,688 / 7,688 | Inline, 7,688 bytes | Exact payload hash; early and tail markers present |
| 64 KiB target, identical representations | 59,175 / 59,175 | 1,744-byte path notice | Saved file hash matched the full payload; early and tail markers present in the file |
| 8 KiB target, distinct representations | 7,712 / 7,712 | Inline, 7,712 bytes | Hash and `S_` markers matched structured content, not text |
| 64 KiB target, distinct representations | 59,199 / 59,199 | Path notice | Saved file hash and `S_` markers matched the full structured content, not text |

In the generated cases, Claude Code selected structured content when both representations differed. In the larger generated case, the model received a path notice and did not report the evidence markers until it read the saved file. The exact default readings above remained inline even at 40,604 bytes, so neither result establishes a universal size threshold. If a client gives an agent a saved-file notice, the agent must read the file before drawing conclusions from the evidence. Moving caveats to the front helps someone reading either form from the top, but cannot determine a client's delivery choice. These results do not establish behavior of other client versions or Codex. The source answer remains complete; a future compact agent projection would need an explicit omission count and an exact route back to the full evidence.
