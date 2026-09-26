import { useState } from 'react';
import { Cls, EventRecord, Reading, Unauthorized, take } from './api';
import { AlreadyEvidence, CaseSummary, CasesUnavailable, NewExhibit, StopRef, addEvidence, classesOf, listCases, openCase, recordsIn } from './cases';
import { firstLine } from './Outcome';
import { useApp } from './store';
import type { NewItem } from './stack';
import styles from './AddEvidence.module.css';

/** What a view hands over, with what only the view knows: the moment it is about and its facts. */
export type EvidenceItem = NewItem & { moment?: string | null; facts?: [string, string][]; stop?: StopRef; classes?: Cls[] };

/**
 * The one control every view uses to hand evidence on. In direction C that is the case: with a
 * case in hand the reading, or the records picked out of it, become its next exhibit; without one
 * the control offers the open cases or a new case started from this evidence.
 *
 * What is added is the observation as it was read, with its outcome, classes and when it was
 * taken, so a failed reading cannot enter a case disguised as a finding. It answers in place:
 * nothing moves, and the answer distinguishes added, already there and failed.
 */
export function AddEvidence({ item, label = 'Add this reading' }: { item: EvidenceItem; label?: string }) {
  const caseId = useApp((s) => s.caseId);
  const caseTitle = useApp((s) => s.caseTitle);
  const holdCase = useApp((s) => s.holdCase);
  const openCaseView = useApp((s) => s.openCase);
  const setSession = useApp((s) => s.setSession);
  const bumpCases = useApp((s) => s.bumpCases);
  const [state, setState] = useState<{ kind: 'ready' | 'adding' | 'added' | 'duplicate' | 'failed'; n?: number; problem?: string }>({ kind: 'ready' });
  const [choices, setChoices] = useState<CaseSummary[] | 'loading' | 'unavailable' | null>(null);
  const key = signature(item);
  const [seen, setSeen] = useState(key);
  const verb = actionWords(label);

  // A retake offers a new observation even when its parameters and selected IDs are unchanged.
  if (seen !== key) {
    setSeen(key);
    setState({ kind: 'ready' });
  }

  function fail(err: unknown) {
    if (err instanceof Unauthorized) return setSession('closed');
    if (err instanceof AlreadyEvidence) return setState({ kind: 'duplicate', n: err.n });
    setState({ kind: 'failed', problem: err instanceof CasesUnavailable ? err.message : err instanceof Error ? err.message : String(err) });
  }

  async function addTo(id: string) {
    setState({ kind: 'adding' });
    try {
      const updated = await addEvidence(id, await exhibitOf(item));
      holdCase(id, updated.title);
      bumpCases();
      setState({ kind: 'added', n: updated.evidence[updated.evidence.length - 1]?.n });
    } catch (err) { fail(err); }
  }

  async function startCase() {
    setState({ kind: 'adding' });
    try {
      const exhibit = await exhibitOf(item);
      const created = await openCase(exhibit.title, { kind: exhibit.stop ? 'stop' : 'reading', label: exhibit.title, stop: exhibit.stop ?? undefined }, [exhibit]);
      bumpCases();
      openCaseView(created.id);
    } catch (err) { fail(err); }
  }

  async function offer(open: boolean) {
    if (!open || choices === 'loading') return;
    setChoices('loading');
    try { setChoices((await listCases()).filter((c) => c.state === 'open')); } catch (err) {
      if (err instanceof Unauthorized) return setSession('closed');
      setChoices('unavailable');
    }
  }

  if (state.kind === 'added' || state.kind === 'duplicate') {
    return (
      <span className={styles.done} role="status">
        <span className={styles.doneTag} aria-hidden="true" />
        {state.kind === 'added' ? `Exhibit ${state.n ?? ''} in the case` : `Already exhibit ${state.n ?? ''}`}
        {caseId ? <button className={styles.link} onClick={() => openCaseView(caseId)}>Open the case</button> : null}
      </span>
    );
  }

  if (caseId) {
    return (
      <span className={styles.wrap}>
        <button className={styles.button} onClick={() => addTo(caseId)} disabled={state.kind === 'adding'} title={caseTitle ? `Adds to “${caseTitle}”` : undefined}>
          <span className={styles.plus} aria-hidden="true" />{state.kind === 'adding' ? 'Adding…' : verb}
        </button>
        {state.kind === 'failed' ? <span className={styles.problem} role="status">{state.problem}</span> : null}
      </span>
    );
  }

  return (
    <details className={styles.menu} onToggle={(event) => offer(event.currentTarget.open)}>
      <summary className={styles.button}><span className={styles.plus} aria-hidden="true" />{verb} to a case</summary>
      <div className={styles.menuPanel}>
        {choices === 'loading' ? <p className={styles.menuNote}>Finding open cases…</p> : null}
        {choices === 'unavailable' ? <p className={styles.menuNote}>This server does not answer the case routes yet.</p> : null}
        {Array.isArray(choices) ? (
          <ul className={styles.menuList}>
            {choices.map((c) => <li key={c.id}><button className={styles.menuChoice} onClick={() => addTo(c.id)} disabled={state.kind === 'adding'}>{c.title}<span>{c.evidence} {c.evidence === 1 ? 'exhibit' : 'exhibits'}</span></button></li>)}
            <li><button className={`${styles.menuChoice} ${styles.menuNew}`} onClick={startCase} disabled={state.kind === 'adding'}>Start a new case with this</button></li>
          </ul>
        ) : null}
        {state.kind === 'failed' ? <p className={styles.problem} role="status">{state.problem}</p> : null}
      </div>
    </details>
  );
}

