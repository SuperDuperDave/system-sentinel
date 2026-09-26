import { observed } from '../api';
import { AddEvidence } from '../AddEvidence';
import { OutcomeLine } from '../Outcome';
import { Section, basisOf, part } from '../Sections';
import { useReading } from '../useReading';
import { useApp } from '../store';
import styles from './ReliabilityHistory.module.css';

interface ReliabilityDay {
  day: string;
  index_last: number | null;
  index_min: number | null;
  records: Record<string, number> | null;
  event_types: { source: string; event_id: number | null; count: number }[] | null;
}

interface Rollup {
  from: string | null;
  to: string | null;
  days: ReliabilityDay[];
}

interface RawRecord {
  TimeGenerated: string;
  SourceName: string;
  ProductName: string | null;
  EventIdentifier: number;
  Message: string | null;
}

const UTC_DAY = 86_400_000;
const PLOT_WIDTH = 1000;
const PLOT_TOP = 8;
const PLOT_HEIGHT = 72;
const shortDate = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' });
const longDate = new Intl.DateTimeFormat(undefined, { year: 'numeric', month: 'long', day: 'numeric', timeZone: 'UTC' });

/** The dates for which Windows returned rows, with absent dates kept distinct from zero records. */
function calendar(days: ReliabilityDay[]): { day: string; row: ReliabilityDay | null }[] {
  if (!days.length) return [];
  const rows = new Map(days.map((row) => [row.day, row]));
  const first = Date.parse(`${days[0].day}T00:00:00Z`);
  const last = Date.parse(`${days[days.length - 1].day}T00:00:00Z`);
  if (!Number.isFinite(first) || !Number.isFinite(last) || last < first) return [];
  return Array.from({ length: Math.min(367, Math.floor((last - first) / UTC_DAY) + 1) }, (_, i) => {
    const day = new Date(first + i * UTC_DAY).toISOString().slice(0, 10);
    return { day, row: rows.get(day) ?? null };
  });
}

function recordCount(row: ReliabilityDay): number | null {
  return row.records === null ? null : Object.values(row.records).reduce((total, count) => total + count, 0);
}

function eventLabel(source: string, id: number | null): string {
  if (source === 'Microsoft-Windows-WindowsUpdateClient' && id === 19) return 'Windows Update · installed successfully · event 19';
  if (source === 'Microsoft-Windows-WindowsUpdateClient' && id === 20) return 'Windows Update · installation failed · event 20';
  if (source === 'Application Error' && id === 1000) return 'Application crash · event 1000';
  if (source === 'Application Hang' && id === 1002) return 'Application hang · event 1002';
  if (source === 'EventLog' && id === 6008) return 'Unexpected shutdown · event 6008';
  return `${source} · event ${id ?? 'unnamed'}`;
}

function dateLabel(day: string, long = false): string {
  const date = new Date(`${day}T00:00:00Z`);
  return (long ? longDate : shortDate).format(date);
}

function indexPoint(value: number, position: number, count: number): [number, number] {
  const x = ((position + 0.5) / count) * PLOT_WIDTH;
  const y = PLOT_TOP + ((10 - Math.max(1, Math.min(10, value))) / 9) * PLOT_HEIGHT;
  return [x, y];
}

