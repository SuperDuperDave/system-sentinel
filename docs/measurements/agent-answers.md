# Agent answer size

The reading envelope carries exact source evidence, its limits, and the collection method. An MCP reading returns that envelope as both JSON text and structured content. This table measures the serialized result, not what a particular client places in a model's context. These numbers are a shape baseline, not a client limit or a diagnosis of truncation.

Regenerate from public synthetic inputs in a source checkout with test dependencies installed. The command needs the repository root on Python's import path because the synthetic bridge is in `tests/`:

```sh
PYTHONPATH=. .venv/bin/python scripts/measure_agent_answers.py
```

In Windows PowerShell, use `$env:PYTHONPATH = '.'` and then `.\.venv\Scripts\python.exe scripts\measure_agent_answers.py`. The screen fixture supplies the first six rows. Its `record` anchor is the current UTC instant, so byte counts can shift slightly with the date and fixture changes. The last row is 2,000 generated System records with a 512-character synthetic message payload and a 512-byte binary value represented as a plain hex string in `Properties`, matching the event collector's projected shape. The script uses a temporary home and reads no machine state.

Measured with source version 1.9.4:

| Synthetic reading | Outcome | Count | MCP text bytes | MCP result bytes | Sections bytes | Method bytes |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| health | ok | — | 743 | 1,684 | 371 | 199 |
| events | ok | 20 | 15,765 | 32,623 | 11,197 | 4,327 |
| record | ok | 50 | 26,722 | 55,736 | 21,897 | 4,578 |
| faults | ok | 7 | 19,160 | 40,261 | 14,244 | 4,682 |
| storms | ok | 40 | 18,159 | 37,134 | 12,046 | 5,738 |
| whea | ok | 30 | 35,601 | 73,905 | 28,503 | 6,913 |
| record: 2,000 generated rows | ok | 2,000 | 3,434,694 | 6,917,719 | 3,429,868 | 4,588 |

For the heavy row, raw `Message` values account for 1,064,000 serialized bytes and raw `Properties` values for 2,056,000. Those fields dominate the 3,429,868-byte sections payload; the 4,588-byte method is a small part of this case. MCP result bytes include the text and structured representations plus their JSON wrapper, so they must not be interpreted as model-context bytes. The source can be requested with smaller counts or a narrower window; the raw answer itself is still complete for the request made.

The envelope now places outcome, count, errors, warnings and redaction notes ahead of sections and method in text and structured content. That preserves the useful prefix of a long answer when someone reads it from the top, but does not reduce its size. A projection must keep source coverage and omission explicit and preserve the alignment between raw records, decoded entries and cross-references.

## Claude Code client probe

On 2026-09-24, a local, synthetic stdio MCP server returned a Sentinel-shaped answer to Claude Code 2.1.280. It supplied JSON text and `structuredContent` with different `T_` and `S_` markers; the server logged the SHA-256 of each representation. Each invocation used only this temporary MCP server and its single answer tool. This tested the client path, not System Sentinel's HTTP transport or any machine reading.

| Synthetic case | Text / structured bytes | Claude Code `tool_result` | Verified result |
| --- | ---: | --- | --- |
| 8 KiB target, identical representations | 7,688 / 7,688 | Inline, 7,688 bytes | Exact payload hash; early and tail markers present |
| 64 KiB target, identical representations | 59,175 / 59,175 | 1,744-byte path notice | Saved file hash matched the full payload; early and tail markers present in the file |
| 8 KiB target, distinct representations | 7,712 / 7,712 | Inline, 7,712 bytes | Hash and `S_` markers matched structured content, not text |
| 64 KiB target, distinct representations | 59,199 / 59,199 | Path notice | Saved file hash and `S_` markers matched the full structured content, not text |

In these calls, Claude Code selected structured content when both representations differed. At the larger size, the model received a path notice and did not report the evidence markers until it read the saved file. Moving caveats to the front helps inline answers and the saved file, but cannot by itself make an oversized answer visible inline. The result does not establish a universal threshold, behavior of other client versions, or Codex behavior. The source answer remains complete; a future compact agent projection would need an explicit omission count and an exact route back to the full evidence.
