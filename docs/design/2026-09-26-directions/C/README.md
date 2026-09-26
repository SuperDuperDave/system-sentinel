# Direction C: the case file

Phase 1 of the interface overhaul, one of three directions built for David's choice. Branch `claude/2026-09-26-ui-C`, off the audit at `fa9886f`. The audit that set the directions is [the interface audit](../../2026-09-26-interface-audit.md); this note covers only C.

Every screen here was rendered by the real dashboard over the synthetic fixture server. Nothing from a real machine appears.

## The idea

People come to Sentinel because something happened, and working it out spans days and a conversation with an agent. So the unit of the interface is the **case**: "It froze and restarted on Sep 12", opened from a stop, a lead or a question. A case holds exhibits (records, stops, readings, each as it was read), set on the case's own timeline in the order they happened, and the person's own notes.

The agent does not write into a case. It **proposes**, the way a pull request does: a proposal carries its claim, the records it cites and the readings it says it read, and it waits in the margin until a person accepts or declines it in the dashboard. Accepted, it becomes a *lead* of class inferred that keeps its citations and sits below the timeline, never on it, so a sentence never turns into a record. Declined, it stays in the case record with the person's reason.

Today this is a convention between clients, not a lock. One token opens every route, so anything holding it could add evidence directly or mint a dashboard session and decide its own proposal. The prototype makes the convention visible (every item names the route it came by; the draft refuses decisions that arrive with the bearer token) and does not claim it is a boundary.

The old global stack becomes a case's evidence, and the handoff becomes the case exported.

## What a person understands at a glance

**Home** asks *What are you looking into?* and answers, in order:

1. One sentence of observed facts about the machine: which Windows, how long it has been running, when the last unplanned stop was, and, on its own line, which readings did not answer. Nothing on it is a verdict, and a reading that failed is drawn as a hatched mark with its name, so an absence never reads as health.
2. The open cases, by the titles the person gave them, with how many proposals wait in each.
3. *Awaiting your review*: every pending proposal across open cases, with its route, its claim, how many records it cites and how many readings it read, and how many of those were not observed.
4. Places to start: the stops the crash reading placed and the leads the signals reading noticed, each with its time and a one-tap *Open a case* that files it as the first exhibit. A stop that already has a case says so instead.

On a first visit there are no cases, so the page leads with the places to start and one sentence saying what a case is for. The glance is answered by the machine sentence and the stops, not by an empty lobby.

**A case** shows its title, when and from what it was opened, the person's notes, then the evidence as a timeline: a day marker, the time each exhibit is about, the gap to the next one in words ("17 s later") with *Read the log before this*, because a gap is a gap in what was picked, not in the record. Each exhibit is tagged with its number, its reading, its class and when it was taken, and says when it was added and by which route. Accepted leads follow the timeline under *Leads you accepted*; an exhibit a lead cites says so. Beside it all, the margin holds what was proposed and not yet decided, and *How this case was built*: every arrival and decision with the route it came by.

**Any reading opened from a case** carries a slim bar, *Adding evidence to: It froze and restarted on Sep 12*, with *Back to the case* and *Set it down*. While it is there, every *Add* in every view adds to that case.

## Screens

Dark is the product's own appearance and the default for captures; `-light` is the paper appearance. Each at 1440x900 and 390x844; `-full` where length matters.

| Shot | Shows |
| --- | --- |
| [home-1440](home-1440.png), [home-390](home-390.png), [home-light-1440](home-light-1440.png), [home-light-390](home-light-390.png) | Home with one open case, two proposals waiting, the stops and leads to start from |
| [case-1440](case-1440.png), [case-390](case-390.png), [case-light-1440](case-light-1440.png) | The case: notes, the timeline with two exhibits, the margin |
| [case-review-1440](case-review-1440.png), [case-review-390](case-review-390.png), [case-review-light-1440](case-review-light-1440.png) | A proposal under review, its citations compared with a fresh reading |
| [case-accepted-1440](case-accepted-1440.png), [case-accepted-390](case-accepted-390.png) | The same proposal accepted: lead 3, inferred, below the timeline; the stop it cites says "Cited by lead 3" |
| [crashes-1440](crashes-1440.png), [crashes-390](crashes-390.png) | Crashes, question first; the stop that already has a case says so |
| [crashes-lens-1440](crashes-lens-1440.png), [crashes-lens-390](crashes-lens-390.png) | Crashes opened while a case is in hand: the bar, and *Add this stop* going to that case |
| [record-1440](record-1440.png), [record-open-1440](record-open-1440.png), [record-open-390](record-open-390.png) | The System log, and an open record with *Add this record to a case* |

