import { ReactNode } from 'react';
import { EventRecord } from '../api';
import { part } from '../Sections';
import { useApp } from '../store';
import { Mark, Track, TrackId, stopTimes, useTracks } from '../timeline';
import { PRESETS, fmt, presetRange, span } from '../time';
import { Chart } from '../chart/Chart';
import { DayList } from '../chart/DayList';
import { Inspector } from '../chart/Inspector';
import { AxisPage, usePhone } from '../chart/Page';
import { count } from '../chart/words';
import { FaultDetail, Stop, StopDetail } from './Crashes';
import summaryStyles from '../chart/Summary.module.css';
import styles from './Stops.module.css';

const TRACKS: TrackId[] = ['stops', 'faults', 'hardware', 'reliability'];

/**
 * Crashes on the time axis: the stops the machine did not plan and the programs that failed while
 * it kept running, in the shared range. Each stop keeps all of its evidence times on its own small
 * axis, because Windows records them separately and they can disagree. The full evidence (every
 * field, the cited raw records, the changes before it, the dump header) opens in the inspector.
 */
export function Stops() {
  const range = useApp((s) => s.range);
  const phone = usePhone();
  const tracks = useTracks(range, TRACKS);
  const [stops, faults] = tracks;
  return (
    <AxisPage
      title="Crashes"
      question="When did the machine stop without shutting down, and which programs failed while it ran."
      summary={<p className={summaryStyles.single}>{crashSentences(stops, faults)}</p>}
      chart={phone ? null : <Chart range={range} tracks={tracks} label="Stops and faults on one time axis" />}
      inspector={<Inspector tracks={tracks} range={range} sheet={phone} detail={(mark) => <MarkEvidence mark={mark} track={tracks.find((t) => t.id === mark.track) ?? null} />} />}
      list={<>
        <StopList track={stops} />
        <div className={styles.faults}>
          <DayList tracks={[faults]} range={range} heading="Program faults, day by day" />
        </div>
      </>}
    />
  );
}

function crashSentences(stops: Track, faults: Track): string {
  const out: string[] = [];
  if (stops.reach.state === 'failed') out.push('Unplanned stops could not be read.');
  else if (stops.reach.state === 'read') {
    out.push(stops.marks.length ? `${cap(count(stops.marks.length, ['unplanned stop', 'unplanned stops']))} in this range.` : 'No unplanned stop in this range.');
  }
  if (faults.reach.state === 'failed') out.push('Program faults could not be read.');
  else if (faults.reach.state === 'read') out.push(`${cap(count(faults.total ?? 0, ['program fault', 'program faults']))} filed in what was read.`);
  return out.join(' ');
}

