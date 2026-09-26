import { useEffect, useRef, useState } from 'react';
import { Cls, Unauthorized, observed, take } from '../api';
import { Case, Citation, Exhibit, Proposal, Route, composedCase, containsIds, decide, patchCase } from '../cases';
import { CopyButton } from '../Copy';
import { dayLong, gap, isIso, timeOf, when } from '../exhibits';
import { ago } from '../Sections';
import { useApp, ViewId } from '../store';
import { useCase } from '../useCases';
import styles from './Case.module.css';

const ROUTE_WORDS: Record<Route, string> = { dashboard: 'in the dashboard', api: 'through the API' };
const LENSES: { id: ViewId; label: string; offers: string }[] = [
  { id: 'crashes', label: 'Crashes', offers: 'stops, program faults, dumps' },
  { id: 'record', label: 'System log', offers: 'records, the record before a moment' },
  { id: 'errors', label: 'Hardware errors', offers: 'WHEA records and bursts' },
  { id: 'signals', label: 'Leads', offers: 'patterns with their rules' },
  { id: 'machine', label: 'Machine', offers: 'parts, drivers, firmware' },
  { id: 'performance', label: 'Performance', offers: 'who used the machine, when' },
  { id: 'space', label: 'Space', offers: 'what fills the disk' },
  { id: 'diagnostics', label: 'Diagnostics', offers: 'PCIe, power, memory on request' },
];

/**
 * A case: one investigation a person keeps. Its evidence is set in the order it happened, which
 * is the case's own timeline; beside it, in the margin, whatever was proposed and not yet
 * accepted, and the record of how the case was built, by route.
 *
 * Nothing proposed is evidence until a person accepts it here. Accepting keeps the proposer's
 * words as an inferred exhibit with its citations, so a claim never turns into a record.
 */
export function CaseView() {
  const caseId = useApp((s) => s.caseId);
  const holdCase = useApp((s) => s.holdCase);
  const loaded = useCase(caseId);
  const [settled, setSettled] = useState<string | null>(null);
  const [announce, setAnnounce] = useState('');
  const [shown, setShown] = useState<Case | null>(null);
  const current = shown?.id === caseId ? shown : loaded.state === 'ok' ? loaded.value : null;

  useEffect(() => { if (loaded.state === 'ok') { setShown(loaded.value); holdCase(loaded.value.id, loaded.value.title); } }, [loaded, holdCase]);

  if (!current) {
    return (
      <section>
        <h1 className={`${styles.title} display`}>{loaded.state === 'loading' ? 'Opening the case…' : 'This case could not be opened'}</h1>
        {loaded.state === 'unavailable' ? <p className={styles.lede}>This server does not answer the case routes. They are a draft this prototype asks of the API.</p> : null}
        {loaded.state === 'failed' ? <p className={styles.lede}>{loaded.problem}</p> : null}
      </section>
    );
  }

  const pending = current.proposals.filter((p) => p.state === 'pending');
  function updated(next: Case, note?: string, newest?: string) {
    setShown(next);
    if (note) setAnnounce(note);
    if (newest) setSettled(newest);
  }

  return (
    <article className={styles.case}>
      <CaseHead c={current} onChange={updated} pending={pending.length} />
      <p className={styles.live} role="status" aria-live="polite">{announce}</p>
      <div className={styles.grid}>
        <Evidence c={current} settled={settled} />
        <aside className={styles.margin} aria-label="Proposals and the case record">
          <section id="proposals" aria-labelledby="proposals-title">
            <h2 id="proposals-title" className={styles.h2}>Proposed, not yet evidence</h2>
            {pending.length ? pending.map((p) => <ProposalCard key={p.id} c={current} p={p} onDecided={updated} />)
              : <p className={styles.quiet}>Nothing waiting. Anything that holds this machine’s token can propose to this case through the API; it waits here until you decide.</p>}
          </section>
          <CaseRecord c={current} />
        </aside>
      </div>
    </article>
  );
}

