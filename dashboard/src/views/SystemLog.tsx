import { Fragment, useState } from 'react';
import { EventRecord, Reading, observed } from '../api';
import { part } from '../Sections';
import { AddToStack } from '../AddToStack';
import { Selection, useApp } from '../store';
import { Mark, Track, TrackId, firstLine, shortProvider, useTracks } from '../timeline';
import { fmt, iso, startOfDay } from '../time';
import { useReading } from '../useReading';
import { Chart } from '../chart/Chart';
import { Inspector } from '../chart/Inspector';
import { AxisPage, usePhone } from '../chart/Page';
import { count } from '../chart/words';
import summaryStyles from '../chart/Summary.module.css';
import styles from './SystemLog.module.css';

const TRACKS: TrackId[] = ['log', 'stops'];
const LEVEL = ['Critical', 'Error', 'Warning', 'Information'] as const;
const PAGE = 100;
const AROUND = 25;

/**
 * The System log on the time axis. Its density by level over the shared range, then its records:
 * the range's records by day, or, once a moment is picked, one continuous list around it: the
 * records before the moment, a rule for the moment, and the records after it.
 *
 * Opening a record inspects it in the sheet; the list keeps its place. Only "the records around
 * this" moves the list, and it says so.
 */
export function SystemLog() {
  const range = useApp((s) => s.range);
  const selection = useApp((s) => s.selection);
  const phone = usePhone();
  const tracks = useTracks(range, TRACKS);
  const log = tracks[0];

  // The list is framed on the last moment picked outside the list; opening one of its own rows
  // inspects that row and leaves the frame where it is.
  const [anchor, setAnchor] = useState<number | null>(() => anchorOf(selection));
  const [seen, setSeen] = useState(selection);
  // How far the frame reaches each way; a new anchor starts a new frame.
  const [depth, setDepth] = useState({ before: AROUND, after: AROUND });
  if (selection !== seen) {
    setSeen(selection);
    const next = !selection ? null
      : selection.kind !== 'stretch' && !(selection.kind === 'mark' && selection.id.startsWith('rec:')) ? anchorOf(selection) : anchor;
    if (next !== anchor) { setAnchor(next); setDepth({ before: AROUND, after: AROUND }); }
  }

  const before = useReading<EventRecord[]>('record', { before: anchor ? iso(anchor) : '', count: depth.before }, anchor !== null, { hold: 'same-params' });
  const after = useReading<EventRecord[]>('events', { log: 'System', levels: [], count: depth.after, since: anchor ? iso(anchor) : '', order: 'oldest' }, anchor !== null, { hold: 'same-params' });
  const known = [...(part<EventRecord[]>(log.taken.reading, 'records') ?? []), ...(part<EventRecord[]>(before.reading, 'records') ?? []), ...(part<EventRecord[]>(after.reading, 'records') ?? [])];

  const resolve = (id: string): Mark | null => {
    const record = known.find((r) => `rec:${r.RecordId}` === id);
    return record ? recordMark(record) : null;
  };
  const envelopeOf = (record: EventRecord): Reading | null => [log.taken.reading, before.reading, after.reading]
    .find((r) => (part<EventRecord[]>(r, 'records') ?? []).some((x) => x.RecordId === record.RecordId)) ?? null;

  return (
    <AxisPage
      title="System log"
      question="What Windows wrote in its System log, and when. A record alone does not establish a cause."
      summary={<p className={summaryStyles.single}>{logSentence(log)}</p>}
      chart={phone ? null : <Chart range={range} tracks={tracks} label="The System log by level, with the stops beside it" />}
      inspector={<Inspector tracks={tracks} range={range} sheet={phone} resolve={resolve}
        detail={(mark) => mark.record ? <RecordFields record={mark.record} envelope={envelopeOf(mark.record)} /> : null} />}
      list={anchor !== null
        ? <Around key={anchor} at={anchor} before={part<EventRecord[]>(before.reading, 'records')} after={part<EventRecord[]>(after.reading, 'records')}
            depth={depth} onDeeper={(side) => setDepth((d) => ({ ...d, [side]: Math.min(2000, d[side] * 2) }))}
            state={observed(before.reading) && observed(after.reading) ? 'read' : before.state === 'lost' || after.state === 'lost' || (before.state === 'done' && after.state === 'done') ? 'failed' : 'taking'}
            onClear={() => { setAnchor(null); }} />
        : <InRange track={log} />}
    />
  );
}

