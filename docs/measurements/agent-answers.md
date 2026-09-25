# Agent answer size

The reading envelope carries exact source evidence, its limits, and the collection method. An MCP reading returns that envelope as both JSON text and structured content. This table measures the serialized result, not what a particular client places in a model's context. These numbers are a shape baseline, not a client limit or a diagnosis of truncation.

Regenerate from public synthetic inputs in a source checkout with test dependencies installed. The command needs the repository root on Python's import path because the synthetic bridge is in `tests/`:

```sh
PYTHONPATH=. .venv/bin/python scripts/measure_agent_answers.py
```

In Windows PowerShell, use `$env:PYTHONPATH = '.'` and then `.\.venv\Scripts\python.exe scripts\measure_agent_answers.py`. The screen fixture supplies Health, Events, Record, Faults, Storms, WHEA and the Power input to Signals. Crash uses the test Crash records and dump inventory, with the source reach set for each requested stop count. Signals also uses synthetic Hardware from the system test, PCIe, Constraints and Reliability from the diagnostics test, and Events from the screen fixture; the script refuses a failed input even when the Signals envelope itself says `ok`. Its `record` anchor is the current UTC instant, and measured duration fields can vary, so byte counts can shift slightly between runs. The last row is 2,000 generated System records with a 512-character synthetic message payload and a 512-byte binary value represented as a plain hex string in `Properties`, matching the event collector's projected shape. The script uses a temporary home and reads no machine state. A test runs the eight default rows in CI to catch fixture or reading drift; the large generated row runs only when the measurement command is invoked.

Measured with source version 1.9.20 on 2026-09-24, using the same public fixtures as the 1.9.19 baseline:

| Synthetic reading | Outcome | Count | MCP text bytes | MCP result bytes | Sections bytes | Method bytes |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| health | ok | — | 950 | 2,126 | 530 | 199 |
| crash | ok | 5 | 40,685 | 84,851 | 27,213 | 13,228 |
| events | ok | 20 | 15,829 | 32,761 | 11,197 | 4,327 |
| record | ok | 50 | 26,769 | 55,836 | 21,897 | 4,578 |
| faults | ok | 7 | 19,207 | 40,361 | 14,244 | 4,682 |
| storms | ok | 40 | 18,207 | 37,236 | 12,046 | 5,738 |
| whea | ok | 30 | 35,649 | 74,007 | 28,503 | 6,913 |
| signals | ok | 11 | 10,277 | 21,854 | 9,004 | 1,053 |
| record: 2,000 generated rows | ok | 2,000 | 3,434,742 | 6,917,821 | 3,429,868 | 4,588 |

For the heavy row, raw `Message` values account for 1,064,000 serialized bytes and raw `Properties` values for 2,056,000. Those fields dominate the 3,429,868-byte sections payload; the 4,588-byte method is a small part of this case. MCP result bytes include the text and structured representations plus their JSON wrapper, so they must not be interpreted as model-context bytes. The source can be requested with smaller counts or a narrower window; the raw answer itself is still complete for the request made.

The envelope now places outcome, count, errors, warnings and redaction notes ahead of sections and method in text and structured content. That preserves the useful prefix of a long answer when someone reads it from the top, but does not reduce its size. A projection must keep source coverage and omission explicit and preserve the alignment between raw records, decoded entries and cross-references.

## Question-directed entry paths

The MCP guidance now starts with Health, then chooses a reading by the person's question. It uses Signals for a broad or unclear question, or when a directed reading leaves the problem unexplained. Signals calls seven underlying readings, including Crash at 20 stops. It takes none of the WHEA readings and does not read the Kernel-WHEA/Errors log. System WHEA-Logger records can appear among its recent System events, but Signals does not classify them as hardware errors. Its `ok` can still have missing inputs: inspect `gap:inputs`, warnings and `method.readings[].outcome`. An `empty` Signals result means no pattern was noticed in what answered, not that the computer is healthy. The question-directed paths below are a routing guide, not fixed diagnostic recipes; further exact reads depend on the answer.

| Synthetic path | Readings | MCP text bytes | Bridge questions |
| --- | ---: | ---: | ---: |
| Broad or unclear: Health + Signals | 2 | 11,227 | 8 |
| Unexpected restart: Health + Crash + Record | 3 | 68,404 | 3 |
| Hardware errors: Health + WHEA + Storms | 3 | 54,806 | 3 |
| Program crashed or hung: Health + Faults | 2 | 20,157 | 2 |
| Former eight-reading tour | 8 | 167,573 | 14 |

The byte totals are sums of the rows above; the bridge-question counts are printed by the same measurement script. A bridge question is one call to the **synthetic** bridge after the app's one-time identity lookup at startup. It does not measure Windows query time, session contention or another machine's records. The broad path returns 6.7% as many MCP text bytes as the former tour, but its eight source questions exceed either directed three-reading path's three. This is why the smaller answer is the starting point for broad questions, not an unconditional second step. A specific question can end after fewer readings; the table includes likely follow-up reads for comparison. The original eight answers remain directly available when needed.

## Signals class content reach in 1.9.27

The same synthetic command was run from the exact published `v1.9.26` source archive and the
1.9.27 candidate on 2026-09-24 local time. This isolates the new per-run
`method.class_content_inputs` field from the other changes since the older table above.

| Synthetic answer | 1.9.26 | 1.9.27 | Added |
| --- | ---: | ---: | ---: |
| Signals method JSON | 1,053 B | 1,259 B | 206 B |
| Signals MCP text | 10,952 B | 11,158 B | 206 B |
| Signals full MCP result | 23,270 B | 23,716 B | 446 B |
| Health + Signals MCP text | 11,902 B | 12,108 B | 206 B |
| Health + Signals bridge questions | 8 | 8 | 0 |

