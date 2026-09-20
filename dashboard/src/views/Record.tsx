import { Fragment, useMemo, useState } from 'react';
import { AddToStack } from '../AddToStack';
import { EventRecord, Reading, observed, section } from '../api';
import { clock, Glyph, OutcomeLine, firstLine } from '../Outcome';
import { Segmented, byDay } from '../Sections';
import { useReading } from '../useReading';
import styles from './Record.module.css';

type Levels = 'errors' | 'all';
const LEVELS: Record<Levels, number[]> = { errors: [1, 2], all: [1, 2, 3, 4] };
const COUNTS = [50, 200, 500];


/**
 * The record: the System log, most recent first, each row inspectable in place, and for any row the
 * records that came before it. The log does not announce a freeze; the next start does.
 */
export function Record() {
  const [levels, setLevels] = useState<Levels>('errors');
  const [count, setCount] = useState(50);
  const taken = useReading<EventRecord[]>('events', { log: 'System', levels: LEVELS[levels], count });
  const records = section(taken.reading, 'records') ?? [];

  return (
    <section>
      <div className={styles.head}>
        <h1 className={`${styles.title} display`}>Record</h1>
        <div className={styles.controls} role="group" aria-label="Which records">
          <Segmented value={levels} onChange={setLevels} options={[{ value: 'errors', label: 'Critical and error' }, { value: 'all', label: 'Every level' }]} label="Which levels" />
          <Segmented value={count} onChange={setCount} options={COUNTS.map((c) => ({ value: c, label: `last ${c}` }))} label="How many" />
        </div>
        {taken.reading ? <AddToStack item={{ kind: 'reading', envelope: taken.reading }} label="Stack this reading" /> : null}
      </div>
      <OutcomeLine taken={taken} noun="records" emptyText={levels === 'errors' ? `No critical or error records among the last ${count}` : 'The log is empty'} />
      {observed(taken.reading) && records.length > 0 && taken.reading ? <Rows records={records} reading={taken.reading} /> : null}
    </section>
  );
}

function Rows({ records, reading }: { records: EventRecord[]; reading: Reading<EventRecord[]> }) {
  const [open, setOpen] = useState<number | null>(null);
  const grouped = useMemo(() => byDay(records, (r) => r.TimeCreated), [records]);
  return (
    <ol className={styles.rows}>
      {grouped.map(([label, rows]) => (
        <Fragment key={label}>
          <li className={`${styles.day} label`} aria-hidden="true">{label}</li>
          {rows.map((r) => (
            <Row key={r.RecordId} record={r} reading={reading} open={open === r.RecordId} onToggle={() => setOpen(open === r.RecordId ? null : r.RecordId)} />
          ))}
        </Fragment>
      ))}
    </ol>
  );
}

function Row({ record, reading, open, onToggle }: { record: EventRecord; reading: Reading<EventRecord[]>; open: boolean; onToggle: () => void }) {
  const t = new Date(record.TimeCreated);
  const level = levelKind(record.Level);
  return (
    <li className={`${styles.row} ${open ? styles.rowOpen : ''}`}>
      <button className={styles.rowButton} onClick={onToggle} aria-expanded={open}>
        <span className={`${styles.time} readout`}>{clock.format(t)}</span>
        <span className={styles.level} title={record.LevelDisplayName}><Glyph kind={level} /></span>
        <span className={`${styles.provider} readout`}>{shortProvider(record.ProviderName)}</span>
        <span className={`${styles.id} readout`}>{record.Id}</span>
        <span className={styles.message}>{record.Message ? firstLine(record.Message) : <em className={styles.noMessage}>no message text</em>}</span>
      </button>
      {open ? <Inspect record={record} reading={reading} /> : null}
    </li>
  );
}

/** Inspect in place: the whole record, then the records before it, without leaving the list. */
function Inspect({ record, reading }: { record: EventRecord; reading: Reading<EventRecord[]> }) {
  const [before, setBefore] = useState(false);
  return (
    <div className={styles.inspect}>
      <dl className={`${styles.facts} readout`}>
        <dt>Provider</dt><dd>{record.ProviderName}</dd>
        <dt>Event</dt><dd>{record.Id}{record.TaskDisplayName ? ` · ${record.TaskDisplayName}` : ''}</dd>
        <dt>Level</dt><dd>{record.LevelDisplayName}</dd>
        <dt>Record</dt><dd>{record.RecordId}</dd>
        <dt>Time</dt><dd>{record.TimeCreated}</dd>
      </dl>
      {record.Message ? <p className={styles.fullMessage}>{record.Message}</p> : null}
      {record.Properties && record.Properties.length ? (
        <details className={styles.props}>
          <summary className="label">Properties · {record.Properties.length}</summary>
          <pre className={`${styles.propsBody} readout`}>{record.Properties.map((p) => (typeof p === 'string' ? p : JSON.stringify(p))).join('\n')}</pre>
        </details>
      ) : null}
      <div className={styles.actions}>
        <button className={styles.action} onClick={() => setBefore((v) => !v)} aria-expanded={before}>{before ? 'Hide the record before this' : 'The record before this'}</button>
        <AddToStack item={{ kind: 'selection', envelope: reading, ids: [record.RecordId] }} label="Stack this record" />
      </div>
      {before ? <Before moment={record.TimeCreated} /> : null}
    </div>
  );
}

/** The records before a moment, oldest first, ending at the moment: what the machine was doing. */
function Before({ moment }: { moment: string }) {
  const taken = useReading<EventRecord[]>('record', { before: moment, count: 25 });
  const rows = section(taken.reading, 'records') ?? [];
  return (
    <div className={styles.before}>
      <p className="label">The 25 records before {clock.format(new Date(moment))}</p>
      <OutcomeLine taken={taken} noun="records" emptyText="Nothing in the log before this moment" />
      {rows.length ? (
        <ol className={styles.beforeRows}>
          {rows.map((r) => (
            <li key={r.RecordId} className={styles.beforeRow}>
              <span className={`${styles.time} readout`}>{clock.format(new Date(r.TimeCreated))}</span>
              <span className={styles.level} title={r.LevelDisplayName}><Glyph kind={levelKind(r.Level)} /></span>
              <span className={`${styles.provider} readout`}>{shortProvider(r.ProviderName)}</span>
              <span className={`${styles.id} readout`}>{r.Id}</span>
              <span className={styles.message}>{r.Message ? firstLine(r.Message) : ''}</span>
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}

function levelKind(level: number): 'critical' | 'error' | 'warning' | 'info' {
  return level === 1 ? 'critical' : level === 2 ? 'error' : level === 3 ? 'warning' : 'info';
}

function shortProvider(name: string): string {
  return name.replace(/^Microsoft-Windows-/, '');
}