function anchorOf(selection: Selection | null): number | null {
  if (!selection || selection.kind === 'stretch') return null;
  if (selection.kind === 'mark' && selection.id.startsWith('rec:')) return null;
  const t = Date.parse(selection.at);
  return Number.isFinite(t) ? t : null;
}

function logSentence(log: Track): string {
  if (log.reach.state === 'failed') return 'The System log could not be read for this range.';
  if (log.reach.state !== 'read') return 'Reading the System log…';
  const levels = log.bins.reduce((acc, b) => { b.levels?.forEach((n, i) => { acc[i] += n; }); return acc; }, [0, 0, 0, 0]);
  const parts = levels.map((n, i) => (n ? `${n.toLocaleString()} ${LEVEL[i].toLowerCase()}` : '')).filter(Boolean);
  const first = log.reach.spans[0]?.[0];
  const reach = first !== undefined ? ` Read from ${fmt.when(first)}.` : '';
  return `${count(log.total ?? 0, ['record', 'records'])} returned in this range${parts.length ? `: ${parts.join(', ')}` : ''}.${reach}${log.reach.note ? ` ${log.reach.note}` : ''}`;
}

export function recordMark(record: EventRecord): Mark {
  const at = Date.parse(record.TimeCreated);
  return { id: `rec:${record.RecordId}`, track: 'log', at, times: [{ label: 'Written', at }], tone: 'ink', record,
    title: `${record.LevelDisplayName ?? LEVEL[Math.min(3, Math.max(0, record.Level - 1))]}: ${shortProvider(record.ProviderName)} ${record.Id}`, line: firstLine(record.Message) };
}

function InRange({ track }: { track: Track }) {
  const range = useApp((s) => s.range);
  const [shown, setShown] = useState(PAGE);
  const records = (part<EventRecord[]>(track.taken.reading, 'records') ?? [])
    .filter((r) => { const t = Date.parse(r.TimeCreated); return t >= range.from && t < range.to; });
  if (track.reach.state === 'waiting') return <p className={styles.quiet}>Reading…</p>;
  if (track.reach.state === 'failed') return <p className={styles.quiet}>The System log did not answer: {track.reach.detail}</p>;
  return (
    <section aria-labelledby="log-heading">
      <h2 id="log-heading" className={styles.heading}>Records in this range, newest first</h2>
      {records.length === 0 ? <p className={styles.quiet}>No records returned in the part of the range that was read.</p> : null}
      <Rows records={records.slice(0, shown)} />
      {records.length > shown ? <button className={styles.more} onClick={() => setShown((n) => n + PAGE)}>Show {Math.min(PAGE, records.length - shown)} more of {records.length}</button> : null}
    </section>
  );
}

function Around({ at, before, after, state, depth, onDeeper, onClear }: {
  at: number;
  before: EventRecord[] | null;
  after: EventRecord[] | null;
  state: 'taking' | 'read' | 'failed';
  depth: { before: number; after: number };
  onDeeper: (side: 'before' | 'after') => void;
  onClear: () => void;
}) {
  const select = useApp((s) => s.select);
  const past = before ?? [];
  const next = after ?? [];
  return (
    <section aria-labelledby="around-heading">
      <div className={styles.aroundHead}>
        <h2 id="around-heading" className={styles.heading}>The records around <span className="readout">{fmt.whenExact(at)}</span></h2>
        <button className={styles.more} onClick={() => { onClear(); select(null); }}>Back to the whole range</button>
      </div>
      {state === 'taking' ? <p className={styles.quiet}>Reading…</p> : null}
      {state === 'failed' ? <p className={styles.quiet}>Windows did not answer for the records around this moment.</p> : null}
      {/* A full page means the log may hold more on that side; asking again doubles the reach, up to 2,000. */}
      {past.length >= depth.before && depth.before < 2000 ? <button className={styles.more} onClick={() => onDeeper('before')}>Earlier records</button> : null}
      <Rows records={past} />
      <p className={styles.momentRule}><span className="readout">{fmt.second(at)}</span> the picked moment</p>
      <Rows records={next} />
      {next.length >= depth.after && depth.after < 2000 ? <button className={styles.more} onClick={() => onDeeper('after')}>Later records</button> : null}
      {state === 'read' && next.length === 0 ? <p className={styles.quiet}>No record after this moment was returned.</p> : null}
    </section>
  );
}

