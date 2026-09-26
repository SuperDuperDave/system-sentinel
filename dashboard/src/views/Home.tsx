import { useState } from 'react';
import { Reading, Unauthorized, observed } from '../api';
import { CaseSummary, openCase } from '../cases';
import { Lead, StopLike, bugcheckWords, caseForStop, dayOf, lasted, leadExhibit, stopExhibit, stopMoment, stopTitle, timeOf, when } from '../exhibits';
import { ago, duration, part } from '../Sections';
import { useApp } from '../store';
import { useCaseList } from '../useCases';
import { Taken, useReading } from '../useReading';
import type { Snapshot } from './MachineOverview';
import styles from './Home.module.css';

/**
 * Home: what am I looking into, what does the agent propose, and what did it read to propose it?
 *
 * A first visit has no case, so the page must still answer the glance: one sentence of observed
 * facts about the machine (never a verdict), then the places a person would start, each a stop or
 * a lead with its time, and each one step from becoming a case. With cases open, they lead, and
 * the proposals waiting for a person sit beside them as a review queue.
 */
export function Home() {
  const cases = useCaseList();
  const crash = useReading('crash', { count: 5 }, true, { hold: 'same-reading' });
  const signals = useReading('signals', {}, true, { hold: 'same-params' });
  const system = useReading('system', {}, true, { hold: 'same-params' });
  const list = cases.state === 'ok' ? cases.value : [];
  const open = list.filter((c) => c.state === 'open');
  const closed = list.filter((c) => c.state === 'closed');
  const pending = open.flatMap((c) => c.pending.map((p) => ({ ...p, caseId: c.id, caseTitle: c.title })));
  const hasCases = open.length > 0;

  return (
    <section className={styles.home}>
      <header className={styles.head}>
        <h1 className={`${styles.title} display`}>What are you looking into?</h1>
        <MachineLine system={system} crash={crash} signals={signals} />
      </header>

      {hasCases ? (
        <div className={styles.columns}>
          <div className={styles.mainColumn}>
            <OpenCases cases={open} />
            <StartingPoints crash={crash} signals={signals} cases={list} first={false} />
          </div>
          <Review pending={pending} />
        </div>
      ) : <StartingPoints crash={crash} signals={signals} cases={list} first />}

      {cases.state === 'unavailable' ? (
        <p className={styles.unavailable}>This server does not answer the case routes, so cases cannot be kept here. The readings still work; the case routes are a draft this prototype asks of the API.</p>
      ) : null}
      {cases.state === 'failed' ? <p className={styles.unavailable}>The cases could not be read: {cases.problem}</p> : null}

      {closed.length ? (
        <details className={styles.closed}>
          <summary><span className={styles.closedSummary}>Closed cases · {closed.length}</span></summary>
          <ul className={styles.caseList}>{closed.map((c) => <CaseRow key={c.id} c={c} />)}</ul>
        </details>
      ) : null}
    </section>
  );
}

/**
 * One sentence of observed facts: which Windows, how long it has run, the last unplanned stop, and
 * which readings did not answer. Each fact is a reading's answer; a reading that failed says so
 * in its own shape, so a missing answer never reads as a healthy one.
 */
function MachineLine({ system, crash, signals }: { system: Taken<unknown>; crash: Taken<unknown>; signals: Taken<unknown> }) {
  const snapshot = part<Snapshot>(system.reading, 'snapshot');
  const stops = (part<StopLike[]>(crash.reading, 'stops') ?? []).filter((s) => stopMoment(s));
  const latest = stops.map(stopMoment).filter((m): m is string => !!m).sort().pop();
  const inputs = (signals.reading?.method.readings as { name: string; outcome: string }[] | undefined) ?? [];
  const missing = inputs.filter((r) => r.outcome !== 'ok' && r.outcome !== 'empty');
  return (
    <p className={styles.machine}>
      {observed(system.reading) && snapshot ? (
        <>
          <span>{snapshot.os_caption?.replace(/^Microsoft /, '') ?? 'Windows'}{snapshot.os_build ? <>, build <span className="readout">{snapshot.os_build}</span></> : null}</span>
          {snapshot.boot_time ? <span>, running since <span className="readout">{when(snapshot.boot_time)}</span>{snapshot.uptime_seconds != null ? ` (${duration(snapshot.uptime_seconds)})` : ''}.</span> : '. '}
        </>
      ) : <Unanswered taken={system} what="The system snapshot" />}
      {' '}
      {observed(crash.reading) ? (
        latest ? <span>The last unplanned stop Sentinel can place was <span className="readout">{when(latest)}</span>, {ago(latest)}.</span>
          : <span>No unplanned stop was placed in the returned records.</span>
      ) : <Unanswered taken={crash} what="The crash reading" />}
      {' '}
      {missing.length ? (
        <span className={`${styles.gapLine} ${styles.gapBlock}`}>
          <span className={`${styles.gapMark} unobserved`} aria-hidden="true" />
          Not observed on arrival: {missing.map((r) => r.name).join(', ')}. Anything they would show is absent here, not clear.
        </span>
      ) : null}
    </p>
  );
}

