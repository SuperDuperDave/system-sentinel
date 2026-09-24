# Agent answer size

The reading envelope carries exact source evidence, its limits, and the collection method. An MCP reading returns that envelope as both JSON text and structured content. This table measures the serialized result, not what a particular client places in a model's context. Client handling of large answers has not been established; these numbers are a shape baseline, not a client limit or a diagnosis of truncation.

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

The envelope now places outcome, count, errors, warnings and redaction notes ahead of sections and method in text. That preserves the useful prefix of a long answer when someone reads it from the top, but does not reduce its size or establish how an agent client handles a long result. Before changing the raw or derived evidence contract, measure client behavior with known-size synthetic answers, including whether it rejects, truncates or passes each representation to the model. A projection must keep source coverage and omission explicit and preserve the alignment between raw records, decoded entries and cross-references.