/** Rows with a day name wherever the day changes, so an order across midnight reads as an order. */
function Rows({ records }: { records: EventRecord[] }) {
  const select = useApp((s) => s.select);
  const selection = useApp((s) => s.selection);
  const chosen = selection?.kind === 'mark' ? selection.id : null;
  return (
    <ol className={styles.rows}>
      {records.map((record, index) => {
        const at = Date.parse(record.TimeCreated);
        const newDay = index === 0 || startOfDay(at) !== startOfDay(Date.parse(records[index - 1].TimeCreated));
        const level = Math.min(4, Math.max(1, record.Level || 4));
        const id = `rec:${record.RecordId}`;
        return (
          <Fragment key={record.RecordId}>
            {newDay ? <li className={styles.day} aria-hidden="true">{fmt.day(at)}</li> : null}
            <li>
              <button className={`${styles.row} ${chosen === id ? styles.rowChosen : ''}`} aria-pressed={chosen === id}
                onClick={() => select({ kind: 'mark', id, at: record.TimeCreated })}>
                <span className={`${styles.time} readout`}><span className={styles.srOnly}>{fmt.day(at)} </span>{fmt.second(at)}</span>
                <span className={`${styles.level} ${styles[`level${level}`]}`}><LevelGlyph level={level} />{LEVEL[level - 1]}</span>
                <span className={`${styles.source} readout`}>{shortProvider(record.ProviderName)} {record.Id}</span>
                <span className={styles.message}>{firstLine(record.Message)}</span>
              </button>
            </li>
          </Fragment>
        );
      })}
    </ol>
  );
}

/** Shape and word together, never colour alone: a filled triangle, a filled square, an open triangle, an open ring. */
export function LevelGlyph({ level }: { level: number }) {
  return (
    <svg className={styles.glyph} viewBox="0 0 12 12" aria-hidden="true">
      {level === 1 ? <path d="M6 1 11.5 11H.5z" /> : level === 2 ? <rect x="1.5" y="1.5" width="9" height="9" rx="1" />
        : level === 3 ? <path d="M6 1.8 10.8 10.4H1.2z" fill="none" strokeWidth="1.5" /> : <circle cx="6" cy="6" r="3.2" fill="none" strokeWidth="1.3" />}
    </svg>
  );
}

function RecordFields({ record, envelope }: { record: EventRecord; envelope: Reading | null }) {
  const select = useApp((s) => s.select);
  return (
    <div className={styles.fields}>
      <dl className={styles.facts}>
        <div><dt>Provider</dt><dd>{record.ProviderName}</dd></div>
        <div><dt>Event</dt><dd className="readout">{record.Id}{record.TaskDisplayName ? ` · ${record.TaskDisplayName}` : ''}</dd></div>
        <div><dt>Record</dt><dd className="readout">{String(record.RecordId)}</dd></div>
        <div><dt>Written</dt><dd className="readout">{record.TimeCreated}</dd></div>
      </dl>
      {record.Message ? <p className={styles.fullMessage}>{record.Message}</p> : <p className={styles.quiet}>No message text.</p>}
      {record.Properties?.length ? (
        <details className={styles.disclosure}>
          <summary>Properties · {record.Properties.length}</summary>
          <pre className="readout">{record.Properties.map((p) => (typeof p === 'string' ? p : JSON.stringify(p))).join('\n')}</pre>
        </details>
      ) : null}
      <details className={styles.disclosure}>
        <summary>The raw record, as returned</summary>
        <pre className="readout">{JSON.stringify(record, null, 2)}</pre>
      </details>
      <div className={styles.fieldActions}>
        <button className={styles.more} onClick={() => { select({ kind: 'moment', at: record.TimeCreated }); useApp.getState().lowerSheet(); }}>The records around this one</button>
        {envelope ? <AddToStack item={{ kind: 'selection', envelope, ids: [record.RecordId] }} label="Stack this record" /> : null}
      </div>
    </div>
  );
}
