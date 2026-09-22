import { Fragment, Ref, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { AddToStack } from '../AddToStack';
import { EventRecord, Reading, observed, section } from '../api';
import { clock, Glyph, OutcomeLine, firstLine } from '../Outcome';
import { Segmented, byDay, day } from '../Sections';
import { useApp } from '../store';
import { Taken, useReading } from '../useReading';
import styles from './Record.module.css';

type Levels = 'errors' | 'all';
type Span = 'recent' | 'boot';
const LEVELS: Record<Levels, number[]> = { errors: [1, 2], all: [1, 2, 3, 4] };
const COUNTS = [50, 200, 500];
/** Since boot is a window, not a count: it asks for the whole session and the outcome line says how much that was. */
const BOOT_COUNT = 500;
const PAGE = 25;

/**
 * The record: the System log, most recent first, each row inspectable in place, and for any row the
 * records that came before it. The log does not announce a freeze; the next start does.
 *
 * This view has one other job: it is where every moment-jump in the dashboard lands. While a
 * moment is held, the log is framed around it — the same rows, read forward into that instant,
 * with one control back. The moment survives leaving the view and coming back, because a person
 * who was interrupted mid-investigation should not have to find it again.
 */
export function Record() {
  const moment = useApp((s) => s.moment);
  // A fresh moment is a fresh frame: remounting drops how far the last one had been widened.
  return moment ? <Frame key={moment} moment={moment} /> : <Log />;
}

/** The log itself: which levels, how many, and whether the window is a count or this session. */
function Log() {
  const [levels, setLevels] = useState<Levels>('errors');
  const [count, setCount] = useState(50);
  const [span, setSpan] = useState<Span>('recent');
  const [filtersOpen, setFiltersOpen] = useState(false);
  const boot = span === 'boot';
  const taken = useReading<EventRecord[]>(
    'events',
    boot ? { log: 'System', levels: LEVELS[levels], count: BOOT_COUNT, since: 'boot' } : { log: 'System', levels: LEVELS[levels], count },
  );
  const records = section(taken.reading, 'records') ?? [];

  return (
    <section>
      <div className={styles.head}>
        <h1 className={`${styles.title} display`}>Record</h1>
        <button className={styles.filterToggle} onClick={() => setFiltersOpen((open) => !open)} aria-expanded={filtersOpen} aria-controls="record-controls">
          <span className="readout">Window</span>
          <span className={styles.filterValue}>{levels === 'errors' ? 'Critical and error' : 'Every level'} · {boot ? 'Since boot' : `Last ${count}`}</span>
          <span className={styles.filterChevron} aria-hidden="true">⌄</span>
        </button>
        <div className={`${styles.controls} ${filtersOpen ? '' : styles.controlsClosed}`} id="record-controls" role="group" aria-label="Which records">
          <Segmented
            value={levels}
            onChange={setLevels}
            options={[
              { value: 'errors', label: 'Critical and error' },
              { value: 'all', label: 'Every level' },
            ]}
            label="Which levels"
          />
          <Segmented
            value={span}
            onChange={setSpan}
            options={[
              { value: 'recent', label: 'Last records' },
              { value: 'boot', label: 'Since boot' },
            ]}
            label="Window"
          />
          {/* The count belongs to the last-records window; since boot has its own, so the control
              goes rather than sitting there meaning nothing, and returns with the value it had. */}
          {boot ? null : <Segmented value={count} onChange={setCount} options={COUNTS.map((c) => ({ value: c, label: `last ${c}` }))} label="How many" />}
        </div>
        {taken.reading ? <AddToStack item={{ kind: 'reading', envelope: taken.reading }} label="Stack this reading" /> : null}
      </div>
      <OutcomeLine
        taken={taken}
        noun={boot ? 'records since boot' : 'records'}
        emptyText={
          boot
            ? levels === 'errors'
              ? 'No critical or error record since this session started'
              : 'Nothing in the log since this session started'
            : levels === 'errors'
              ? `No critical or error records among the last ${count}`
              : 'The log is empty'
        }
      />
      {observed(taken.reading) && records.length > 0 && taken.reading ? <Rows records={records} reading={taken.reading} overview /> : null}
    </section>
  );
}

/**
 * The log framed on a moment: the records before it, oldest first, ending there. The title says
 * what the list is rather than what the view is called, because that is the question being asked;
 * the way back is beside it and clears the moment everywhere.
 */
function Frame({ moment }: { moment: string }) {
  const setMoment = useApp((s) => s.setMoment);
  const when = new Date(moment);
  const before = useBefore(moment);

  return (
    <section>
      <div className={styles.head}>
        <h1 className={`${styles.title} display`}>The record before {clock.format(when)}</h1>
        <div className={styles.controls}>
          <button className={styles.action} onClick={() => setMoment(null)}>
            Back to the log
          </button>
          {before.held ? (
            <AddToStack item={{ kind: 'reading', envelope: before.held, title: `The record before ${moment}` }} label="Stack this reading" />
          ) : null}
        </div>
      </div>
      <p className={`${styles.frameLine} readout`}>{day.format(when)} · the System log, every level, oldest first and ending at this moment</p>
      <OutcomeLine taken={before.taken} noun="records" emptyText="Nothing in the log before this moment" />
      {before.rows.length && before.held ? (
        <>
          <More before={before} />
          <Rows records={before.rows} reading={before.held} listRef={before.list} />
        </>
      ) : null}
    </section>
  );
}

function Rows({ records, reading, listRef, overview = false }: { records: EventRecord[]; reading: Reading<EventRecord[]>; listRef?: Ref<HTMLOListElement>; overview?: boolean }) {
  const [open, setOpen] = useState<number | null>(null);
  const grouped = useMemo(() => byDay(records, (r) => r.TimeCreated), [records]);
  const openRecord = (id: number) => {
    setOpen(id);
    requestAnimationFrame(() => {
      const target = document.querySelector<HTMLButtonElement>(`li[data-record="${id}"] > button`);
      target?.scrollIntoView({ block: 'center' });
      target?.focus({ preventScroll: true });
    });
  };
  return (
    <>
      {overview ? <RecordOverview records={records} selected={open} onOpen={openRecord} /> : null}
      <ol className={styles.rows} ref={listRef}>
        {grouped.map(([label, rows]) => (
          <Fragment key={label}>
            <li className={`${styles.day} label`} aria-hidden="true">{label}</li>
            {rows.map((r) => (
              <Row key={r.RecordId} record={r} reading={reading} open={open === r.RecordId} onToggle={() => setOpen(open === r.RecordId ? null : r.RecordId)} />
            ))}
          </Fragment>
        ))}
      </ol>
    </>
  );
}

const DENSITY_BINS = 12;
const LOCAL_STAMP = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
const newestRecord = (rows: EventRecord[]) => rows.reduce((newest, row) => Date.parse(row.TimeCreated) > Date.parse(newest.TimeCreated) ? row : newest);

/** A map of the returned rows only. An empty bin is never a claim about the rest of the log. */
function RecordOverview({ records, selected, onOpen }: { records: EventRecord[]; selected: number | null; onOpen: (id: number) => void }) {
  const timed = records.map((record) => ({ record, at: Date.parse(record.TimeCreated) })).filter((item) => Number.isFinite(item.at));
  const oldest = timed.length ? Math.min(...timed.map((item) => item.at)) : 0;
  const newest = timed.length ? Math.max(...timed.map((item) => item.at)) : 0;
  const start = oldest === newest ? oldest - 30 * 60_000 : oldest;
  const span = oldest === newest ? 60 * 60_000 : newest - oldest;
  const bins: EventRecord[][] = Array.from({ length: DENSITY_BINS }, () => []);
  for (const item of timed) {
    const index = Math.min(DENSITY_BINS - 1, Math.floor(((item.at - start) / span) * DENSITY_BINS));
    bins[index].push(item.record);
  }
  const peak = Math.max(1, ...bins.map((bin) => bin.length));
  const providers = new Map<string, EventRecord[]>();
  for (const record of records) {
    const group = providers.get(record.ProviderName) ?? [];
    group.push(record);
    providers.set(record.ProviderName, group);
  }
  const leading = [...providers].sort((a, b) => b[1].length - a[1].length).slice(0, 3);

  return (
    <section className={styles.overview} aria-labelledby="record-overview-title">
      <div className={styles.overviewHead}>
        <div><p className="label">Returned sample</p><h2 id="record-overview-title" className="display">The shape of these records</h2></div>
        <p>These bars describe the {records.length} rows below. A blank interval means no returned row falls there; earlier records may exist.</p>
      </div>
      <div className={styles.overviewBody}>
        <div className={styles.density}>
          <div className={`${styles.plotTitle} readout`}><span>When Windows logged them</span><span>Peak {peak} in one interval</span></div>
          {timed.length ? (
            <>
              <div className={styles.bins} role="group" aria-label="Returned record density by time">
                {bins.map((bin, index) => {
                  const binStart = start + (span * index) / DENSITY_BINS;
                  const binEnd = start + (span * (index + 1)) / DENSITY_BINS;
                  const latest = bin.length ? newestRecord(bin) : null;
                  return latest ? (
                    <button
                      key={index}
                      className={`${styles.bin} ${bin.some((r) => r.RecordId === selected) ? styles.binSelected : ''}`}
                      onClick={() => onOpen(latest.RecordId)}
                      aria-label={`${bin.length} returned ${bin.length === 1 ? 'record' : 'records'} from ${LOCAL_STAMP.format(binStart)} to ${LOCAL_STAMP.format(binEnd)}; open the newest one below`}
                    ><span style={{ height: `${Math.max(4, (bin.length / peak) * 68)}px` }} /></button>
                  ) : <span className={styles.binEmpty} key={index} aria-hidden="true" />;
                })}
              </div>
              <div className={`${styles.plotAxis} readout`}><span>{LOCAL_STAMP.format(oldest)}</span><span>{LOCAL_STAMP.format(newest)}</span></div>
            </>
          ) : <p className={`${styles.noTime} readout`}>No returned record had a time to plot.</p>}
          {timed.length < records.length ? <p className={`${styles.unplaced} readout`}>{records.length - timed.length} returned records had no usable time.</p> : null}
        </div>
        <div className={styles.sources}>
          <p className={`${styles.plotTitle} readout`}>Top sources · {providers.size} in the sample</p>
          {leading.map(([name, entries]) => (
            <button key={name} className={styles.source} onClick={() => onOpen(newestRecord(entries).RecordId)} aria-label={`${name}: ${entries.length} of ${records.length} returned records; open a matching row below`}>
              <span className={styles.sourceLine}><span title={name}>{shortProvider(name)}</span><strong className="readout">{entries.length} / {records.length}</strong></span>
              <span className={styles.sourceTrack}><span style={{ width: `${(entries.length / records.length) * 100}%` }} /></span>
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}

function Row({ record, reading, open, onToggle }: { record: EventRecord; reading: Reading<EventRecord[]>; open: boolean; onToggle: () => void }) {
  const t = new Date(record.TimeCreated);
  const level = levelKind(record.Level);
  return (
    <li className={`${styles.row} ${open ? styles.rowOpen : ''}`} data-record={record.RecordId}>
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

/** The records before a moment, under the row they belong to: what the machine was doing, without leaving the list. */
function Before({ moment }: { moment: string }) {
  const before = useBefore(moment);
  return (
    <div className={styles.before}>
      <p className="label">The records before {clock.format(new Date(moment))}</p>
      <OutcomeLine taken={before.taken} noun="records" emptyText="Nothing in the log before this moment" />
      {before.rows.length ? (
        <>
          <More before={before} />
          <ol className={styles.beforeRows} ref={before.list}>
            {before.rows.map((r) => (
              <li key={r.RecordId} className={styles.beforeRow} data-record={r.RecordId}>
                <span className={`${styles.time} readout`}>{clock.format(new Date(r.TimeCreated))}</span>
                <span className={styles.level} title={r.LevelDisplayName}><Glyph kind={levelKind(r.Level)} /></span>
                <span className={`${styles.provider} readout`}>{shortProvider(r.ProviderName)}</span>
                <span className={`${styles.id} readout`}>{r.Id}</span>
                <span className={styles.message}>{r.Message ? firstLine(r.Message) : ''}</span>
              </li>
            ))}
          </ol>
        </>
      ) : null}
    </div>
  );
}

interface Widened {
  /** The latest take, whatever it answered: the outcome line shows this one. */
  taken: Taken<EventRecord[]>;
  /** The last take that observed the machine: the rows and the stack are made of this one, so a
   *  widening that was not observed says so above the frame and leaves the frame standing. */
  held: Reading<EventRecord[]> | null;
  rows: EventRecord[];
  more: () => void;
  list: Ref<HTMLOListElement>;
  /** The log answered with fewer records than were asked for: there is nothing earlier to ask for. */
  atStart: boolean;
}

/**
 * The records before a moment, widened a page at a time.
 *
 * One reading, not a stitched sequence of them: asking for more asks the log for a longer run
 * before the same moment, so two records written in the same second cannot fall between two takes,
 * and what goes on the stack is the whole frame rather than its last page. The new rows arrive
 * above the ones already read, which would otherwise push the reader's place down the screen, so
 * the top row is measured before the take and the page is scrolled back onto it after.
 */
function useBefore(moment: string): Widened {
  const [count, setCount] = useState(PAGE);
  const taken = useReading<EventRecord[]>('record', { before: moment, count });
  // Design for interrupted work: a wider take that did not observe the machine must not take the
  // narrower one's rows away with it. The frame stands on the last observed envelope.
  const [held, setHeld] = useState<Reading<EventRecord[]> | null>(null);
  useEffect(() => {
    if (observed(taken.reading)) setHeld(taken.reading);
  }, [taken.reading]);
  const rows = section(held, 'records') ?? [];
  const list = useRef<HTMLOListElement>(null);
  const place = useRef<{ id: string; top: number } | null>(null);

  const more = useCallback(() => {
    const first = list.current?.querySelector<HTMLElement>('[data-record]');
    place.current = first?.dataset.record ? { id: first.dataset.record, top: first.getBoundingClientRect().top } : null;
    setCount((c) => c + PAGE);
  }, []);

  useLayoutEffect(() => {
    const held = place.current;
    if (!held || taken.state === 'taking') return;
    const node = list.current?.querySelector<HTMLElement>(`[data-record="${held.id}"]`);
    if (node) window.scrollBy(0, node.getBoundingClientRect().top - held.top);
    place.current = null;
  }, [rows.length, taken.state]);

  return { taken, held, rows, more, list, atStart: taken.state === 'done' && observed(taken.reading) && rows.length < count };
}

/** The way further back, at the top of the list because that is where the records it asks for appear. */
function More({ before }: { before: Widened }) {
  if (before.atStart) return <p className={`${styles.logStart} readout`}>The log holds nothing earlier than this.</p>;
  return (
    <button className={`${styles.more} readout`} onClick={before.more} disabled={before.taken.state === 'taking'}>
      {before.taken.state === 'taking' ? 'Taking…' : `${PAGE} more before this`}
    </button>
  );
}

function levelKind(level: number): 'critical' | 'error' | 'warning' | 'info' {
  return level === 1 ? 'critical' : level === 2 ? 'error' : level === 3 ? 'warning' : 'info';
}

function shortProvider(name: string): string {
  return name.replace(/^Microsoft-Windows-/, '');
}