function cap(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Each stop as a row: what Windows named, how long it was down, and its evidence times on its own axis. */
function StopList({ track }: { track: Track }) {
  const select = useApp((s) => s.select);
  const setRange = useApp((s) => s.setRange);
  const range = useApp((s) => s.range);
  const selection = useApp((s) => s.selection);
  const all = (part<Stop[]>(track.taken.reading, 'stops') ?? []);
  const outside = all.filter((stop) => !stopTimes(stop).some((t) => t.at >= range.from && t.at < range.to));
  const older = outside.filter((stop) => stopTimes(stop).every((t) => t.at < range.from));
  return (
    <section className={styles.stops} aria-labelledby="stops-heading">
      <h2 id="stops-heading" className={styles.heading}>Unplanned stops</h2>
      {track.reach.state !== 'read' ? <p className={styles.quiet}>{track.reach.state === 'waiting' ? 'Reading…' : 'The stop reading did not answer, so nothing can be said about stops in this range.'}</p>
        : track.marks.length === 0 ? <p className={styles.quiet}>None returned in this range.</p> : null}
      <ol className={styles.stopList}>
        {[...track.marks].sort((a, b) => b.at - a.at).map((mark) => (
          <li key={mark.id}>
            <button className={`${styles.stop} ${selection?.kind === 'mark' && selection.id === mark.id ? styles.stopChosen : ''}`}
              aria-pressed={selection?.kind === 'mark' && selection.id === mark.id}
              onClick={() => select({ kind: 'mark', id: mark.id, at: new Date(mark.at).toISOString() })}>
              <span className={styles.stopHead}>
                <span className={`${styles.stopWhen} readout`}>{fmt.when(mark.at)}</span>
                <span className={styles.stopTitle}>{mark.line || 'Unplanned stop'}</span>
              </span>
              <EvidenceAxis mark={mark} />
            </button>
          </li>
        ))}
      </ol>
      {older.length ? (
        <p className={styles.older}>
          {count(older.length, ['more stop', 'more stops'])} on record before this range, the latest on {fmt.day(Math.max(...older.flatMap((s) => stopTimes(s).map((t) => t.at))))}.
          {range.preset !== '30d' ? <> <button className={styles.inline} onClick={() => setRange(presetRange('30d'))}>Show {PRESETS[0].long}</button></> : null}
        </p>
      ) : null}
    </section>
  );
}

/**
 * A stop's own axis. The times sit at their true spacing from the first to the last, so a gap
 * between the last record and Windows' estimate is visible rather than averaged away.
 */
function EvidenceAxis({ mark }: { mark: Mark }) {
  const lo = Math.min(...mark.times.map((t) => t.at));
  const hi = Math.max(...mark.times.map((t) => t.at));
  const width = Math.max(1, hi - lo);
  return (
    <span className={styles.axis}>
      <span className={styles.track} aria-hidden="true">
        <span className={styles.down} style={{ left: 0, right: 0 }} />
        {mark.times.map((t) => (
          <span key={t.label} className={`${styles.pin} ${styles[pinClass(t.label)]}`} style={{ left: `${((t.at - lo) / width) * 100}%` }} />
        ))}
      </span>
      <span className={styles.pins}>
        {mark.times.map((t, i) => (
          <span key={t.label} className={styles.pinLabel}>
            <span className={`${styles.pinGlyph} ${styles[pinClass(t.label)]}`} aria-hidden="true" />
            {t.label} <span className="readout">{fmt.second(t.at)}</span>{i > 0 ? <span className={styles.delta}> +{span(t.at - mark.times[0].at)}</span> : null}
          </span>
        ))}
      </span>
    </span>
  );
}

function pinClass(label: string): string {
  return label === 'Next start' || label === 'Restart announced' ? 'pinStart' : label === 'Last System record' ? 'pinLast' : 'pinEstimate';
}

/** The facts a person reads first about a stop; every field follows behind one disclosure. */
export function StopFacts({ stop }: { stop: Stop }) {
  const rows: [string, ReactNode][] = [];
  rows.push(['Bug check', stop.bugcheck ? <>{stop.bugcheck.name ?? 'Unnamed'} <span className="readout">{stop.bugcheck.code}</span></>
    : stop.no_bugcheck_recorded === null ? <span className={styles.unknown}>Unknown: the logs read cannot establish one</span>
      : stop.no_bugcheck_recorded ? 'None recorded' : <span className={styles.unknown}>Not returned</span>]);
  const estimate = stop.stopped_at ? Date.parse(stop.stopped_at) : null;
  const start = stop.started_at ? Date.parse(stop.started_at) : null;
  rows.push(['Down', estimate !== null && start !== null ? <>{span(start - estimate)} <span className={styles.derived}>from Windows' estimate to the next start</span></> : <span className={styles.unknown}>Unknown: a time is missing</span>]);
  rows.push(['Dump', stop.dump ? <>{stop.dump.name}<span className={styles.derived}> · {matchedBy(stop.dump.matched_by)}</span></> : <span className={styles.unknown}>{stop.dump_inventory_complete ? 'None matched on disk' : 'None matched; the inventory was incomplete'}</span>]);
  if (stop.last_record_before) {
    const last = stop.last_record_before;
    rows.push(['Last record', <>{last.ProviderName} {last.Id} · {last.LevelDisplayName}<span className={styles.message}>{last.Message?.split('\n')[0]}</span></>]);
  }
  if (typeof stop.power?.whea_boot_error_count === 'number' && stop.power.whea_boot_error_count > 0) {
    rows.push(['Hardware errors at boot', <span className="readout">{stop.power.whea_boot_error_count}</span>]);
  }
  return (
    <dl className={styles.facts}>
      {rows.map(([name, value]) => <div key={name} className={styles.fact}><dt>{name}</dt><dd>{value}</dd></div>)}
    </dl>
  );
}

/** How the reading paired the dump with the stop, in its own terms (see the crash reading's description). */
function matchedBy(how: string): string {
  if (how === '1001') return 'named by the bug check record (event 1001)';
  if (how === 'report') return 'attached to the error report';
  if (how === 'time') return 'matched by time: the newest dump written as it restarted';
  return `matched by ${how}`;
}

/** In the inspector: the first facts, then everything the old reading showed, one disclosure down. */
function MarkEvidence({ mark, track }: { mark: Mark; track: Track | null }) {
  const reading = track?.taken.reading ?? null;
  if (mark.stop) {
    return (
      <>
        <StopFacts stop={mark.stop} />
        <details className={styles.more}>
          <summary>Every field, the cited records and the changes before it</summary>
          <div className={styles.moreBody}><StopDetail stop={mark.stop} envelope={reading} /></div>
        </details>
      </>
    );
  }
  if (mark.fault) {
    const records = part<EventRecord[]>(reading, 'records') ?? [];
    return (
      <details className={styles.more}>
        <summary>The decoded report and its raw record</summary>
        <div className={styles.moreBody}><FaultDetail fault={mark.fault} envelope={reading} rawRecords={records} showMomentLink={false} /></div>
      </details>
    );
  }
  return null;
}