/** The view's own words for what it offers, as an action on the case rather than the stack. */
function actionWords(label: string): string {
  return label.replace(/^Stack /, 'Add ').replace(/ to the stack$/, '').replace(/^Add to stack$/, 'Add this reading');
}

/** Turn what a view holds into an exhibit: the observation as read, placed at its moment. */
async function exhibitOf(item: EvidenceItem): Promise<NewExhibit> {
  const empty = { reading: null, params: null, asked_at: null, outcome: null, classes: [], ids: null, moment: null, facts: null, note: null, citations: null };
  if (item.kind === 'note') return { ...empty, kind: 'note', title: item.title ?? 'Note', note: item.note };
  const envelope: Reading = 'envelope' in item ? item.envelope : await take(item.take.name, item.take.params as Record<string, string>);
  const ids = item.kind === 'selection' ? item.ids : null;
  const records = ids ? recordsIn(envelope, ids).sort((a, b) => Date.parse(a.TimeCreated) - Date.parse(b.TimeCreated)) : [];
  const params = envelope.params ?? {};
  const moment = item.moment ?? records[0]?.TimeCreated ?? (typeof params.before === 'string' ? params.before : null);
  return {
    ...empty,
    kind: item.kind,
    title: item.title ?? (records.length === 1 ? recordTitle(records[0]) : `${envelope.reading} reading`),
    reading: envelope.reading,
    params,
    asked_at: envelope.asked_at,
    outcome: envelope.outcome,
    classes: item.classes ?? classesOf(envelope, ids),
    ids,
    moment,
    facts: item.facts ?? factsOf(envelope, records),
    stop: item.stop ?? null,
  };
}

function recordTitle(record: EventRecord): string {
  const source = record.ProviderName.replace(/^Microsoft-Windows-/, '');
  return `${source} ${record.Id}${record.Message ? `: ${firstLine(record.Message)}` : ''}`;
}

function factsOf(envelope: Reading, records: EventRecord[]): [string, string][] {
  if (records.length === 1) {
    const r = records[0];
    return [
      ['Record', `${r.Log ?? 'System'} ${r.RecordId} · ${r.ProviderName} · event ${r.Id}${r.LevelDisplayName ? ` · ${r.LevelDisplayName}` : ''}`],
      ['Written', r.TimeCreated],
      ...(r.Message ? [['Message', firstLine(r.Message)] as [string, string]] : []),
    ];
  }
  if (records.length > 1) return [['Records', String(records.length)], ['First', records[0].TimeCreated], ['Last', records[records.length - 1].TimeCreated]];
  return [['Returned', envelope.count == null ? envelope.outcome : `${envelope.count}`], ['Outcome', envelope.outcome]];
}

/** Reset the control when the observation or selection changes. The server owns duplicate checks. */
function signature(item: NewItem): string {
  if (item.kind === 'note') return `note:${item.note}`;
  const ids = 'ids' in item ? item.ids : null;
  if ('envelope' in item) {
    return JSON.stringify([item.kind, item.envelope.reading, item.envelope.params, ids, item.envelope.asked_at]);
  }
  return JSON.stringify([item.kind, item.take.name, item.take.params, ids]);
}
