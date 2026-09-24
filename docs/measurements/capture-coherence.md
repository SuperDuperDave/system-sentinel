# One observation per capture source

A capture is an offline record. Through 1.9.20, it saved the seven readings that Signals uses and then asked those sources again inside Signals. The two answers could differ in time and scope: the archive's default `crash.json` requested five stops, while Signals examined up to twenty. A Signals crash reference could therefore point to a raw row absent from the ZIP. In 1.9.21, capture gathers those seven inputs once, composes Signals from them and saves those same input envelopes as members. The manifest names their origin and actual parameters.

This measurement compares the published `v1.9.20` source with `v1.9.21` using the same public synthetic fixture and [`measure_capture.py`](../../scripts/measure_capture.py). It reads no host data, uses a temporary data home and deletes it. Run it from a source checkout with test dependencies installed:

```sh
PYTHONPATH=. .venv/bin/python scripts/measure_capture.py
```

The ordinary case combines the screen fixture and test reading payloads. The saturated case generates 200 System rows across levels 1–4, each with a deterministic random 512-character message and 512 property bytes, plus twenty stop rows. It deliberately fills the two scopes that change. The script counts synthetic bridge calls and, in the saturated case, its two replacement source takers. Counts describe fixture calls, not Windows query cost. ZIP bytes include all reading members, saved Stack, handoff, manifest, compression and ZIP framing. The captures are unredacted synthetic evidence; a redacted real capture can differ. Timestamps and measured durations inside the ZIP can shift the compressed byte count by a few bytes between runs.

| Fixture | Source questions, 1.9.20 → 1.9.21 | ZIP bytes, 1.9.20 → 1.9.21 | Uncompressed bytes, 1.9.20 → 1.9.21 |
| --- | ---: | ---: | ---: |
| Ordinary | 32 → 25 | 70,868 → 72,061 | 326,196 → 345,916 |
| Saturated | 32 → 25 | 119,784 → 298,598 | 358,966 → 656,274 |

The seven removed calls are the second takes of Signals' inputs. This is a count reduction, not a measured time saving. The ordinary ZIP grew about 1.7%. The saturated ZIP grew about 179 kB, or 2.49 times its former size, because it now retains evidence Signals had already queried and discarded from the archive. In that case `events.json` grew from 89,499 to 356,663 uncompressed bytes when its saved scope widened from 50 error/critical rows to the actual 200 rows at levels 1–4. `crash.json` grew from 10,517 to 40,129 bytes when the saved stop scope widened from five to twenty. The same raw rows are present in Signals' input observation; trimming the member would break offline inspection of what Signals actually used.

Events and Crash have fixed row and stop limits, but individual event messages and properties can be large. These fixtures do not bound real capture bytes, test Windows latency, estimate how often users make captures or establish a typical compression ratio. A multi-megabyte real Events member or a concrete sharing-size limit would justify a separately designed byte budget with an explicit omitted-byte count; silently narrowing Signals inside captures or discarding the source rows would weaken the evidence contract.
