import { Fragment, Ref, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { AddToStack } from '../AddToStack';
import { EventRecord, Reading, type RecordId, observed, section } from '../api';
import { clock, OutcomeLine, firstLine } from '../Outcome';
import { LevelGlyph } from '../Marks';
import { Segmented, byDay, day, part } from '../Sections';
import { useApp, VIEWS } from '../store';
import { Taken, useReading } from '../useReading';
import { FaultDetail, type Fault } from './Crashes';
import { ReportDetail } from './Errors';
import { KernelReports, reportsFromWindow, type ReportReach, type ReportSource } from './KernelReports';
import styles from './Record.module.css';

type Levels = 'errors' | 'all';
type Span = 'recent' | 'boot';
const LEVELS: Record<Levels, number[]> = { errors: [1, 2], all: [] };
const COUNTS = [50, 200, 500];
/** The boot window is bounded; the reading warns when older matching records were left out. */
const BOOT_COUNT = 500;
const PAGE = 25;
const FRAME_LIMIT = 2000;
const WINDOW_STAMP = new Intl.DateTimeFormat(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });

interface NearbyReach { complete: boolean | null; covered_from: string | null; covered_until: string | null }
interface NearbySource { log_oldest?: string | null; oldest_state?: string | null; log_enabled?: boolean | null; queried_at?: string | null }

/** Retention failure, an old rotated window, and a partly observed window need different words. */
function nearbyReachText(reach: NearbyReach | null, source: NearbySource | null, end: string, label: string): string {
  if (reach?.complete === true) return 'Queried window covered';
  if (source?.oldest_state === 'empty') return `${label} holds no retained records; this does not establish earlier absence`;
  const oldest = source?.log_oldest ? Date.parse(source.log_oldest) : NaN;
  if (source?.oldest_state === 'ok' && Number.isFinite(oldest) && oldest >= Date.parse(end)) {
    return `${label} begins at ${WINDOW_STAMP.format(oldest)}; this queried window is older than its retained history`;
  }
  if (source?.log_enabled === false) return `${label} is disabled; absence of new records cannot be established`;
  if (reach?.covered_from && reach.covered_until) {
    return `Partly covered · ${WINDOW_STAMP.format(new Date(reach.covered_from))} to ${WINDOW_STAMP.format(new Date(reach.covered_until))}`;
  }
  return 'Window coverage could not be established';
}

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
        <h1 className={`${styles.title} display`}>The System log</h1>
        {taken.reading ? <AddToStack item={{ kind: 'reading', envelope: taken.reading }} label="Stack this reading" /> : null}
      </div>
      <p className={styles.intro}>What Windows wrote down, newest first. Open a row for its full message and the records before it. An entry alone does not establish a cause.</p>
      <div className={styles.controlRow}>
        <button className={styles.filterToggle} onClick={() => setFiltersOpen((open) => !open)} aria-expanded={filtersOpen} aria-controls="record-controls">
          <span>Showing</span>
          <span className={styles.filterValue}>{levels === 'errors' ? 'Critical and error' : 'Every level'} · {boot ? `This Windows session · up to ${BOOT_COUNT}` : `Last ${count}`}</span>
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
              { value: 'boot', label: 'This Windows session' },
            ]}
            label="Window"
          />
          {/* The count belongs to the last-records window; since boot has its own, so the control
              goes rather than sitting there meaning nothing, and returns with the value it had. */}
          {boot ? null : <Segmented value={count} onChange={setCount} options={COUNTS.map((c) => ({ value: c, label: `${c}` }))} label="How many" />}
        </div>
      </div>
      <OutcomeLine
        taken={taken}
        noun={boot ? 'records since the reported Windows session start' : 'records'}
        singular={boot ? 'record since the reported Windows session start' : 'record'}
        emptyText={
          boot
            ? levels === 'errors'
              ? 'No critical or error record returned from the retained log since the reported Windows session start'
              : 'No record returned from the retained log since the reported Windows session start'
            : levels === 'errors'
              ? 'No critical or error records returned from the retained log'
              : 'No records returned from the retained log'
        }
      />
      {observed(taken.reading) && records.length > 0 && taken.reading ? <Rows records={records} reading={taken.reading} overview /> : null}
    </section>
  );
}