function CaseHead({ c, pending, onChange }: { c: Case; pending: number; onChange: (next: Case, note?: string) => void }) {
  const setView = useApp((s) => s.setView);
  const bumpCases = useApp((s) => s.bumpCases);
  const setSession = useApp((s) => s.setSession);
  const [renaming, setRenaming] = useState(false);
  const [closing, setClosing] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const title = useRef<HTMLInputElement>(null);
  const closingNote = useRef<HTMLTextAreaElement>(null);

  async function change(patch: Parameters<typeof patchCase>[1], note?: string) {
    try {
      onChange(await patchCase(c.id, patch), note);
      bumpCases();
      setProblem(null);
    } catch (error) {
      if (error instanceof Unauthorized) { setSession('closed'); return; }
      setProblem(error instanceof Error ? error.message : String(error));
    }
  }

  return (
    <header className={styles.head}>
      <p className={styles.kicker}>
        <span className={c.state === 'open' ? styles.stateOpen : styles.stateClosed}>{c.state === 'open' ? 'Open case' : 'Closed case'}</span>
        <span>Opened {when(c.opened_at)} {ROUTE_WORDS[c.route]}, from {c.opened_from.label}</span>
      </p>
      {renaming ? (
        <form className={styles.rename} onSubmit={(event) => { event.preventDefault(); setRenaming(false); void change({ title: title.current?.value ?? c.title }); }}>
          <label className="label" htmlFor="case-title">Case title</label>
          <input id="case-title" ref={title} className={`${styles.titleInput} display`} defaultValue={c.title} autoComplete="off" />
          <span className={styles.row}><button className={styles.primary} type="submit">Save title</button><button className={styles.secondary} type="button" onClick={() => setRenaming(false)}>Cancel</button></span>
        </form>
      ) : <h1 className={`${styles.title} display`}>{c.title}</h1>}
      {c.state === 'closed' ? <p className={styles.closedNote}>Closed {c.closed_at ? when(c.closed_at) : ''}{c.closing_note ? <>: <q>{c.closing_note}</q></> : '.'}</p> : null}
      {pending ? <a className={styles.jump} href="#proposals">{pending} {pending === 1 ? 'proposal waits' : 'proposals wait'} for your review</a> : null}

      <Notes c={c} onChange={onChange} />

      <div className={styles.actions}>
        {c.state === 'open' ? (
          <details className={styles.addEvidence}>
            <summary className={styles.primary}>Add evidence</summary>
            <div className={styles.lensPanel}>
              <p className={styles.lensNote}>Open a reading as a lens. While this case is in hand, every “Add” in that reading adds here.</p>
              <ul className={styles.lensList}>
                {LENSES.map((lens) => <li key={lens.id}><button className={styles.lens} onClick={() => setView(lens.id)}><span>{lens.label}</span><span className={styles.lensOffers}>{lens.offers}</span></button></li>)}
              </ul>
            </div>
          </details>
        ) : null}
        <Handoff c={c} />
        {!renaming ? <button className={styles.secondary} onClick={() => setRenaming(true)}>Rename</button> : null}
        {c.state === 'open' && !closing ? <button className={styles.secondary} onClick={() => setClosing(true)}>Close the case</button> : null}
        {c.state === 'closed' ? <button className={styles.secondary} onClick={() => change({ state: 'open' }, 'The case is open again.')}>Reopen</button> : null}
      </div>
      {closing ? (
        <form className={styles.closeForm} onSubmit={(event) => { event.preventDefault(); setClosing(false); void change({ state: 'closed', closing_note: closingNote.current?.value ?? '' }, 'The case is closed.'); }}>
          <label className="label" htmlFor="closing-note">What you concluded, in your words (optional)</label>
          <textarea id="closing-note" ref={closingNote} rows={2} className={styles.textarea} />
          <span className={styles.row}><button className={styles.primary} type="submit">Close the case</button><button className={styles.secondary} type="button" onClick={() => setClosing(false)}>Keep it open</button></span>
        </form>
      ) : null}
      {problem ? <p className={styles.problem} role="status">{problem}</p> : null}
    </header>
  );
}

/** The person's own words: authored input, set apart from every exhibit, saved when they leave the field. */
function Notes({ c, onChange }: { c: Case; onChange: (next: Case) => void }) {
  const setSession = useApp((s) => s.setSession);
  const [draft, setDraft] = useState(c.notes);
  const [state, setState] = useState<'saved' | 'saving' | 'unsaved' | 'failed'>('saved');
  const [seen, setSeen] = useState(c.id);
  if (seen !== c.id) { setSeen(c.id); setDraft(c.notes); setState('saved'); }

  async function save() {
    if (draft === c.notes) return;
    setState('saving');
    try { onChange(await patchCase(c.id, { notes: draft })); setState('saved'); } catch (error) {
      if (error instanceof Unauthorized) { setSession('closed'); return; }
      setState('failed');
    }
  }

  return (
    <div className={styles.notes}>
      <label className={styles.notesLabel} htmlFor="case-notes">Your notes <span>{state === 'saving' ? 'saving…' : state === 'unsaved' ? 'not saved yet' : state === 'failed' ? 'could not save; your text is still here' : c.notes_updated_at ? `saved ${ago(c.notes_updated_at)}` : 'nothing written yet'}</span></label>
      <textarea id="case-notes" className={styles.notesText} value={draft} rows={3} readOnly={c.state === 'closed'}
        placeholder="What happened, in your words. What you have ruled out."
        onChange={(event) => { setDraft(event.target.value); setState('unsaved'); }} onBlur={save} />
    </div>
  );
}