To take them again, see [The harness](#running-it).

## Decisions and why

**Home, then case, then evidence.** The audit's first cause was a navigation that mirrors the API's reading catalog. Here the rail reads like a file drawer: *Home*, then the open cases by name, then *Readings* as lenses (Crashes first, then the System log, Hardware errors, Leads, Machine, Performance, Space, Diagnostics), then *Agent*. A reading is where evidence comes from, not where an investigation lives.

**One control carries the idea across every view.** Every view already handed evidence on through one component, the stack button, 43 times across the views. It became `AddEvidence`: with a case in hand it adds the observation, or the records picked out of it, as that case's next exhibit; without one it offers the open cases or a new case started from this evidence, titled from what it holds. No view had to learn about cases. This is the subtraction the direction rests on: the global stack's controls, the stack page as the only place a handoff is assembled, and the question "which stack item belongs to which problem" all go, and the capability stays.

**The timeline is the evidence.** An earlier plan had an evidence column and a separate case timeline. They are one list: observed exhibits ordered by the moment they are about, with day markers and named gaps. The order they were added survives as the exhibit number and in the case record, so both questions ("what happened when" and "what did we learn when") are answered without a mode. Interpretation stays off the time axis: a lead that spans Sep 5 to Sep 12 placed at one instant would read as something that happened then.

**Proposals are a review surface, not a feed.** A proposal sits behind a dashed edge in the margin, the only drawn outline on these pages, because it carries a meaning: not evidence yet. It shows the proposer's words as a quotation, its citations in the readout face with a way to open each, what it says it read (a failed input is hatched, never plain text), and what accepting will change ("adds lead 4, class inferred, with its 3 citations, kept below the timeline; it changes no exhibit"). *Compare the citations with a fresh reading* takes each cited reading again and shows what it holds for each cited record (log, ID, source, event, when it was written), flags a time that differs from the cited moment, and says *Not in this reading* when a bounded reading no longer holds it, which does not make the citation false. It confirms the record, never the proposer's label or claim, and never gates *Accept*, because a new reading is not the cited one.

**Route, not author.** One token opens every route today, so the server cannot tell a person from an agent. Items say how they arrived: *in the dashboard* (the session cookie) or *through the API* (the bearer token). The case record repeats that anyone holding the token can use either. The draft refuses to decide a proposal over the bearer route, so "accepted in the dashboard" is true of every accepted lead; it is still not a boundary.

**Inferred stays visibly inferred.** An accepted proposal keeps the proposer's sentence in italic quotation marks, at a quieter size than the records, on the margin's surface rather than an exhibit sheet, below the timeline, with the note that the cited records are the evidence and the sentence is not. Exact codes inside any sentence (0x133, DPC_WATCHDOG_VIOLATION) are set in the readout face so a serif never turns them into "Ox133". The seeded case also holds a declined proposal ("The display driver caused both freezes") with the person's reason, to show the review surface refusing a causal claim.

**Editorial type, mono only for exact values.** Newsreader for what a person titles and reads first (case titles, page titles, section heads, claims), DM Sans for prose, labels and controls, and JetBrains Mono only for record IDs, codes, times and citations. The audit's "one voice for everything" is fixed globally by the shared `.display`, `.label` and readout classes, so the older views change with it. Newsreader is bundled (132 KB with its optical-size axis, OFL) because the dashboard serves everything from the machine and loads nothing from a CDN.

**A semantic channel, used sparingly.** New tokens for *stop* (a coral diamond), *attention* (amber, only for "to review" and inputs not observed), *not observed* (a hatch) and the paper surfaces. Each travels with a shape and a word. Level glyphs now differ by shape and hue (Critical triangle and Error disc in the stop hue, Warning ring in amber, Information a short muted dash), where before Error and Information differed by the size of a dot. Evidence class is one quiet mark before its word: raw a filled dot, derived a half-filled one, inferred a dashed ring, invariant a diamond; the uppercase class badges are gone.

**Separation by space and surface.** Exhibits are sheets on the lit field, the case's margin is a change of surface, the open record row becomes a sheet. On Home the review queue has no frame of its own: the dashed proposals are the frame. Drawn borders in `dashboard/src` fell from 115 to 107 (`grep -rE 'border(-(top|bottom|left|right))?\s*:' --include=*.css`, excluding `none` and `0`); the new Home and Case pages draw five, each carrying a meaning (the proposal edge, the API route chip).

**Two appearances.** Paper (warm off-white, ink, one deep green for focus and live) and the dark green instrument field, kept. *Auto* follows the system; *Paper* and *Field* are kept in this browser only. The mark keeps its instrument colours in both. Every colour in the older views that was already a token follows along; about 18 hard-coded values in older view styles do not yet (see cost). Times are 24-hour everywhere.

**Motion.** An accepted proposal settles into place with one lift and highlight, focus moves to the new lead, and a polite live region says "Accepted as lead 3". A decline moves focus to the next proposal. Under Reduced Motion the highlight is removed and the focus and announcement remain.

**Access.** Every control is a button, link, summary or field with its own name; targets are 44 px (a script found none smaller on the case page); the rail, the menu, *Add evidence*, *Hand off* and the add menu are native disclosures; focus lands on the page title after navigation, on the proposal *Review* opened, on the new lead after an accept. Notes save a moment after typing stops, and the page warns before closing with words unsaved. On a phone the case bar is one line: the case's title, *Back to the case*, and an icon to set it down.

## What it asks of the API

The largest ask of the three directions. The prototype drafts it only in the fixture server ([`docs/screens/fixtures/cases_draft.py`](../../../screens/fixtures/cases_draft.py), tested in `tests/test_screen_fixture.py`), so the security boundary in `sentinel/` is untouched.

| Route | Does |
| --- | --- |
| `GET /api/cases`, `POST /api/cases` | List (open first) and open a case, optionally with its first exhibits |
| `GET`, `PATCH /api/cases/{id}` | The case; change title, notes, open or closed (with a closing note) |
| `POST /api/cases/{id}/evidence` | Add an exhibit; the same records from the same reading twice is `409` whenever they were taken, and a whole reading is a new observation each time |
| `POST /api/cases/{id}/proposals` | Propose: a claim, at least one citation, the readings read. Never enters the evidence |
| `POST /api/cases/{id}/proposals/{pid}/accept`, `.../decline` | Decide once, from the dashboard route (`403` over the bearer token); a decline keeps its reason; deciding twice is `409` |

A closed case refuses new evidence, proposals and decisions with `409` until it is reopened, so nothing waits unseen on a case Home no longer lists.
| `GET /api/cases/{id}/composed` | The case as a handoff: notes, evidence in order, declined proposals |

What a real implementation needs beyond the draft:

- **Exhibits store the envelope, as the stack does.** The draft stores provenance and a display excerpt. The real route should keep the stack's item model per case: the stored reading, `origin` (`taken` by Sentinel or `supplied` by a client), verbosity, redaction, and the composed summary rules. Most of that code exists.
- **Proposals whose citations Sentinel takes itself.** The draft's *what it read* is reported by the proposer. If the proposal route takes each cited reading on arrival, the way `stack_add(take=…)` does, the margin can say what Sentinel observed when the proposal arrived rather than what the proposer claims. That removes most of the need for the per-client credential below before this is useful.
- **Per-case storage with a real concurrency story.** Today the stack is one file behind a cross-process lock, and a damaged file answers `503` for the whole stack. Cases need one file per case (or a small database) so one damaged case never takes the others down, plus a migration of the existing stack into a first case.
- **MCP parity.** `cases_list`, `case_get`, `case_propose`, and the composed case as a resource per case, replacing `stack_add` for agents. An agent should not be able to accept.
- **A credential per client, eventually.** Until then the route is the honest label; a separate design pass should decide whether acceptance must require the dashboard's cookie (it does not in the draft, and a cookie can be minted from the token, so it would not be a boundary anyway).
- **Prompt presets.** The predictive preset names the audit found (*Preventative Oracle* and others) should be renamed or removed with the stack, since agents receive them through `prompts_list`.

## What carrying it across all 17 views costs

Already carried by this branch, because it lives in shared code: the shell and rail, the phone menu and the case bar; the type roles; the class mark; the outcome line, section head, segmented control, open row and moment link restyled; level glyphs; both appearances wherever a view used tokens; and case-aware *Add* in every view that had a stack button.

Still to do, per view:

- **Crashes, System log** (built here): fold the remaining mono key/value readouts in an open stop into prose labels with mono values, as the exhibit does.
- **Hardware errors, Leads (Signals), Machine, Performance, Space, Diagnostics, Reliability history, Changes near stop, Kernel reports, PCIe map, Memory map, Process pressure, Machine overview**: move each one's question and summary above its method line (the audit's disclosure order); give each its own exhibit title and facts when it hands evidence on, as Crashes does for a stop (about a dozen small `stopEvidence`-style helpers); replace its remaining partition borders with space and surface; convert its hard-coded colours (about 18 across the older styles) to tokens so paper renders correctly. Signals needs *Open a case* on each lead.
- **Stack**: retire. It is already out of the rail (reachable at `?view=stack`); its prompt library and composed handoff become *Hand off this case*; its file migrates into a first case.
- **System log**: the list of records before a moment still runs from one day into the next without a day marker, the audit's finding; it needs the same day markers the case timeline has.
- **Agents**: becomes "what agents did here": the proposals received, by route, across cases, and the setup commands below that.

