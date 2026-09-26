import { Range, dayStarts, fmt, startOfDay } from '../time';
import { Mark, Track, coverageOf } from '../timeline';
import { useApp } from '../store';
import { count, list, lower } from './words';
import styles from './DayList.module.css';

/**
 * The marks, day by day, newest first: the canonical list, and the whole view on a phone.
 *
 * Every day says which sources did not read it, so a quiet day that nobody looked at never reads
 * as a quiet day. Runs of days with nothing returned and the same coverage fold into one row.
 */

type Item =
  | { kind: 'mark'; mark: Mark }
  | { kind: 'count'; key: string; at: number; tone: Mark['tone']; title: string; line: string; from: number; to: number };

interface Day { from: number; to: number; items: Item[]; unread: string[]; partial: string[] }

export function DayList({ tracks, range, heading = 'Day by day', unreadNote = true }: {
  tracks: Track[];
  range: Range;
  heading?: string;
  /** Off where the page's own sentences already say which sources were not read at all. */
  unreadNote?: boolean;
}) {
  const select = useApp((s) => s.select);
  const selection = useApp((s) => s.selection);
  const chosen = selection?.kind === 'mark' ? selection.id : null;
  const days = build(tracks, range);
  const failed = tracks.filter((t) => t.reach.state === 'failed');
  const unmeasured = tracks.filter((t) => t.kind === 'samples' && t.reach.state === 'read' && !t.reach.spans.length);
  const rows = fold(days);

  return (
    <section className={styles.days} aria-labelledby="days-heading">
      <h2 id="days-heading" className={styles.heading}>{heading}</h2>
      {unreadNote && (failed.length || unmeasured.length) ? (
        <p className={styles.always}>
          <span className={styles.hatch} aria-hidden="true" />
          <span>
            {failed.length ? <>Not read on any day: {list(failed.map((t) => t.label))}, because {failed.length === 1 ? 'the reading' : 'those readings'} did not answer. </> : null}
            {unmeasured.length ? <>{list(unmeasured.map((t) => t.label))}: no samples stored, so nothing was measured.</> : null}
          </span>
        </p>
      ) : null}
      <ol className={styles.list}>
        {rows.map((row) => (
          <li key={row.from} className={styles.day}>
            <h3 className={styles.dayHead}>
              <span className={styles.dayName}>{row.days > 1 ? `${fmt.day(row.from)} to ${fmt.day(row.last)}` : fmt.day(row.from)}</span>
              {row.items.length === 0 ? <span className={styles.quiet}>{quietWords(row, tracks)}</span> : null}
            </h3>
            {row.unread.length || row.partial.length ? (
              <p className={styles.coverage}>
                <span className={styles.hatch} aria-hidden="true" />
                <span>{[row.unread.length ? `Not read: ${list(row.unread)}` : '', row.partial.length ? `Read for part of the day: ${list(row.partial)}` : ''].filter(Boolean).join('. ')}</span>
              </p>
            ) : null}
            {row.items.length ? (
              <ul className={styles.items}>
                {row.items.map((item) => {
                  const id = item.kind === 'mark' ? item.mark.id : item.key;
                  const tone = item.kind === 'mark' ? item.mark.tone : item.tone;
                  const at = item.kind === 'mark' ? item.mark.at : item.at;
                  const title = item.kind === 'mark' ? item.mark.title : item.title;
                  const line = item.kind === 'mark' ? item.mark.line : item.line;
                  return (
                    <li key={id}>
                      <button
                        className={`${styles.item} ${chosen === id ? styles.itemChosen : ''}`}
                        aria-pressed={chosen === id}
                        onClick={() => item.kind === 'mark'
                          ? select({ kind: 'mark', id, at: new Date(item.mark.at).toISOString() })
                          : select({ kind: 'stretch', from: item.from, to: item.to })}
                      >
                        <span className={`${styles.time} readout`}>{fmt.minute(at)}</span>
                        <span className={`${styles.glyph} ${styles[`tone_${tone}`]}`} aria-hidden="true" />
                        <span className={styles.text}>
                          <span className={styles.title}>{title}</span>
                          {line ? <span className={styles.line}>{line}</span> : null}
                          {item.kind === 'mark' && item.mark.times.length > 1 ? (
                            <span className={`${styles.times} readout`}>{item.mark.times.map((t) => `${shortLabel(t.label)} ${fmt.second(t.at)}`).join(' · ')}</span>
                          ) : null}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            ) : null}
          </li>
        ))}
      </ol>
    </section>
  );
}

function shortLabel(label: string): string {
  return label === 'Last System record' ? 'last record' : label === "Windows' stop estimate" ? 'estimate' : label === 'Next start' ? 'start' : label.toLowerCase();
}

function quietWords(row: Folded, tracks: Track[]): string {
  const read = tracks.filter((t) => t.reach.state === 'read' && t.kind === 'events' && !row.unread.includes(t.label));
  return read.length ? `Nothing returned by ${list(read.map((t) => lower(t.label)))}` : 'Nothing was read';
}

function build(tracks: Track[], range: Range): Day[] {
  const starts = dayStarts(range.from, range.to).reverse();
  return starts.map((start) => {
    const next = new Date(start);
    next.setDate(next.getDate() + 1);
    const from = Math.max(start, range.from);
    const to = Math.min(next.getTime(), range.to);
    const items: Item[] = [];
    const unread: string[] = [];
    const partial: string[] = [];
    for (const track of tracks) {
      if (track.reach.state !== 'read') continue;
      if (track.kind === 'samples' && !track.reach.spans.length) continue;
      const covered = coverageOf(track, from, to, range);
      if (covered === 'none') { unread.push(track.label); continue; }
      if (covered === 'part') partial.push(track.label);
      const marks = track.marks.filter((m) => m.at >= from && m.at < to);
      if (track.id === 'faults' && marks.length > 4) {
        const apps = new Map<string, number>();
        for (const m of marks) apps.set(m.title, (apps.get(m.title) ?? 0) + 1);
        items.push({ kind: 'count', key: `faults:${from}`, at: marks[0].at, tone: 'ink', from, to,
          title: count(marks.length, ['program fault', 'program faults']),
          line: [...apps.entries()].sort((a, b) => b[1] - a[1]).slice(0, 3).map(([name, n]) => `${name.replace(/^[^:]+: /, '')}${n > 1 ? ` ×${n}` : ''}`).join(', ') });
      } else items.push(...marks.map((mark) => ({ kind: 'mark' as const, mark })));
      if (track.bins.length && (track.id === 'hardware' || track.id === 'kernel' || track.id === 'log')) {
        const bins = track.bins.filter((b) => b.from >= from && b.from < to && b.count);
        if (!bins.length) continue;
        const total = bins.reduce((n, b) => n + (b.count ?? 0), 0);
        const peak = bins.reduce((a, b) => ((b.count ?? 0) > (a.count ?? 0) ? b : a));
        if (track.id === 'log') {
          const levels = bins.reduce((acc, b) => { b.levels?.forEach((n, i) => { acc[i] += n; }); return acc; }, [0, 0, 0, 0]);
          const errors = levels[1];
          const warnings = levels[2];
          if (!errors && !warnings) continue;
          items.push({ kind: 'count', key: `log:${from}`, at: peak.from, tone: 'ink', from, to,
            title: `${[errors ? count(errors, ['error', 'errors']) : '', warnings ? count(warnings, ['warning', 'warnings']) : ''].filter(Boolean).join(' and ')} in the System log`,
            line: `${count(total, ['record', 'records'])} of every level returned this day` });
        } else {
          items.push({ kind: 'count', key: `${track.id}:${from}`, at: peak.from, tone: 'hardware', from: peak.from, to: peak.to,
            title: `${count(total, ['hardware error report', 'hardware error reports'])}${track.id === 'kernel' ? ' (Kernel-WHEA)' : ''}`,
            line: bins.length > 1 ? `Most in the bucket from ${fmt.minute(peak.from)}: ${peak.count}` : `All in the bucket from ${fmt.minute(peak.from)}` });
        }
      }
    }
    items.sort((a, b) => (b.kind === 'mark' ? b.mark.at : b.at) - (a.kind === 'mark' ? a.mark.at : a.at));
    return { from, to, items, unread, partial };
  });
}

interface Folded extends Day { days: number; last: number }

/** Adjacent days with nothing returned and the same coverage become one row. */
function fold(days: Day[]): Folded[] {
  const out: Folded[] = [];
  for (const day of days) {
    const prev = out[out.length - 1];
    const quiet = !day.items.length && !day.partial.length;
    if (prev && quiet && !prev.items.length && !prev.partial.length && prev.unread.join('|') === day.unread.join('|')) {
      prev.days += 1;
      prev.from = day.from;
      continue;
    }
    out.push({ ...day, days: 1, last: startOfDay(day.from) });
  }
  // `from` walks back as days fold; `last` keeps the newest day of the run.
  return out;
}