function Handoff({ c }: { c: Case }) {
  const [text, setText] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const held = useRef<HTMLPreElement>(null);
  return (
    <details className={styles.handoff} onToggle={(event) => {
      if (!event.currentTarget.open) return;
      composedCase(c.id).then(setText, (error: unknown) => setProblem(error instanceof Error ? error.message : String(error)));
    }}>
      <summary className={styles.secondary}>Hand off this case</summary>
      <div className={styles.handoffPanel}>
        <p className={styles.lensNote}>The case as text for an agent or a person: the evidence in order, your notes, and the proposals you declined. Nothing leaves this machine unless you send it.</p>
        {problem ? <p className={styles.problem}>{problem}</p> : null}
        {text ? <><CopyButton text={text} selectRef={held} label="Copy the case" /><pre ref={held} className={`${styles.handoffText} readout`}>{text}</pre></> : problem ? null : <p className={styles.quiet}>Composing…</p>}
      </div>
    </details>
  );
}

/** The case's timeline: exhibits by the moment they are about, days marked, gaps named. */
function Evidence({ c, settled }: { c: Case; settled: string | null }) {
  const placed = c.evidence.filter((e) => e.moment).sort((a, b) => Date.parse(a.moment!) - Date.parse(b.moment!));
  const unplaced = c.evidence.filter((e) => !e.moment);
  return (
    <section className={styles.evidence} aria-labelledby="evidence-title">
      <h2 id="evidence-title" className={styles.h2}>Evidence, in the order it happened</h2>
      {!c.evidence.length ? <p className={styles.quiet}>No evidence yet. Add a reading from a lens, or accept a proposal.</p> : null}
      <ol className={styles.timeline}>
        {placed.map((exhibit, index) => {
          const day = dayLong(exhibit.moment!);
          const newDay = index === 0 || day !== dayLong(placed[index - 1].moment!);
          const between = index > 0 ? gap(placed[index - 1].moment!, exhibit.moment!) : null;
          return (
            <li key={exhibit.id} className={styles.entry}>
              {newDay ? <p className={styles.day}>{day}{between && index > 0 ? <span> · {between}</span> : null}</p>
                : between ? <p className={styles.gap}>{between}</p> : null}
              <p className={`${styles.at} readout`}>{timeOf(exhibit.moment!)}<span className={styles.atWord}>{exhibit.kind === 'claim' ? 'latest cited moment' : 'when it happened'}</span></p>
              <ExhibitCard exhibit={exhibit} c={c} settled={settled === exhibit.id} />
            </li>
          );
        })}
      </ol>
      {unplaced.length ? (
        <>
          <p className={styles.day}>Not placed in time<span> · readings about a span, not a moment</span></p>
          <ol className={styles.timeline}>{unplaced.map((exhibit) => <li key={exhibit.id} className={styles.entry}><ExhibitCard exhibit={exhibit} c={c} settled={settled === exhibit.id} /></li>)}</ol>
        </>
      ) : null}
    </section>
  );
}

function ClassMarks({ classes }: { classes: Cls[] }) {
  if (!classes.length) return null;
  return <>{classes.map((cls) => <span key={cls} className="cls" data-cls={cls}>{cls}</span>)}</>;
}