/**
 * The log framed on a moment: the records before it and those Windows wrote after it. The title says
 * what the list is rather than what the view is called, because that is the question being asked;
 * the way back is beside it and clears the moment everywhere.
 */
function Frame({ moment }: { moment: string }) {
  const setMoment = useApp((s) => s.setMoment);
  const origin = useApp((s) => s.recordOrigin);
  const setView = useApp((s) => s.setView);
  const when = new Date(moment);
  const before = useBefore(moment);

  return (
    <section>
      <div className={styles.head}>
        <h1 className={`${styles.title} display`}>The record around {clock.format(when)}</h1>
        <div className={styles.controls}>
          {origin ? <button className={styles.action} onClick={() => {
            if (history.state?.sentinelReturnTo === origin) history.back();
            else setView(origin);
          }}>Back to {VIEWS.find((item) => item.id === origin)?.label ?? origin}</button> : null}
          <button className={styles.action} onClick={() => setMoment(null)}>
            Back to the log
          </button>
          {before.held ? (
            <AddToStack item={{ kind: 'reading', envelope: before.held, title: `The record before ${moment}` }} label="Stack this reading" />
          ) : null}
        </div>
      </div>
      <p className={`${styles.frameLine} readout`}>{day.format(when)} · System log, every level · records timestamped before this moment and the first returned records timestamped at or after it</p>
      <button className={`${styles.jumpAfter} readout`} onClick={() => {
        const heading = document.getElementById('record-after-title');
        heading?.focus({ preventScroll: true });
        heading?.scrollIntoView({ block: 'start' });
      }}>Jump to the records at or after this time ↓</button>
      <h2 className={`${styles.frameSideTitle} display`}>Before this moment</h2>
      <OutcomeLine taken={before.taken} noun="records" singular="record" emptyText="No retained System records returned before this moment" />
      {before.rows.length && before.held ? (
        <>
          <More before={before} />
          <Rows records={before.rows} reading={before.held} listRef={before.list} />
        </>
      ) : null}
      <After moment={moment} />
      <FaultWindow moment={moment} />
      <KernelReportWindow moment={moment} />
    </section>
  );
}

