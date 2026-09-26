import { ReactNode, useEffect, useRef } from 'react';
import { Range, fixedRange, fmt, nearWindow, span } from '../time';
import { Mark, Track, coveredShare, within } from '../timeline';
import { Selection, useApp } from '../store';
import { count, reachWords } from './words';
import styles from './Inspector.module.css';

/**
 * The inspector: one raised sheet that holds what was picked on the chart, and what each source
 * returned near it. At rest it is the coverage ledger, which is the question a person should ask
 * before reading any stretch of the chart: what was read here at all?
 *
 * It says "near" and never "because". Two things close in time are shown together so a person can
 * compare them; the sheet does not rank them or claim one led to the other.
 */
export function Inspector({ tracks, range, resolve, detail, sheet = false }: {
  tracks: Track[];
  range: Range;
  /** Marks a view knows about beyond the tracks' own, such as a System log row. */
  resolve?: (id: string) => Mark | null;
  /** The full evidence a view can show for a mark: a stop's facts, a record's fields. */
  detail?: (mark: Mark) => ReactNode;
  /** On a phone the inspector is a sheet over the list, with its own close. */
  sheet?: boolean;
}) {
  const selection = useApp((s) => s.selection);
  const select = useApp((s) => s.select);
  const heading = useRef<HTMLHeadingElement>(null);
  const mark = selection?.kind === 'mark' ? tracks.flatMap((t) => t.marks).find((m) => m.id === selection.id) ?? resolve?.(selection.id) ?? null : null;
  const key = selection ? JSON.stringify(selection) : '';

  // A new pick moves focus to the sheet only on a phone, where the sheet covers the list; on a desk
  // the chart keeps focus so the arrows keep working.
  useEffect(() => { if (sheet && key) heading.current?.focus(); }, [key, sheet]);
  // The phone's sheet closes with Escape, like any sheet over content.
  useEffect(() => {
    if (!sheet || !key) return;
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') select(null); };
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, [sheet, key, select]);

  if (sheet && !selection) return null;

  return (
    <aside className={`${styles.inspector} ${sheet ? styles.sheet : ''}`} aria-label="Inspector">
      {selection ? (
        <div className={styles.top}>
          <span className={styles.kicker}>{selection.kind === 'stretch' ? 'Picked stretch' : mark ? 'Picked mark' : 'Picked moment'}</span>
          <button className={styles.close} onClick={() => select(null)} aria-label="Clear the pick">Clear</button>
        </div>
      ) : null}
      {!selection ? <Ledger tracks={tracks} range={range} heading={heading} />
        : selection.kind === 'stretch' ? <Stretch selection={selection} tracks={tracks} heading={heading} />
          : <Moment at={Date.parse(selection.at)} mark={mark} tracks={tracks} range={range} heading={heading} detail={detail} />}
    </aside>
  );
}

function Ledger({ tracks, range, heading }: { tracks: Track[]; range: Range; heading: React.RefObject<HTMLHeadingElement | null> }) {
  const setView = useApp((s) => s.setView);
  return (
    <>
      <h2 ref={heading} tabIndex={-1} className={styles.title}>What was read</h2>
      <p className={styles.lede}>Each source looked at its own stretch of this range. Where a row is hatched, nothing can be said about that time from that source.</p>
      <ul className={styles.ledger}>
        {tracks.map((track) => (
          <li key={track.id} className={`${styles.ledgerRow} ${track.reach.state === 'read' ? '' : styles.unread}`}>
            <span className={styles.ledgerName}>
              {track.view ? <button className={styles.link} onClick={() => setView(track.view!)}>{track.label}</button> : track.label}
              <span className={styles.ledgerSource}>{track.source}</span>
            </span>
            <span className={styles.ledgerReach}>{reachWords(track, range)}</span>
          </li>
        ))}
      </ul>
      <p className={styles.hint}>Pick a moment or a mark on the chart to see what every source returned near it.</p>
    </>
  );
}

function Moment({ at, mark, tracks, range, heading, detail }: {
  at: number; mark: Mark | null; tracks: Track[]; range: Range; heading: React.RefObject<HTMLHeadingElement | null>; detail?: (mark: Mark) => ReactNode;
}) {
  const setView = useApp((s) => s.setView);
  const setRange = useApp((s) => s.setRange);
  const view = useApp((s) => s.view);
  const near = nearWindow(range);
  const lo = mark ? Math.min(...mark.times.map((t) => t.at)) : at;
  const hi = mark ? Math.max(...mark.times.map((t) => t.at)) : at;
  const zoomSpan = Math.max(60_000, (range.to - range.from) / 7);
  return (
    <>
      {mark ? (
        <>
          <h2 ref={heading} tabIndex={-1} className={`${styles.title} ${styles[`tone_${mark.tone}`]}`}>{mark.title}</h2>
          {mark.line ? <p className={styles.markLine}>{mark.line}</p> : null}
          <ol className={styles.times}>
            {mark.times.map((t, i) => (
              <li key={t.label}>
                <span className={`${styles.timeValue} readout`}>{fmt.whenExact(t.at)}</span>
                <span className={styles.timeLabel}>{t.label}{i > 0 ? <span className={styles.timeDelta}> · {span(t.at - mark.times[0].at)} after</span> : null}</span>
              </li>
            ))}
          </ol>
          {mark.times.length > 1 ? <p className={styles.note}>Windows keeps these times in separate records and they can disagree. None is the stop; each is shown.</p> : null}
        </>
      ) : (
        <>
          <h2 ref={heading} tabIndex={-1} className={`${styles.title} readout`}>{fmt.whenExact(at)}</h2>
          <p className={styles.markLine}>{fmt.dayLong(at)}</p>
        </>
      )}

      <div className={styles.actions}>
        {view !== 'record' ? <button className={styles.action} onClick={() => setView('record')}>The System log around this</button> : null}
        {mark?.track === 'stops' && view !== 'crashes' ? <button className={styles.action} onClick={() => setView('crashes')}>This stop in Crashes</button> : null}
        {zoomSpan < range.to - range.from ? <button className={styles.actionQuiet} onClick={() => setRange(fixedRange((lo + hi) / 2 - zoomSpan / 2, (lo + hi) / 2 + zoomSpan / 2))}>Zoom to {span(zoomSpan)} around it</button> : null}
      </div>

      {mark && detail ? <div className={styles.detail}>{detail(mark)}</div> : null}

      <Near tracks={tracks} from={lo - near} to={hi + near} title={`Near ${mark ? 'it' : 'this moment'}: ${span(near)} either side`} exclude={mark?.id} />
    </>
  );
}

function Stretch({ selection, tracks, heading }: { selection: Extract<Selection, { kind: 'stretch' }>; tracks: Track[]; heading: React.RefObject<HTMLHeadingElement | null> }) {
  const setRange = useApp((s) => s.setRange);
  return (
    <>
      <h2 ref={heading} tabIndex={-1} className={`${styles.title} readout`}>{span(selection.to - selection.from)}</h2>
      <p className={styles.markLine}><span className="readout">{fmt.whenExact(selection.from)}</span> to <span className="readout">{fmt.whenExact(selection.to)}</span></p>
      <div className={styles.actions}>
        <button className={styles.action} onClick={() => setRange(fixedRange(selection.from, selection.to))}>Zoom to this stretch</button>
      </div>
      <Near tracks={tracks} from={selection.from} to={selection.to} title="In this stretch" />
    </>
  );
}

/** Each source, and what it returned between two times, or that it did not read there. */
function Near({ tracks, from, to, title, exclude }: { tracks: Track[]; from: number; to: number; title: string; exclude?: string }) {
  const select = useApp((s) => s.select);
  return (
    <section className={styles.near} aria-label={title}>
      <h3 className={styles.nearTitle}>{title}</h3>
      <p className={styles.note}>Near in time is not a cause. These are what each source returned between <span className="readout">{fmt.minute(from)}</span> and <span className="readout">{fmt.minute(to)}</span>{fmt.day(from) !== fmt.day(to) ? ` (${fmt.day(from)} to ${fmt.day(to)})` : ''}.</p>
      <ul className={styles.nearList}>
        {tracks.map((track) => {
          const share = coveredShare(track, from, to);
          if (track.reach.state !== 'read' || share === 0) {
            return (
              <li key={track.id} className={`${styles.nearRow} ${styles.unread}`}>
                <span className={styles.nearName}>{track.label}</span>
                <span className={styles.nearValue}>{track.reach.state === 'waiting' ? 'Reading…' : track.reach.state === 'failed' ? 'Not read: the reading did not answer' : track.kind === 'samples' ? 'Not measured here' : 'Not read here'}</span>
              </li>
            );
          }
          const found = within(track, from, to);
          const marks = found.marks.filter((m) => m.id !== exclude);
          const partial = share < 0.999;
          let value: string;
          if (track.kind === 'samples') {
            const values = (track.samples ?? []).filter((s) => s.at >= from && s.at <= to && s.value !== null).map((s) => s.value as number);
            value = values.length ? `${Math.min(...values)} to ${Math.max(...values)}% over ${count(values.length, track.noun)}` : 'No samples here';
          } else if (track.bins.length && found.count !== null) {
            const levels = found.levels ? levelWords(found.levels) : '';
            value = found.count ? `${count(found.count, track.noun)}${levels ? `: ${levels}` : ''}` : `No ${track.noun[1]}`;
            // Counts come in whole buckets, so say which stretch they cover when it is wider than the question.
            if (found.binFrom < from || found.binTo > to) value += `, in the buckets from ${fmt.minute(found.binFrom)} to ${fmt.minute(found.binTo)}`;
          } else value = marks.length ? count(marks.length, track.noun) : `No ${track.noun[1]}`;
          return (
            <li key={track.id} className={styles.nearRow}>
              <span className={styles.nearName}>{track.label}</span>
              <span className={styles.nearValue}>{value}{partial ? <span className={styles.partial}> · read for part of this window only</span> : null}</span>
              {marks.length ? (
                <ul className={styles.nearMarks}>
                  {marks.slice(0, 4).map((m) => (
                    <li key={m.id}>
                      <button className={styles.nearMark} onClick={() => select({ kind: 'mark', id: m.id, at: new Date(m.at).toISOString() })}>
                        <span className={`${styles.nearMarkTime} readout`}>{fmt.minute(m.at)}</span>
                        <span className={styles.nearMarkTitle}>{m.title}</span>
                      </button>
                    </li>
                  ))}
                  {marks.length > 4 ? <li className={styles.more}>and {marks.length - 4} more</li> : null}
                </ul>
              ) : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function levelWords(levels: [number, number, number, number]): string {
  const names: [string, string][] = [['critical', 'critical'], ['error', 'errors'], ['warning', 'warnings'], ['information', 'information']];
  return levels.map((n, i) => (n ? count(n, names[i]) : '')).filter(Boolean).join(', ');
}