/** An exhibit: its tag says which reading, what class, when it was taken; its body says what it holds. */
function ExhibitCard({ exhibit, c, settled }: { exhibit: Exhibit; c: Case; settled: boolean }) {
  const ref = useRef<HTMLElement>(null);
  const proposal = exhibit.accepted_from ? c.proposals.find((p) => p.id === exhibit.accepted_from) : null;
  useEffect(() => {
    if (!settled || !ref.current) return;
    ref.current.focus({ preventScroll: true });
    ref.current.scrollIntoView({ block: 'nearest', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  }, [settled]);
  const claim = exhibit.kind === 'claim';
  const moments = (exhibit.citations ?? []).map((cite) => cite.moment).filter((m): m is string => !!m).sort();
  return (
    <article ref={ref} id={exhibit.id} tabIndex={-1} className={`${styles.exhibit} ${claim ? styles.exhibitClaim : ''} ${settled ? styles.settled : ''}`} aria-labelledby={`${exhibit.id}-title`}>
      <p className={styles.tag}>
        <span className={styles.tagHole} aria-hidden="true" />
        <span className={styles.tagN}>Exhibit {exhibit.n}</span>
        {exhibit.reading ? <span className="readout">{exhibit.reading}</span> : <span>{claim ? 'a proposal' : exhibit.kind}</span>}
        <ClassMarks classes={exhibit.classes} />
        {exhibit.asked_at ? <span>taken {when(exhibit.asked_at)}</span> : null}
        {exhibit.outcome && exhibit.outcome !== 'ok' && exhibit.outcome !== 'empty' ? <span className={`${styles.tagMissing} unobserved`}>not observed: {exhibit.outcome}</span> : null}
        {exhibit.outcome === 'empty' ? <span>answered empty</span> : null}
      </p>
      {claim ? (
        <blockquote id={`${exhibit.id}-title`} className={styles.exhibitClaimText}>{exhibit.title}</blockquote>
      ) : (
        <h3 id={`${exhibit.id}-title`} className={styles.exhibitTitle}>
          {exhibit.stop ? <span className={styles.stopMark} aria-hidden="true" /> : null}{exhibit.title}
        </h3>
      )}
      {claim ? <p className={styles.claimNote}>The proposer’s words, accepted as a lead. The cited records below are the evidence; the sentence is not.{moments.length > 1 ? ` It spans ${when(moments[0])} to ${when(moments[moments.length - 1])}.` : ''}</p> : null}
      {exhibit.facts?.length ? (
        <dl className={styles.facts}>
          {exhibit.facts.map(([key, value]) => (
            <div key={key} className={styles.fact}><dt>{key}</dt><dd className={valueClass(key, value)}>{isIso(value) ? when(value, true) : value}</dd></div>
          ))}
        </dl>
      ) : null}
      {exhibit.citations?.length ? <Citations cites={exhibit.citations} /> : null}
      <footer className={styles.exhibitFoot}>
        <span className={styles.route}>
          {proposal ? <>Proposed {ROUTE_WORDS[proposal.route]} {when(proposal.received_at)}, accepted {ROUTE_WORDS[exhibit.route]} {when(exhibit.added_at)}</> : <>Added {ROUTE_WORDS[exhibit.route]} {when(exhibit.added_at)}</>}
        </span>
        <GoToEvidence stop={exhibit.stop} moment={exhibit.moment} />
      </footer>
    </article>
  );
}

/** Mono for exact values (records, codes, times); the case's own prose stays in the text face. */
function valueClass(key: string, value: string): string {
  if (/^(Not recorded|None matched|Bug check status unknown|No bug check recorded)$/.test(value)) return styles.absent;
  if (isIso(value) || /^(Record|Bug check|Dump|Records|Down for|Last System record)/.test(key)) return `${styles.exact} readout`;
  return styles.prose;
}

function Citations({ cites }: { cites: Citation[] }) {
  return (
    <ul className={styles.cites} aria-label="Citations">
      {cites.map((cite, index) => (
        <li key={index} className={styles.cite}>
          <span className={styles.citeLabel}>{cite.label}</span>
          <span className={`${styles.citeRef} readout`}>
            {cite.reading}{cite.ids?.length ? ` · record ${cite.ids.join(', ')}` : ''}{cite.signal ? ` · ${cite.signal}` : ''}{cite.moment ? ` · ${when(cite.moment, true)}` : ''}
          </span>
          <GoToEvidence stop={cite.stop} moment={cite.moment} signal={cite.signal} compact />
        </li>
      ))}
    </ul>
  );
}

/** Where a piece of evidence can be read in full: Crashes for a stop, the log around a moment, Leads for a lead. */
function GoToEvidence({ stop, moment, signal, compact = false }: { stop?: Exhibit['stop']; moment?: string | null; signal?: string; compact?: boolean }) {
  const setCrashesView = useApp((s) => s.setCrashesView);
  const setView = useApp((s) => s.setView);
  const setMoment = useApp((s) => s.setMoment);
  const setSignalId = useApp((s) => s.setSignalId);
  if (stop) {
    return <button className={styles.go} onClick={() => { setCrashesView({ stopId: stop.records.start != null ? `start:${stop.records.start}:${stop.started_at}` : null, focus: 'stop' }); setView('crashes'); }}>{compact ? 'Open' : 'Open in Crashes'}</button>;
  }
  if (signal) return <button className={styles.go} onClick={() => { setSignalId(signal); setView('signals'); }}>{compact ? 'Open' : 'Open the lead'}</button>;
  if (moment) return <button className={styles.go} onClick={() => setMoment(moment)}>{compact ? 'Log' : 'The log around this moment'}</button>;
  return null;
}

/**
 * A proposal under review: the proposer's words set apart, what it cites, what it says it read,
 * and a check a person can run without trusting it. Accept enters it as an inferred exhibit;
 * decline keeps it, with the person's reason, in the case record.
 */
function ProposalCard({ c, p, onDecided }: { c: Case; p: Proposal; onDecided: (next: Case, note?: string, newest?: string) => void }) {
  const bumpCases = useApp((s) => s.bumpCases);
  const setSession = useApp((s) => s.setSession);
  const [declining, setDeclining] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const reason = useRef<HTMLTextAreaElement>(null);

  async function choose(decision: 'accept' | 'decline') {
    setBusy(true);
    try {
      const next = await decide(c.id, p.id, decision, decision === 'decline' ? reason.current?.value : undefined);
      const added = next.evidence.find((e) => e.accepted_from === p.id);
      bumpCases();
      onDecided(next, decision === 'accept' ? `Accepted as exhibit ${added?.n ?? ''}.` : 'Declined. The proposal and your reason stay in the case record.', added?.id);
    } catch (error) {
      if (error instanceof Unauthorized) { setSession('closed'); return; }
      setProblem(error instanceof Error ? error.message : String(error));
      setBusy(false);
    }
  }

  return (
    <article id={p.id} className={styles.proposal} aria-labelledby={`${p.id}-claim`} tabIndex={-1}>
      <p className={styles.proposalRoute}><span className={styles.pendingMark} aria-hidden="true" />Proposed {ROUTE_WORDS[p.route]} · {when(p.received_at)} · {ago(p.received_at)}</p>
      <blockquote id={`${p.id}-claim`} className={styles.proposalClaim}>{p.claim}</blockquote>
      <h3 className={styles.h3}>What it cites</h3>
      <Citations cites={p.cites} />
      <h3 className={styles.h3}>What it says it read</h3>
      <ul className={styles.reads}>
        {p.read.map((r, index) => (
          <li key={index} className={styles.read}>
            <span className="readout">{r.reading}</span>
            <span className={r.outcome === 'ok' || r.outcome === 'empty' ? styles.readOk : `${styles.readMissing} unobserved`}>{r.outcome === 'ok' ? 'answered' : r.outcome === 'empty' ? 'answered, empty' : `not observed: ${r.outcome}`}</span>
            <span className={styles.readWhen}>taken {when(r.asked_at)}</span>
            {r.inputs ? (
              <span className={styles.inputs}>drew on {Object.entries(r.inputs).map(([name, outcome], i) => (
                <span key={name}>{i ? ', ' : ''}<span className="readout">{name}</span>{outcome === 'ok' ? '' : <span className={styles.warnWord}> ({outcome})</span>}</span>
              ))}</span>
            ) : null}
          </li>
        ))}
      </ul>
      <p className={styles.provenance}>Reported by the proposer. One token opens every route today, so the server cannot yet confirm which client read what. Check the citations yourself:</p>
      <CheckCitations cites={p.cites} />
      <p className={styles.consequence}>Accepting adds exhibit {c.evidence.length + 1}: these words as an inferred lead with its {p.cites.length} {p.cites.length === 1 ? 'citation' : 'citations'}, placed at {latestMoment(p.cites) ? when(latestMoment(p.cites)) : 'no moment'}. It changes no other exhibit and closes nothing.</p>
      {problem ? <p className={styles.problem} role="status">{problem}</p> : null}
      {declining ? (
        <form className={styles.declineForm} onSubmit={(event) => { event.preventDefault(); void choose('decline'); }}>
          <label className="label" htmlFor={`${p.id}-reason`}>Why not (kept in the case record)</label>
          <textarea id={`${p.id}-reason`} ref={reason} rows={2} className={styles.textarea} />
          <span className={styles.row}><button className={styles.secondary} type="submit" disabled={busy}>Decline</button><button className={styles.quietButton} type="button" onClick={() => setDeclining(false)}>Back</button></span>
        </form>
      ) : (
        <span className={styles.row}>
          <button className={styles.primary} onClick={() => choose('accept')} disabled={busy || c.state !== 'open'}>Accept into evidence</button>
          <button className={styles.secondary} onClick={() => setDeclining(true)} disabled={busy || c.state !== 'open'}>Decline…</button>
        </span>
      )}
    </article>
  );
}

function latestMoment(cites: Citation[]): string | null {
  const moments = cites.map((cite) => cite.moment).filter((m): m is string => !!m).sort();
  return moments[moments.length - 1] ?? null;
}

type Check = { label: string; state: 'checking' | 'found' | 'missing' | 'unobserved' | 'unchecked'; detail: string };

/** Take each cited reading again and look for what it cites. A check, not a verdict on the claim. */
function CheckCitations({ cites }: { cites: Citation[] }) {
  const [checks, setChecks] = useState<Check[] | null>(null);
  async function run() {
    setChecks(cites.map((cite) => ({ label: cite.label, state: 'checking', detail: '' })));
    const results = await Promise.all(cites.map(async (cite): Promise<Check> => {
      if (!cite.ids?.length && !cite.signal) return { label: cite.label, state: 'unchecked', detail: 'Nothing exact to look for' };
      try {
        const reading = await take(cite.reading, cite.params as Record<string, string>);
        const at = when(reading.asked_at, true);
        if (!observed(reading)) return { label: cite.label, state: 'unobserved', detail: `${cite.reading} was not observed (${reading.outcome}) at ${at}` };
        const found = cite.signal
          ? reading.sections.some((s) => Array.isArray(s.data) && (s.data as { id?: string }[]).some((item) => item.id === cite.signal))
          : containsIds(reading.sections.map((s) => s.data), cite.ids!);
        return { label: cite.label, state: found ? 'found' : 'missing', detail: found ? `Present in ${cite.reading} taken ${at}` : `Not in ${cite.reading} taken ${at}. A bounded reading can roll a record out of its window; this does not make the citation false.` };
      } catch (error) {
        return { label: cite.label, state: 'unobserved', detail: error instanceof Error ? error.message : String(error) };
      }
    }));
    setChecks(results);
  }
  return (
    <div className={styles.check}>
      <button className={styles.quietButton} onClick={run} disabled={checks?.some((c) => c.state === 'checking')}>{checks ? 'Check again' : 'Check the citations against a fresh reading'}</button>
      {checks ? (
        <ul className={styles.checks} aria-live="polite">
          {checks.map((check, index) => (
            <li key={index} className={styles[`check_${check.state}`]}>
              <span className={styles.checkWord}>{check.state === 'checking' ? 'Checking' : check.state === 'found' ? 'Found' : check.state === 'missing' ? 'Not found' : check.state === 'unobserved' ? 'Not observed' : 'Not checkable'}</span>
              <span>{check.label}</span>
              {check.detail ? <span className={styles.checkDetail}>{check.detail}</span> : null}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/** How the case was built: every arrival and decision with its route. Declined proposals keep their reason. */
function CaseRecord({ c }: { c: Case }) {
  const declined = c.proposals.filter((p) => p.state === 'declined');
  return (
    <section className={styles.record} aria-labelledby="record-title">
      <h2 id="record-title" className={styles.h2}>How this case was built</h2>
      <ol className={styles.ledger}>
        {[...c.record].reverse().map((entry, index) => (
          <li key={index} className={styles.ledgerEntry}>
            <span className={entry.route === 'api' ? styles.routeApi : styles.routeDashboard}>{entry.route === 'api' ? 'API' : 'Dashboard'}</span>
            <span className={styles.ledgerWhat}>{entry.what}</span>
            <span className={styles.ledgerWhen}>{when(entry.at)}</span>
          </li>
        ))}
      </ol>
      {declined.length ? (
        <details className={styles.declined}>
          <summary>Declined proposals · {declined.length}</summary>
          {declined.map((p) => (
            <div key={p.id} className={styles.declinedItem}>
              <blockquote className={styles.declinedClaim}>{p.claim}</blockquote>
              <p className={styles.declinedReason}>Declined {p.decided_at ? when(p.decided_at) : ''}: {p.decline_reason ?? 'no reason given'}</p>
            </div>
          ))}
        </details>
      ) : null}
      <p className={styles.provenance}>Routes, not authors: “API” means the item arrived with the bearer token, “Dashboard” with this browser’s session. Anyone holding the token can use either.</p>
    </section>
  );
}
