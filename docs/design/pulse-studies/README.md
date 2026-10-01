# System Pulse concept studies

Three interactive design explorations, not a renamed or connected application. Open `index.html` directly in a browser. No build step or running service is required. Typeface requests go to Google Fonts; system fonts are the fallback. No machine reading, account or token is used.

- **Luminous instrument:** the layered observation map is the navigation landmark.
- **Tactile instrument:** the selected question opens a compact working surface.
- **Pulse journal:** a retained observation is read as an incident account.

Each supports the same two moments: orient around a pulse and inspect a recorded incident. Take a pulse is a local demonstration. Select observation areas, open an incident, inspect raw sample records, prepare a report preview, and switch appearance. Example buttons demonstrate a returned incident with a denied source, empty matching records, partial retention and unavailable collector contact. A missing collector retains the saved observation and makes no claim about why contact was lost.

## Evidence contract

The `sample` object contains only synthetic records. It anchors collection to 2026-10-01 at 10:00 UTC, with a 09:59–10:00 collection span and a retrospective 24-hour question. The same sample is used in every design. Alternative example states project empty results or limited coverage; they are illustrative scenarios, not observations of Windows.

- The restart count comes from the single composed stop in `sample.stops`; raw Event 41, 6008 and 1001 rows are supporting records, not three stops.
- Event 6008 supplies the 09:42 stop estimate. The next-start records are filed after 09:46. The time difference is not an error duration or causal claim.
- The returned bug-check code is 0x124 (292 in Event 41). It does not establish the failing component or root cause.
- The earlier update has its own source timestamp. Temporal proximity is not attributed cause.
- The volume reports 512 GiB total and 132 GiB free. Used space is derived as 380 GiB. The bar depicts these quantities and cannot establish historical growth or safe cleanup.
- Hardware-error access is denied in the gap examples. Empty matching results remain distinct from a denied source. A partial example additionally names unknown retained reach.
- The report retains fixed collection date, UTC, sample references and unanswered cause. It is a preview, not a download or an assistant interpretation.

The exploded map is an index into observation areas. Its ordering and grid do not claim physical topology or a failure chain. One event mark belongs to the composed restart. Selection lighting is selection, not severity. No health score, baseline, prediction, diagnostic accuracy, process history or repair is claimed.

## Verification and limits

Review evidence and design disposition are maintained in the project's private method. These studies were exercised in local Chromium for source inspection, report preview, example states, appearance and narrow layout. That establishes browser behavior on synthetic inputs, not collector behavior, Windows installation, actual-user comprehension or acceptance of a visual direction.

The studies add no production route, new reading, command or notification. The installed tool's identity and behavior are unchanged.