/** A separate, exact oldest-first reading; its reach is never merged with the before frame. */
function After({ moment }: { moment: string }) {
  const [count, setCount] = useState(PAGE);
  const frame = useRef<HTMLElement>(null);
  const focusNewRow = useRef<number | null>(null);
  const taken = useReading<EventRecord[]>('events', { log: 'System', levels: [], since: moment, order: 'oldest', count });
  const held = useHeld(taken);
  const rows = section(held, 'records') ?? [];
  useLayoutEffect(() => {
    const index = focusNewRow.current;
    if (index === null) return;
    if (rows.length > index) {
      frame.current?.querySelectorAll<HTMLButtonElement>('li[data-record] > button')[index]?.focus();
      focusNewRow.current = null;
    } else if (taken.state === 'lost' || taken.state === 'done') {
      focusNewRow.current = null;
    }
  }, [rows.length, taken.state, held, taken.reading]);
  const collection = held?.sections.find((s) => s.name === 'collection')?.data as unknown as
    ({ truncated?: boolean | null; stopped?: unknown; queried_at?: string | null; window_end?: string | null; log_oldest?: string | null; oldest_state?: string | null; log_enabled?: boolean | null } | undefined);
  const coverage = held?.sections.find((s) => s.name === 'coverage')?.data as unknown as NearbyReach | undefined;
  const heldCount = typeof held?.params.count === 'number' ? held.params.count : PAGE;
  const nextCount = Math.min(FRAME_LIMIT, heldCount * 2);
  const stale = held && held !== taken.reading;
  const reachedEnd = coverage?.complete === true;
  const limitReached = heldCount === FRAME_LIMIT && collection?.truncated === true;
  const stopped = collection?.stopped != null;

  return <section ref={frame} className={styles.after} aria-labelledby="record-after-title">
    <div className={styles.momentMarker}><span className="readout">Selected moment · {moment}</span></div>
    <div className={styles.afterHead}>
      <div><p className="label">The System log · every level</p><h2 id="record-after-title" tabIndex={-1} className="display">At or after this moment</h2></div>
      {held && observed(held) ? <AddToStack item={{ kind: 'reading', envelope: held, title: `The record at or after ${moment}` }} label={stale ? 'Stack this held reading' : 'Stack this reading'} /> : null}
    </div>
    <p className={styles.afterIntro}>The first returned System records timestamped from this instant. A restart can write its own record here. Clock changes can move records across this boundary. A nearby record is a lead to inspect, not proof of a cause.</p>
    <OutcomeLine taken={taken} noun="records" singular="record" emptyText="No retained System records returned at or after this moment" />
    {stale ? <p className={`${styles.afterStatus} readout`} role="status">Showing the last observed reading while {taken.state === 'taking' ? 'another take is in progress.' : 'the latest take did not observe the machine.'}</p> : null}
    {stale && held?.warnings.length ? <ul className={`${styles.nearbyWarnings} readout`} aria-label="Limits of the held reading">{held.warnings.map((warning, index) => <li key={index}>{firstLine(warning)}</li>)}</ul> : null}
    {collection && coverage ? <p className={`${styles.afterStatus} readout`}>{nearbyReachText(coverage, collection, collection.window_end ?? collection.queried_at ?? moment, 'System log')} · {rows.length} returned of {heldCount} requested{collection.truncated === true ? '; later matching records remain' : ''}.</p> : null}
    {rows.length && held ? <Rows records={rows} reading={held} /> : null}
    <div className={styles.afterActions}>
      {rows.length > 0 && held && observed(held) && collection?.truncated === true && !stopped && heldCount < FRAME_LIMIT ? <button className={`${styles.more} readout`} aria-disabled={taken.state === 'taking'} onClick={(event) => {
        if (taken.state === 'taking') return;
        if (event.detail === 0) focusNewRow.current = rows.length;
        if (nextCount === count) taken.retake();
        else setCount(nextCount);
      }}>{taken.state === 'taking' ? 'Taking…' : `${nextCount - heldCount} more at or after this`}</button> : null}
      {limitReached ? <p className={`${styles.afterStatus} readout`}>This frame reached {FRAME_LIMIT.toLocaleString()} records. Choose a later moment to continue.</p> : null}
      {collection?.truncated === false && !reachedEnd ? <p className={`${styles.afterStatus} readout`}>This read reached the end of retained matching records; coverage of the requested time could not be established.</p> : null}
      {rows.length > 0 ? <button className={`${styles.action} readout`} aria-disabled={taken.state === 'taking'} onClick={() => { if (taken.state !== 'taking') taken.retake(); }}>{taken.state === 'taking' ? 'Taking…' : 'Read this side again'}</button> : null}
    </div>
  </section>;
}

/** An optional second source beside the System frame, opened only when the person asks for it. */
function FaultWindow({ moment }: { moment: string }) {
  const [open, setOpen] = useState(false);
  const [asked, setAsked] = useState(false);
  const at = Date.parse(moment);
  const outerStart = new Date(at - 60 * 60 * 1000).toISOString();
  const outerEnd = new Date(at + 60 * 60 * 1000).toISOString();

  return <section className={styles.nearby} aria-labelledby="nearby-faults-title">
    <div className={styles.nearbyHead}>
      <div>
        <p className="label">Application log · optional second source</p>
        <h2 id="nearby-faults-title" className="display">Fault reports near this moment</h2>
      </div>
      <button className={styles.action} onClick={() => { setAsked(true); setOpen((value) => !value); }} aria-expanded={open} aria-controls="nearby-faults-body">
        {open ? 'Hide reports' : 'Read nearby reports'}
      </button>
    </div>
    <p className={styles.nearbyIntro}>Looks for application crashes, hangs and live kernel reports filed near this moment. Each side keeps its nearest records and has its own reach. A nearby report is a lead, not proof of a cause; reports may be filed after the fault occurred. A live kernel report can have records on both sides, so a side only shows the records it returned.</p>
    <div id="nearby-faults-body" hidden={!open}>
      <p className={`${styles.nearbyReach} readout`}>Two Application log reads meet at {clock.format(new Date(moment))}; 50 records nearest the moment on each side.</p>
      <FaultSide moment={moment} since={outerStart} before={moment} order="newest" asked={asked} />
      <FaultSide moment={moment} since={moment} before={outerEnd} order="oldest" asked={asked} />
    </div>
  </section>;
}