The result includes text and structured content, so its byte delta is larger than one JSON
field. These are transfer-shape measurements from public synthetic inputs, not client context
usage, Windows latency or a real-machine sample. The new field names content inputs consulted by
each class during composition; the existing `method.readings` still owns each input's outcome and
warnings. The separate `gap:inputs` lead checks all input outcomes.

Compared with 1.9.19 on the same fixtures, Signals grew from 6,617 to 10,277 MCP text bytes (+3,660, about 55%). Crash leads now carry bounded log-local row references with explicit missing and omitted counts, and `method.readings` includes each input's original observation time and count. Each ref names `event_record` and includes tool-ready `params`. Signals still takes seven source questions; `event_record` is an optional exact follow-up, not part of this table. At this 1.9.20 measurement point, a pressure share or Power ledger co-occurrence had no exact-row reference. A fresh read cannot reproduce the original capped sample. Version 1.9.22 adds bounded refs for the display-reset lead only; see the [aggregate-evidence measurement](signals-aggregate-evidence.md). The byte increase is measured; any reduction in investigation time or total agent cost remains unmeasured.

Three earlier direct Claude Code 2.1.280 probes with Opus 5.5 and requested high effort connected to a disposable stdio MCP server backed only by these public synthetic fixtures. The server offered the 1.9.19 candidate's reading tools, with instructions as noted below; it logged each call's name, outcome and MCP text bytes. All three probes completed. This tests one client's choices under three prompts, not a before-and-after comparison or a typical user's session. The quick-check probe used the final 1.9.19 depth guidance; the other two used the earlier question-directed wording from that candidate. They did not test the new `event_record` path or the larger 1.9.20 Signals answer.

| Prompt | Observed tool sequence | Calls | MCP text bytes returned | Client cache-creation input tokens |
| --- | --- | ---: | ---: | ---: |
| “Are there hardware errors?” | Health, WHEA, Storms, four exact WHEA records | 7 | 102,210 | 56,409 |
| Broad computer health question with the Signals PCIe input unavailable | Health, System (failed), Signals, Crash, WHEA, Faults, Storms, one exact WHEA record, PCIe, Hardware GPU, Memory | 11 | 148,454 | 82,600 |
| “Quick check: is anything wrong?” with the Signals PCIe input unavailable | Health, Signals | 2 | 7,702 | 9,445 |

The hardware prompt went directly to both WHEA sources and then selected records, without taking Signals. The broad prompt took Signals early but continued into a larger investigation; a compact first answer does not cap later work. The client later took PCIe directly after that Signals input was absent, and its final answer disclosed a separate failed System snapshot. It did not explicitly name the earlier Signals PCIe gap after that direct read. The quick check stopped after Signals, explicitly named PCIe as unavailable, said WHEA and Faults were not checked, and presented crash patterns as leads rather than a diagnosis. The fixture's System failure in the broad probe is a limitation of that probe, not an observed host failure. In the longer probes, the client noticed synthetic evidence and did not name a single established cause. MCP text bytes are what the server returned, not measured model-context bytes; client token accounting includes more than tool bodies. These probes do not measure another agent, host latency or the effect of changing instructions alone.

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

In the 1.9.18 run, Claude Code requested all eight exact answers in the then-recommended order in one session. It made eight tool calls and received all eight complete parsed answers inline, with no saved-file notice. Its final response could cite the first Health answer (`ok`, no count) and last Signals answer (`ok`, 11 signals). The eight MCP text bodies total 163,913 bytes, including 40,375 bytes of methods; the structured tool results total 163,826 bytes. The client reported 79,831 cache-creation input tokens over nine turns. That is client accounting, not a direct measure of how thoroughly the model reasoned over every record. A separate 1.9.17 sequential probe likewise kept all eight inline and reported different cache usage, so neither probe establishes a stable context cost. The sequential result says more about that former tour than isolated calls, but does not establish behavior for larger counts or another client. The newer question-directed probes above used a different synthetic server and prompt, so they cannot isolate the effect of the instruction change.

An earlier probe tested generated Sentinel-shaped payloads rather than these exact default readings:

That server supplied JSON text and `structuredContent` with different `T_` and `S_` markers; it logged the SHA-256 of each representation. Each invocation used only the temporary server and its single answer tool. Both probes tested the client path, not System Sentinel's HTTP transport or any machine reading.

| Synthetic case | Text / structured bytes | Claude Code `tool_result` | Verified result |
| --- | ---: | --- | --- |
| 8 KiB target, identical representations | 7,688 / 7,688 | Inline, 7,688 bytes | Exact payload hash; early and tail markers present |
| 64 KiB target, identical representations | 59,175 / 59,175 | 1,744-byte path notice | Saved file hash matched the full payload; early and tail markers present in the file |
| 8 KiB target, distinct representations | 7,712 / 7,712 | Inline, 7,712 bytes | Hash and `S_` markers matched structured content, not text |
| 64 KiB target, distinct representations | 59,199 / 59,199 | Path notice | Saved file hash and `S_` markers matched the full structured content, not text |

In the generated cases, Claude Code selected structured content when both representations differed. In the larger generated case, the model received a path notice and did not report the evidence markers until it read the saved file. The exact default readings above remained inline even at 40,604 bytes, so neither result establishes a universal size threshold. If a client gives an agent a saved-file notice, the agent must read the file before drawing conclusions from the evidence. Moving caveats to the front helps someone reading either form from the top, but cannot determine a client's delivery choice. These results do not establish behavior of other client versions or Codex. The source answer remains complete; a future compact agent projection would need an explicit omission count and an exact route back to the full evidence.