/** Windows' reliability history, not a health score: bars count the events Windows returned. */
export function ReliabilityHistory() {
  const chosen = useApp((state) => state.crashesView.reliabilityDay);
  const setCrashesView = useApp((state) => state.setCrashesView);
  const taken = useReading('reliability', { days: 30 }, true, { hold: 'same-params' });
  const rollup = part<Rollup>(taken.reading, 'days');
  const records = part<RawRecord[]>(taken.reading, 'records') ?? [];
  const days = rollup ? calendar(rollup.days) : [];
  const selected = days.find((day) => day.day === chosen) ?? [...days].reverse().find((day) => day.row);
  const row = selected?.row ?? null;
  const max = Math.max(1, ...days.map((day) => day.row ? recordCount(day.row) ?? 0 : 0));
  const selectedCount = row ? recordCount(row) : null;
  const indexes = days.map((day) => day.row?.index_last ?? null);
  const indexPath = indexes.map((value, i) => {
    if (value == null || !Number.isFinite(value)) return '';
    const [x, y] = indexPoint(value, i, days.length);
    return `${i > 0 && indexes[i - 1] != null && Number.isFinite(indexes[i - 1]) ? 'L' : 'M'} ${x.toFixed(1)} ${y.toFixed(1)}`;
  }).join(' ');
  const selectedRecords = row ? records.filter((record) => record.TimeGenerated?.startsWith(row.day)) : [];

  return (
    <Section
      title="Windows' reliability record"
      cls="derived"
      basis={basisOf(taken.reading, 'days')}
      note="Last 30 days requested · UTC days · this is Windows' own account"
      controls={taken.reading ? <AddEvidence item={{ kind: 'reading', envelope: taken.reading, title: 'Windows reliability record, last 30 days' }} /> : null}
    >
      <p className={styles.intro}>Windows keeps both fault reports and informational events here, including successful updates. The bars count all returned events; the selected day names each type. Its index is Windows' measure, not a cause finding.</p>
      <OutcomeLine taken={taken} noun={taken.reading?.count === null ? 'reliability history' : 'reliability events'} singular="reliability event" emptyText="Windows returned no reliability history for this window" />
      {observed(taken.reading) && days.length > 0 ? (
        <>
          <figure className={styles.figure}>
            <p className={`${styles.plotLabel} label`}>Windows stability index <span>1 least stable · 10 most stable</span></p>
            {indexPath ? (
              <div className={styles.indexPlot}>
                <div className={`${styles.indexAxis} readout`} aria-hidden="true"><span>10</span><span>1</span></div>
                <svg viewBox={`0 0 ${PLOT_WIDTH} 88`} preserveAspectRatio="none" role="img" aria-label="Windows' last reported stability index by day; gaps mean no index was returned">
                  <line className={styles.gridline} x1="0" y1="8" x2={PLOT_WIDTH} y2="8" vectorEffect="non-scaling-stroke" />
                  <line className={styles.gridline} x1="0" y1="80" x2={PLOT_WIDTH} y2="80" vectorEffect="non-scaling-stroke" />
                  <path className={styles.indexLine} d={indexPath} fill="none" vectorEffect="non-scaling-stroke" />
                  {indexes.map((value, i) => {
                    if (value == null || !Number.isFinite(value)) return null;
                    const [x, y] = indexPoint(value, i, days.length);
                    return <circle key={days[i].day} className={selected?.day === days[i].day ? styles.indexSelected : styles.indexDot} cx={x} cy={y} r={selected?.day === days[i].day ? 4 : 2.25} vectorEffect="non-scaling-stroke" />;
                  })}
                </svg>
              </div>
            ) : <p className={`${styles.noIndex} readout`}>Windows returned no stability index in these daily rows.</p>}
            <p className={`${styles.plotLabel} label`}>Reliability events <span>all returned types</span></p>
            <div className={styles.plot} role="group" aria-label="Reliability records by UTC day">
              {days.map(({ day, row: entry }) => {
                const count = entry ? recordCount(entry) : null;
                const active = selected?.day === day;
                return (
                  <button
                    key={day}
                    type="button"
                    className={`${styles.day} ${entry ? '' : styles.missing} ${active ? styles.active : ''}`}
                    aria-label={`${dateLabel(day, true)} UTC: ${count === null ? entry ? 'event count unavailable' : 'no daily row returned' : `${count} reliability ${count === 1 ? 'event' : 'events'} returned`}${entry?.index_last == null ? '' : `, last index ${entry.index_last}`}`}
                    aria-pressed={active}
                    onClick={() => setCrashesView({ reliabilityDay: day })}
                  >
                    <span className={styles.barTrack}>
                      {count === null ? <span className={styles.absent} /> : count === 0 ? <span className={styles.zero} /> : <span className={styles.bar} style={{ height: `${Math.max(10, (count / max) * 100)}%` }} />}
                    </span>
                    <span className={styles.tick} />
                  </button>
                );
              })}
            </div>
            <figcaption className={`${styles.caption} readout`}>
              <span>{dateLabel(days[0].day)}</span>
              <span>event count by day · bar height relative to this window</span>
              <span>{dateLabel(days[days.length - 1].day)}</span>
            </figcaption>
          </figure>
          <p className={`${styles.key} readout`}>A dot means the returned event count is zero. A short dash means the count is unknown: no daily row was returned, or the event source did not answer.</p>
          <label className={`${styles.pickerLabel} label`} htmlFor="reliability-day">Inspect a day</label>
          <select id="reliability-day" className={styles.picker} value={selected?.day ?? ''} onChange={(event) => setCrashesView({ reliabilityDay: event.target.value })}>
            {days.map(({ day, row: entry }) => <option key={day} value={day}>{dateLabel(day, true)} · {entry ? recordCount(entry) === null ? 'event count unavailable' : `${recordCount(entry)} events` : 'no daily row'}</option>)}
          </select>
          {row ? (
            <div className={styles.inspect} aria-live="polite">
              <h3 className={styles.dayTitle}>{dateLabel(row.day, true)} <span className="readout">UTC</span></h3>
              <p className={styles.summary}>
                {selectedCount === null ? 'No event count could be established for this day.' : `Windows returned ${selectedCount} reliability ${selectedCount === 1 ? 'event' : 'events'}.`}
                {row.index_last == null ? ' No stability index was returned for this day.' : ` Its last reported stability index was ${row.index_last.toFixed(1)} of 10${row.index_min == null ? '' : `; its lowest hourly value was ${row.index_min.toFixed(1)}`}.`}
              </p>
              {row.event_types?.length ? (
                <dl className={styles.sources}>
                  {row.event_types.map(({ source, event_id, count }) => <div key={`${source}:${event_id}`}><dt>{eventLabel(source, event_id)}</dt><dd className="readout">{count}</dd></div>)}
                </dl>
              ) : null}
              {selectedRecords.length ? (
                <details className={styles.raw}>
                  <summary>Raw records from this day · {selectedRecords.length}</summary>
                  <pre className="readout">{JSON.stringify(selectedRecords, null, 2)}</pre>
                </details>
              ) : null}
            </div>
          ) : selected ? <p className={styles.summary}>Windows returned no daily row for {dateLabel(selected.day, true)}. This day is unknown in this reading.</p> : null}
        </>
      ) : null}
    </Section>
  );
}