function FaultSide({ moment, since, before, order, asked }: {
  moment: string; since: string; before: string; order: 'newest' | 'oldest'; asked: boolean;
}) {
  const earlier = order === 'newest';
  const [limit, setLimit] = useState(10);
  const taken = useReading('faults', { since, before, order, count: 50 }, asked);
  const shown = useHeld(taken);
  const raw = part<EventRecord[]>(shown, 'records') ?? [];
  const decoded = part<Fault[]>(shown, 'decoded') ?? [];
  const reach = part<NearbyReach>(shown, 'coverage');
  const source = part<NearbySource>(shown, 'collection');
  const times = new Map(raw.map((row) => [String(row.RecordId), row.TimeCreated]));
  const filingTime = (fault: Fault) => {
    const representative = times.get(String(fault.RecordId));
    if (order !== 'oldest' || !fault.report) return representative;
    const returned = fault.report.records.map((id) => times.get(String(id))).filter((at): at is string => !!at && Number.isFinite(Date.parse(at)));
    return returned.reduce((first, at) => compareFilingTimes(at, first) < 0 ? at : first, representative ?? returned[0]);
  };
  const placed = [...decoded].sort((a, b) => {
    const at = filingTime(a);
    const bt = filingTime(b);
    const aKnown = !!at && Number.isFinite(Date.parse(at));
    const bKnown = !!bt && Number.isFinite(Date.parse(bt));
    if (!aKnown || !bKnown) return aKnown ? -1 : bKnown ? 1 : 0;
    return (earlier ? -1 : 1) * compareFilingTimes(at, bt);
  });
  const futureEnd = !!source?.queried_at && Date.parse(before) > Date.parse(source.queried_at);
  const stale = shown !== null && shown !== taken.reading;

  return <section className={styles.nearbySide} aria-labelledby={`fault-${order}-title`}>
    <h3 id={`fault-${order}-title`} className="display">{earlier ? 'Before this moment · nearest first' : 'At or after this moment · nearest first'}</h3>
    <OutcomeLine taken={taken} noun="Application-log records" singular="Application-log record"
      emptyText={`No matching fault report returned ${earlier ? 'before' : 'at or after'} this moment in the queried Application-log window`} />
    {stale ? <p className={`${styles.nearbyReach} readout`} role="status">Showing the reading taken at {clock.format(new Date(shown.asked_at))}; {taken.state === 'taking' ? 'another take is in progress.' : 'the last completed take did not observe the machine.'}</p> : null}
    {stale && shown.warnings.length ? <ul className={`${styles.nearbyWarnings} readout`} aria-label="Limits of the held reading">{shown.warnings.map((warning, index) => <li key={index}>{firstLine(warning)}</li>)}</ul> : null}
    {reach ? <p className={`${styles.nearbyReach} readout`}>{nearbyReachText(reach, source, before, 'Application log')}{futureEnd ? ` · requested end is after the machine's query time` : ''}</p> : null}
    {shown && observed(shown) ? <AddToStack item={{ kind: 'reading', envelope: shown, title: `Fault reports ${earlier ? 'before' : 'at or after'} ${moment}` }} label={stale ? 'Stack this held reading' : 'Stack this reading'} /> : null}
    {placed.length ? <>
      <p className={`${styles.nearbyCount} readout`}>{decoded.length} interpreted {decoded.length === 1 ? 'fault' : 'faults'} from {raw.length} returned {raw.length === 1 ? 'record' : 'records'}. A live kernel report can span several records.</p>
      <ol className={styles.nearbyList}>{placed.slice(0, limit).map((fault) => {
        const time = filingTime(fault);
        const representativeTime = times.get(String(fault.RecordId));
        const subject = fault.report?.name ?? fault.report?.code ?? (typeof fault.fields.AppName === 'string' ? fault.fields.AppName : null);
        const firstOfReport = !earlier && fault.report && fault.report.records.length > 1;
        const key = fault.report?.id ? `${fault.Log ?? 'Application'}:report:${fault.report.id}` : `${fault.Log ?? 'Application'}:record:${fault.RecordId}`;
        return <li key={key}>
          <details>
            <summary><span>{fault.kind}{subject ? ` · ${subject}` : ''}</span><span className="readout">{time && Number.isFinite(Date.parse(time)) ? clock.format(new Date(time)) : 'time unknown'}{firstOfReport ? ` · first of ${fault.report?.records.length} returned records` : ` · #${fault.RecordId}`}</span></summary>
            {firstOfReport ? <p className={`${styles.nearbyReach} readout`}>Placed by this side’s first returned record. The detail below comes from its latest returned record, #{fault.RecordId}.</p> : null}
            <FaultDetail fault={fault} at={representativeTime} envelope={shown} rawRecords={raw} showMomentLink={false} />
          </details>
        </li>;
      })}</ol>
      {placed.length > limit ? <button className={styles.action} onClick={() => setLimit((value) => value + 10)}>Show 10 more interpreted faults</button> : null}
    </> : null}
  </section>;
}

function compareFilingTimes(a: string | undefined, b: string | undefined): number {
  if (!a) return b ? 1 : 0;
  if (!b) return -1;
  const aMs = Date.parse(a);
  const bMs = Date.parse(b);
  if (!Number.isFinite(aMs)) return Number.isFinite(bMs) ? 1 : 0;
  if (!Number.isFinite(bMs)) return -1;
  const milliseconds = aMs - bMs;
  if (milliseconds) return milliseconds;
  const fraction = (value: string) => Number((value.match(/\.(\d{1,7})(?:Z|[+-]\d{2}:\d{2})$/)?.[1] ?? '').padEnd(7, '0'));
  return fraction(a) - fraction(b);
}

/** The report channel can retain evidence after the System log has rotated away. */
function KernelReportWindow({ moment }: { moment: string }) {
  const [open, setOpen] = useState(false);
  const [asked, setAsked] = useState(false);
  const outerStart = new Date(Date.parse(moment) - 60 * 60 * 1000).toISOString();
  const outerEnd = new Date(Date.parse(moment) + 60 * 60 * 1000).toISOString();

  return <section className={styles.nearby} aria-labelledby="nearby-kernel-reports-title">
    <div className={styles.nearbyHead}>
      <div>
        <p className="label">Kernel-WHEA channel · separate source</p>
        <h2 id="nearby-kernel-reports-title" className="display">Hardware error reports near this moment</h2>
      </div>
      <button className={styles.action} onClick={() => { setAsked(true); setOpen((value) => !value); }} aria-expanded={open} aria-controls="nearby-kernel-reports-body">
        {open ? 'Hide hardware reports' : 'Read nearby hardware reports'}
      </button>
    </div>
    <p className={styles.nearbyIntro}>Report times say when Windows filed each report, not necessarily when the error occurred. Opened from a restart, this window can include reports filed as Windows started again; a later restart can fall outside it. A nearby report is a lead to inspect.</p>
    <div id="nearby-kernel-reports-body" hidden={!open}>
      <p className={`${styles.nearbyReach} readout`}>Two separate channel reads meet at {clock.format(new Date(moment))}. Each keeps the reports nearest this moment; each has its own coverage and limit.</p>
      <KernelReportSide moment={moment} since={outerStart} before={moment} order="newest" asked={asked} />
      <KernelReportSide moment={moment} since={moment} before={outerEnd} order="oldest" asked={asked} />
    </div>
  </section>;
}

function useHeld<T>(taken: Taken<T>): Reading<T> | null {
  const [held, setHeld] = useState<Reading<T> | null>(null);
  useEffect(() => { if (observed(taken.reading)) setHeld(taken.reading); }, [taken.reading]);
  return observed(taken.reading) ? taken.reading : held;
}

function KernelReportSide({ moment, since, before, order, asked }: {
  moment: string; since: string; before: string; order: 'newest' | 'oldest'; asked: boolean;
}) {
  const earlier = order === 'newest';
  const taken = useReading('whea_window', { source: 'kernel_whea', since, before, order, count: 250 }, asked);
  const shown = useHeld(taken);
  const reports = reportsFromWindow(shown);
  const collection = part<ReportSource & { window_start: string; window_end: string; observed_end: string }>(shown, 'collection');
  const reach = part<ReportReach>(shown, 'coverage');
  const bounds = collection?.window_start && collection.window_end
    ? `${WINDOW_STAMP.format(new Date(collection.window_start))} to ${WINDOW_STAMP.format(new Date(collection.window_end))}` : null;
  const futureEnd = !!collection?.observed_end && Date.parse(collection.observed_end) < Date.parse(collection.window_end);
  const reachText = nearbyReachText(reach ? { ...reach, covered_until: reach.covered_until ?? null } : null, collection ?? null, collection?.window_end ?? before, 'Kernel-WHEA channel');
  const answered = shown?.outcome === 'ok' || shown?.outcome === 'empty';
  const stale = shown !== null && shown !== taken.reading;

  return <section className={styles.nearbySide} aria-labelledby={`kernel-${order}-title`}>
    <h3 id={`kernel-${order}-title`} className="display">{earlier ? 'Before this moment · nearest first' : 'At or after this moment · nearest first'}</h3>
    <OutcomeLine taken={taken} noun="Kernel-WHEA reports" singular="Kernel-WHEA report"
      emptyText={`No Kernel-WHEA report returned ${earlier ? 'before' : 'at or after'} this moment in the queried channel window`} />
    {stale ? <p className={`${styles.nearbyReach} readout`} role="status">Showing the reading taken at {clock.format(new Date(shown.asked_at))}; {taken.state === 'taking' ? 'another take is in progress.' : 'the last completed take did not observe the machine.'}</p> : null}
    {answered && bounds ? <p className={`${styles.nearbyReach} readout`}>{reachText} · queried {bounds}{futureEnd ? ' · requested end is after the machine’s query time' : ''}</p> : null}
    {shown && observed(shown) ? <AddToStack item={{ kind: 'reading', envelope: shown, title: `Kernel-WHEA reports ${earlier ? 'before' : 'at or after'} ${moment}` }} label={stale ? 'Stack this held reading' : 'Stack this reading'} /> : null}
    {shown && observed(shown) && reports.length ? <KernelReports
      reading={shown} reports={reports} range={null} source={collection ?? null} reach={reach}
      inspect={(report) => <ReportDetail report={report} showMomentLink={false} />}
    /> : null}
  </section>;
}

function Rows({ records, reading, listRef, overview = false }: { records: EventRecord[]; reading: Reading<EventRecord[]>; listRef?: Ref<HTMLOListElement>; overview?: boolean }) {
  const [open, setOpen] = useState<RecordId | null>(null);
  const grouped = useMemo(() => byDay(records, (r) => r.TimeCreated), [records]);
  const openRecord = (id: RecordId) => {
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

const DENSITY_BINS = 6;
const LOCAL_STAMP = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
const newestRecord = (rows: EventRecord[]) => rows.reduce((newest, row) => Date.parse(row.TimeCreated) > Date.parse(newest.TimeCreated) ? row : newest);

/** A map of the returned rows only. An empty bin is never a claim about the rest of the log. */
function RecordOverview({ records, selected, onOpen }: { records: EventRecord[]; selected: RecordId | null; onOpen: (id: RecordId) => void }) {
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
        <div><p className="label">Returned sample · {records.length} rows</p><h2 id="record-overview-title" className="display">Where these entries fall</h2></div>
        <p>Each bar counts only the rows below. An empty interval has no returned row; other System log entries may exist. Select a bar or source to inspect a matching row.</p>
      </div>
      <div className={styles.overviewBody}>
        <div className={styles.density}>
          <div className={`${styles.plotTitle} readout`}><span>Logged time · returned rows</span><span>Most in one interval: {peak}</span></div>
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
                    ><span className={`${styles.binCount} readout`}>{bin.length}</span><span className={styles.binBar} style={{ height: `${Math.max(4, (bin.length / peak) * 68)}px` }} /></button>
                  ) : <span className={styles.binEmpty} key={index} aria-hidden="true" />;
                })}
              </div>
              <div className={`${styles.plotAxis} readout`}><span>{LOCAL_STAMP.format(oldest)}</span><span>{LOCAL_STAMP.format(newest)}</span></div>
            </>
          ) : <p className={`${styles.noTime} readout`}>No returned record had a time to plot.</p>}
          {timed.length < records.length ? <p className={`${styles.unplaced} readout`}>{records.length - timed.length} returned records had no usable time.</p> : null}
        </div>
        <div className={styles.sources}>
          <p className={`${styles.plotTitle} readout`}>Sources · top 3 of {providers.size} returned</p>
          {leading.map(([name, entries]) => (
            <button key={name} className={styles.source} onClick={() => onOpen(newestRecord(entries).RecordId)} aria-label={`${name}: ${entries.length} of ${records.length} returned records; open a matching row below`}>
              <span className={styles.sourceLine}><span title={name}>{shortProvider(name) || 'Unnamed source'}</span><strong className="readout">{entries.length} / {records.length}</strong></span>
              <span className={styles.sourceTrack}><span style={{ width: `${(entries.length / records.length) * 100}%` }} /></span>
              <span className={`${styles.sourceAction} readout`}>Open a matching row ↓</span>
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}

function Row({ record, reading, open, onToggle }: { record: EventRecord; reading: Reading<EventRecord[]>; open: boolean; onToggle: () => void }) {
  const t = new Date(record.TimeCreated);
  return (
    <li className={`${styles.row} ${open ? styles.rowOpen : ''}`} data-record={record.RecordId}>
      <button className={styles.rowButton} onClick={onToggle} aria-expanded={open}>
        <span className={`${styles.time} readout`}><span className={styles.srOnly}>{day.format(t)} </span>{clock.format(t)}</span>
        <span className={styles.level}><LevelGlyph level={record.Level} /><span className={`${styles.levelText} readout`}>{record.LevelDisplayName}</span></span>
        <span className={`${styles.provider} readout`}>{shortProvider(record.ProviderName)}</span>
        <span className={`${styles.id} readout`}>event {record.Id}</span>
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
      <details className={styles.rawRecord}>
        <summary className="readout">Raw record · exact returned fields</summary>
        <pre className="readout">{JSON.stringify(record, null, 2)}</pre>
      </details>
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
      <OutcomeLine taken={before.taken} noun="records" singular="record" emptyText="No retained records returned before this moment" />
      {before.rows.length ? (
        <>
          <More before={before} />
          <ol className={styles.beforeRows} ref={before.list}>
            {before.rows.map((r) => (
              <li key={r.RecordId} className={styles.beforeRow} data-record={r.RecordId}>
                <span className={`${styles.time} readout`}><span className={styles.srOnly}>{day.format(new Date(r.TimeCreated))} </span>{clock.format(new Date(r.TimeCreated))}</span>
                <span className={styles.level}><LevelGlyph level={r.Level} /><span className={`${styles.levelText} readout`}>{r.LevelDisplayName}</span></span>
                <span className={`${styles.provider} readout`}>{shortProvider(r.ProviderName)}</span>
                <span className={`${styles.id} readout`}>event {r.Id}</span>
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
  retry: () => void;
  moreCount: number;
  list: Ref<HTMLOListElement>;
  /** The log answered that the requested frame has no older record. */
  atStart: boolean;
  atLimit: boolean;
  atStop: boolean;
}

/**
 * The records before a moment, progressively widened from a small first page.
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
  const { retake } = taken;
  // Design for interrupted work: a wider take that did not observe the machine must not take the
  // narrower one's rows away with it. The frame stands on the last observed envelope.
  const [held, setHeld] = useState<Reading<EventRecord[]> | null>(null);
  useEffect(() => {
    if (observed(taken.reading)) setHeld(taken.reading);
  }, [taken.reading]);
  const rows = section(held, 'records') ?? [];
  const list = useRef<HTMLOListElement>(null);
  const place = useRef<{ id: string; top: number } | null>(null);
  const heldCount = typeof held?.params.count === 'number' ? held.params.count : PAGE;
  const nextCount = Math.min(FRAME_LIMIT, heldCount * 2);

  const rememberPlace = useCallback(() => {
    const first = list.current?.querySelector<HTMLElement>('[data-record]');
    place.current = first?.dataset.record ? { id: first.dataset.record, top: first.getBoundingClientRect().top } : null;
  }, []);
  const more = useCallback(() => {
    rememberPlace();
    if (nextCount === count) retake();
    else setCount(nextCount);
  }, [count, nextCount, rememberPlace, retake]);
  const retry = useCallback(() => {
    rememberPlace();
    retake();
  }, [rememberPlace, retake]);

  useLayoutEffect(() => {
    const saved = place.current;
    if (!saved || held !== taken.reading) return;
    const node = list.current?.querySelector<HTMLElement>(`[data-record="${saved.id}"]`);
    if (node) window.scrollBy(0, node.getBoundingClientRect().top - saved.top);
    place.current = null;
  }, [held, taken.reading]);

  const collection = held?.sections.find((s) => s.name === 'collection')?.data as unknown as { truncated?: boolean | null; stopped?: { kind: string; detail: string } | null } | undefined;
  const heldLimit = held?.params.count;
  return {
    taken, held, rows, more, retry, moreCount: nextCount - heldCount, list,
    atStart: observed(held) && collection?.truncated === false,
    atLimit: heldLimit === FRAME_LIMIT && collection?.truncated === true,
    atStop: observed(held) && collection?.stopped != null,
  };
}

/** The way further back, at the top of the list because that is where the records it asks for appear. */
function More({ before }: { before: Widened }) {
  const status = useRef<HTMLParagraphElement>(null);
  const moreButton = useRef<HTMLButtonElement>(null);
  const hadFocus = useRef(false);
  useLayoutEffect(() => {
    const target = status.current ?? moreButton.current;
    if (target && hadFocus.current) {
      target.focus();
      hadFocus.current = false;
    }
  }, [before.atStart, before.atLimit, before.atStop]);
  if (before.atStart || before.atLimit || before.atStop) {
    const message = before.atStop
      ? 'Windows stopped returning older records here. The reading explains why.'
      : before.atStart
        ? 'Windows returned every retained record before this moment.'
        : `This frame reached ${FRAME_LIMIT.toLocaleString()} records. Choose an earlier moment to continue.`;
    return <>
      <p ref={status} tabIndex={-1} aria-live="polite" className={`${styles.logStart} readout`}>{message}</p>
      {before.atStop ? <button className={`${styles.more} readout`} onClick={() => { if (before.taken.state !== 'taking') { hadFocus.current = true; before.retry(); } }} aria-disabled={before.taken.state === 'taking'}>{before.taken.state === 'taking' ? 'Trying again…' : 'Try this frame again'}</button> : null}
    </>;
  }
  return (
    <button ref={moreButton} className={`${styles.more} readout`} onClick={() => { if (before.taken.state !== 'taking') { hadFocus.current = true; before.more(); } }} onFocus={() => { hadFocus.current = true; }} onBlur={() => { hadFocus.current = false; }} aria-disabled={before.taken.state === 'taking'}>
      {before.taken.state === 'taking' ? 'Taking…' : `${before.moreCount} more before this`}
    </button>
  );
}

function shortProvider(name: string): string {
  return name.replace(/^Microsoft-Windows-/, '');
}