function Unanswered({ taken, what }: { taken: Taken<unknown>; what: string }) {
  if (taken.state === 'taking' || taken.state === 'idle') return <span className={styles.pending}>{what}: reading…</span>;
  return <span className={styles.gapLine}><span className={`${styles.gapMark} unobserved`} aria-hidden="true" />{what} was not observed{taken.reading ? ` (${taken.reading.outcome})` : ''}.</span>;
}

function OpenCases({ cases }: { cases: CaseSummary[] }) {
  return (
    <section aria-labelledby="open-cases">
      <h2 id="open-cases" className={styles.sectionTitle}>Open cases</h2>
      <ul className={styles.caseList}>{cases.map((c) => <CaseRow key={c.id} c={c} />)}</ul>
    </section>
  );
}

function CaseRow({ c }: { c: CaseSummary }) {
  const openCaseView = useApp((s) => s.openCase);
  return (
    <li>
      <button className={styles.caseRow} onClick={() => openCaseView(c.id)}>
        <span className={styles.caseTitle}>{c.title}</span>
        <span className={styles.caseMeta}>
          {c.state === 'open' ? `Opened ${ago(c.opened_at)} from ${c.opened_from.label}` : `Closed ${c.closed_at ? ago(c.closed_at) : ''}`}
          {' · '}{c.evidence} {c.evidence === 1 ? 'exhibit' : 'exhibits'}
          {c.pending.length ? <span className={styles.caseReview}> · {c.pending.length} {c.pending.length === 1 ? 'proposal' : 'proposals'} to review</span> : null}
        </span>
      </button>
    </li>
  );
}

/** Proposals from every open case, newest first: the queue a person clears. */
function Review({ pending }: { pending: (CaseSummary['pending'][number] & { caseId: string; caseTitle: string })[] }) {
  const openCaseView = useApp((s) => s.openCase);
  const sorted = [...pending].sort((a, b) => b.received_at.localeCompare(a.received_at));
  function review(caseId: string, id: string) {
    openCaseView(caseId);
    history.replaceState(history.state, '', `${location.pathname}${location.search}#${id}`);
  }
  return (
    <section className={styles.review} aria-labelledby="awaiting-review">
      <h2 id="awaiting-review" className={styles.sectionTitle}>Awaiting your review</h2>
      {sorted.length ? (
        <ul className={styles.proposals}>
          {sorted.map((p) => (
            <li key={p.id} className={styles.proposal}>
              <p className={styles.proposalRoute}>Proposed through the API · {ago(p.received_at)} · for <span className={styles.proposalCase}>{p.caseTitle}</span></p>
              <blockquote className={styles.claim}>{p.claim}</blockquote>
              <p className={styles.proposalBasis}>
                Cites {p.cites} · read {p.read} {p.read === 1 ? 'reading' : 'readings'}
                {p.not_observed ? <> · <span className={styles.warnWord}>{p.not_observed} not observed</span></> : null}
              </p>
              <button className={styles.reviewButton} onClick={() => review(p.caseId, p.id)}>Review</button>
            </li>
          ))}
        </ul>
      ) : <p className={styles.quiet}>Nothing waiting. An agent with the token can propose evidence to an open case; it enters only when you accept it.</p>}
    </section>
  );
}

/**
 * Where a person would start: the stops the crash reading placed and the leads the signals reading
 * noticed, newest first, each with the time it is about. Opening a case from one makes it the
 * first exhibit, so the case starts with evidence rather than an empty page.
 */
function StartingPoints({ crash, signals, cases, first }: { crash: Taken<unknown>; signals: Taken<unknown>; cases: CaseSummary[]; first: boolean }) {
  const stops = (part<StopLike[]>(crash.reading, 'stops') ?? []).filter((s) => stopMoment(s));
  const leads = (part<Lead[]>(signals.reading, 'signals') ?? []).filter((l) => l.class === 'transitions' && !l.id.startsWith('transition:unexpected-shutdown'));
  return (
    <section className={`${styles.start} ${first ? styles.startFirst : ''}`} aria-labelledby="where-to-start">
      <h2 id="where-to-start" className={styles.sectionTitle}>{first ? 'Where to start' : 'Other places to start'}</h2>
      {first ? <p className={styles.startIntro}>A case holds what you find about one problem: the evidence, in the order it happened, your notes, and anything an agent proposes for you to accept. Start one from something the machine recorded.</p> : null}
      <div className={styles.startGrid}>
        <div>
          <h3 className={styles.startHead}>Unplanned stops <span className={styles.startSource}>crash reading{crash.reading ? ` · taken ${timeOf(crash.reading.asked_at, false)}` : ''}</span></h3>
          {!observed(crash.reading) ? <Unanswered taken={crash} what="The crash reading" /> : null}
          {observed(crash.reading) && !stops.length ? <p className={styles.quiet}>No stop was placed in the returned records.</p> : null}
          <ol className={styles.points}>
            {stops.slice(0, 3).map((stop) => <StopPoint key={`${stop.records.start}:${stop.records.power_41}:${stop.reported_at}`} stop={stop} envelope={crash.reading!} cases={cases} />)}
          </ol>
          {stops.length > 3 ? <GoTo view="crashes" words={`All ${stops.length} returned stops in Crashes`} /> : null}
        </div>
        <div>
          <h3 className={styles.startHead}>Leads <span className={styles.startSource}>signals reading · patterns, not diagnoses</span></h3>
          {!observed(signals.reading) ? <Unanswered taken={signals} what="The signals reading" /> : null}
          {observed(signals.reading) && !leads.length ? <p className={styles.quiet}>No lead about stops was noticed in what answered.</p> : null}
          <ol className={styles.points}>
            {leads.slice(0, 3).map((lead) => <LeadPoint key={lead.id} lead={lead} envelope={signals.reading!} />)}
          </ol>
          <GoTo view="signals" words="Every lead, with its rule" />
        </div>
      </div>
    </section>
  );
}