A rough size: two to three focused sessions for the view pass, one for the Stack retirement and migration, and the API work above, which is the long pole and needs its own design review before it leaves the fixture.

## Honest weaknesses

- **Nobody opens a case on a first visit.** The home answers the glance with the machine sentence and the stops, but a person who only wants to look around gets a page that keeps inviting them to open something. Direction A answers that visitor more directly.
- **It depends on the largest API change of the three**, and on storage and concurrency work that the current stack shows is not trivial. Until that exists, this is a prototype over a fixture.
- **Route is weaker than authorship.** A person running `curl` with the token appears as "through the API"; an agent that mints a cookie appears as "in the dashboard". The accept step carries the safety, not the label.
- **A timeline invites causal reading.** Two exhibits 17 seconds apart look related. The gap words and "not a cause" notes help; they do not remove the pull.
- **Bookkeeping.** Cases have to be named, closed and occasionally cleaned up. The close state keeps the list short; nothing yet merges two cases about the same problem or suggests closing a quiet one.
- **The citation compare is only as exact as a citation.** It matches `Log:RecordId` (or the citation's log), shows what it found and flags a differing time, but it confirms that a record exists and what it is, not that the claim about it holds, and a record that rolled out of a bounded window cannot be compared at all. The seeded proposal shows one honest wrinkle: it cites the Sep 5 Kernel-Power 41 at Windows' stop estimate, and the compare says the record itself was written 17 minutes later.
- **No live updates.** A new proposal appears when the case or Home is read again (after any change, or on return); nothing pushes it. The MCP server already has a subscription mechanism for the handoff that a case could reuse.
- **Older views under the new shell** keep their own layouts and some mono-heavy readouts, and a few hard-coded colours look wrong on paper. They are reachable and working, not redesigned.

## Running it

From the worktree root, after `npx vite build` in `dashboard/` (it writes the ignored `sentinel/static`):

```
SYSTEM_SENTINEL_HOME=<scratch> ./.venv/bin/python docs/screens/fixtures/fixture-server.py --port 8043
NODE_PATH=<dir with playwright> TOKEN_FILE=<scratch>/token PORT=8043 OUT=<dir> \
  node docs/screens/fixtures/capture-views.cjs home case case-review case-accepted crashes crashes-lens record record-open
COLOR_SCHEME=light SUFFIX=-light ... node docs/screens/fixtures/capture-views.cjs home case case-review
```

The fixture server seeds one open case with two exhibits, two pending proposals and one declined, and one closed case. `POST /api/cases/_fixture/reseed` puts them back; the `case-accepted` shot uses it before and after accepting. `SENTINEL_FIXTURE_CASES=0` serves the fixture without the draft routes, which is how the dashboard behaves against a server that lacks them: it says the routes are missing rather than showing no cases.

## Review

- **GPT-6 Sol** was asked first, through `multithread peer codex`, and refused before submission twice (the main checkout and this worktree): Multithread's hooks for Codex have not been reviewed in an interactive launch. The friction is recorded. No Sol review informs this note.
- **An Opus 5.5 stand-in** (`multithread peer claude`, high effort, read-only, 211 seconds) reviewed the built branch and its screenshots adversarially. Taken: the review gate was worded as a guarantee it is not, now stated as a convention and made visible; accepted claims came off the time axis into *Leads you accepted*; gaps offer the log between; one exhibit shape per kind (a lead from Leads now matches a lead from Home; WHEA selections with `Log:RecordId` now convert); the same records taken twice are one exhibit; the handoff now carries a lead's citations and the real routes; the compare shows what it found instead of a bare "Found", respects the cited log and flags a differing time; moments are compared as times, not text; closed cases refuse changes; Home shows each proposal's real route; a conflicting decision reads the case again; *Review* lands on the proposal; decline keeps focus; notes save while typing; a server without the routes is reported as such; claims are no longer clamped (they end in their caveat); the double frame around Home's review queue is gone; the phone case bar is one line; 24-hour times; codes in the readout face; Stack out of the rail. Not taken: making C a layer on B rather than a home of its own. That is the choice David is making between directions, and this branch is the strongest version of C to choose against.
- **Buddy** (Muse, through the bridge, about ten minutes) critiqued the build plan before the code existed. Taken: the first exhibit filled when a case is opened from a stop or lead; the not-observed line on its own line; a statement of what accepting changes; the check as optional compare that never gates *Accept*, with wording for records rolled out of a bounded window; both moments on each exhibit (when it happened, when it was added); inferred exhibits set apart from observed ones; the held case marked in the rail. Not taken: an untitled draft case created silently on *Add* without a case in hand. The add menu asks one question (which case, or a new one titled from the evidence) because evidence landing silently in the wrong case is the worse failure.
