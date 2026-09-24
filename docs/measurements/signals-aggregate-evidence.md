# Qualifying aggregate Signals evidence

Signals counts a provider's share of returned System rows and notes when display resets and wakes or resumes appear in the same bounded Power ledger. Through 1.9.21, a large share was called a “burst” without measuring a time concentration. The reset lead's `window.last` was the newest transition of any kind, which the dashboard turned into a Record jump even when it was not a reset. In 1.9.22, these leads state their returned sample and the reset lead cites unique raw System rows it used.

The measurement uses only synthetic public data. It compares the published v1.9.21 source with v1.9.22 source using the same scripts and fixture. `scripts/measure_agent_answers.py` builds the ordinary broad agent answer, including the live Signals input scopes. `scripts/measure_signals_leads.py` builds a separate 200-row, fourteen-day Events sample with one provider's sixty rows in a one-minute cluster, and a Power sample with thirteen reset rows (one malformed id and two ambiguous duplicate-id rows) plus a wake. The second script serializes each lead as compact UTF-8 JSON; it does not measure MCP's duplicate text and structured representation. Both scripts can be run from a checkout with test dependencies:

```sh
PYTHONPATH=. .venv/bin/python scripts/measure_agent_answers.py
PYTHONPATH=. .venv/bin/python scripts/measure_signals_leads.py
```

To reproduce the old-source side, extract `git archive v1.9.21` into a temporary directory, copy the current `scripts/measure_signals_leads.py` into that directory and run it there with the same Python environment. The agent-answer script already exists at that tag. Neither script reads Windows or a real app home.

| Synthetic observation | v1.9.21 | v1.9.22 | Change |
| --- | ---: | ---: | ---: |
| Signals MCP text bytes | 10,277 | 10,952 | +675 |
| Signals MCP result bytes | 21,854 | 23,270 | +1,416 |
| Broad Health + Signals MCP text bytes | 11,227 | 11,902 | +675 |
| Broad path machine questions | 8 | 8 | 0 |
| Pressure lead compact JSON bytes | 395 | 619 | +224 |
| Display-reset lead compact JSON bytes | 426 | 1,282 | +856 |
| Display-reset inline exact refs | 0 | 5 | +5 |

The new reset lead reports thirteen raw candidates, three unusable references and five valid references omitted by the display cap. `sample.oldest` and `sample.newest` refer only to returned rows and become null when any row time is unreadable. `limit_reached: false` means no extra matching row was observed beyond the query's limit; older events may already have left the log. The pressure share remains rows divided by returned rows, not events per unit time. The synthetic broad fixture contains no display-reset lead, so its +675 bytes do not include the reference-heavy case. That byte change includes new scope fields and changed title and summary wording, not just the `sample` block. One byte of unrelated Storms output differed between earlier agent-answer runs because generated timestamps changed; the Signals row and broad-path comparison isolate the intended change.

These figures do not establish host query duration, archive size, model context consumed by a particular client, human investigation time or how often these leads fire on a real machine. An exact `event_record` follow-up is a new observation and can report a reused id or a row no longer returned; it does not recreate the original Signals sample. The dashboard's dedicated exact-record interaction remains to be built.