function StopPoint({ stop, envelope, cases }: { stop: StopLike; envelope: Reading; cases: CaseSummary[] }) {
  const existing = caseForStop(cases, stop);
  const moment = stopMoment(stop)!;
  const setCrashesView = useApp((s) => s.setCrashesView);
  const setView = useApp((s) => s.setView);
  return (
    <li className={styles.point}>
      <span className={styles.pointWhen}><span className={styles.stopMark} aria-hidden="true" />{dayOf(moment)} <span className="readout">{timeOf(moment, false)}</span></span>
      <span className={styles.pointWhat}>{stop.stopped_at || stop.started_at ? 'Stopped without shutting down' : 'Stop report filed'}. <span className="readout">{bugcheckWords(stop)}</span>{stop.down_seconds != null ? `. Down ${lasted(stop.down_seconds)}` : ''}.</span>
      <span className={styles.pointActions}>
        {existing ? <OpenExisting c={existing} /> : <StartCase title={stopTitle(stop)} from={{ kind: 'stop', label: `the stop on ${when(moment)}`, stop: stopExhibit(stop, envelope).stop ?? undefined }} exhibit={() => stopExhibit(stop, envelope)} />}
        <button className={styles.quietButton} onClick={() => { setCrashesView({ stopId: stop.records.start != null ? `start:${stop.records.start}:${stop.started_at}` : `power:${stop.records.power_41}:${stop.announced_at}`, focus: 'stop' }); setView('crashes'); }}>See it in Crashes</button>
      </span>
    </li>
  );
}

function LeadPoint({ lead, envelope }: { lead: Lead; envelope: Reading }) {
  const setSignalId = useApp((s) => s.setSignalId);
  const setView = useApp((s) => s.setView);
  return (
    <li className={styles.point}>
      <span className={styles.pointWhen}><span className={styles.leadMark} aria-hidden="true" />Lead</span>
      <span className={styles.pointWhat}>{lead.title}.</span>
      <span className={styles.pointActions}>
        <StartCase title={lead.title} from={{ kind: 'lead', label: `the lead “${lead.title}”` }} exhibit={() => leadExhibit(lead, envelope)} />
        <button className={styles.quietButton} onClick={() => { setSignalId(lead.id); setView('signals'); }}>Its rule</button>
      </span>
    </li>
  );
}

function OpenExisting({ c }: { c: CaseSummary }) {
  const openCaseView = useApp((s) => s.openCase);
  return <button className={styles.inCase} onClick={() => openCaseView(c.id)}>In the case <span>{c.title}</span></button>;
}

function StartCase({ title, from, exhibit }: { title: string; from: Parameters<typeof openCase>[1]; exhibit: () => ReturnType<typeof stopExhibit> }) {
  const openCaseView = useApp((s) => s.openCase);
  const bumpCases = useApp((s) => s.bumpCases);
  const setSession = useApp((s) => s.setSession);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function start() {
    setBusy(true);
    try {
      const created = await openCase(title, from, [exhibit()]);
      bumpCases();
      openCaseView(created.id);
    } catch (error) {
      if (error instanceof Unauthorized) { setSession('closed'); return; }
      setProblem(error instanceof Error ? error.message : String(error));
      setBusy(false);
    }
  }
  return <>
    <button className={styles.startButton} onClick={start} disabled={busy}>{busy ? 'Opening…' : 'Open a case'}</button>
    {problem ? <span className={styles.quiet} role="status">{problem}</span> : null}
  </>;
}

function GoTo({ view, words }: { view: 'crashes' | 'signals'; words: string }) {
  const setView = useApp((s) => s.setView);
  return <button className={styles.goTo} onClick={() => setView(view)}>{words}<span aria-hidden="true"> →</span></button>;
}
