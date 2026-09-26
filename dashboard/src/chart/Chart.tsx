import { KeyboardEvent, PointerEvent, useMemo, useRef, useState } from 'react';
import { Range, bucketSeconds, fmt, span, ticks } from '../time';
import { Mark, Track, unreadGaps } from '../timeline';
import { Selection, useApp } from '../store';
import { ReachCaption } from './words';
import styles from './Chart.module.css';

/**
 * The chart recorder: one time axis, one row per source.
 *
 * Every row shares the axis, and every row draws where its reading looked. Inside that reach an
 * empty stretch is plain paper: read, nothing returned. Outside it the paper is hatched: not read.
 * A reading that failed hatches its whole row and says so. Picking a moment, a mark or a dragged
 * stretch fills the inspector beside the chart; the chart itself never moves.
 *
 * Pointer: click a moment or a mark, drag a stretch. Keyboard: the plot is one slider. Arrows move
 * the cursor a bucket (Shift for ten), Enter picks it, Page Up and Page Down step through the
 * marks, Escape clears. Every mark is also a row in the day list, which is the canonical list.
 */
export function Chart({ range, tracks, label }: { range: Range; tracks: Track[]; label: string }) {
  const selection = useApp((s) => s.selection);
  const select = useApp((s) => s.select);
  const overlay = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const [cursor, setCursor] = useState<number | null>(null);
  const [drag, setDrag] = useState<{ start: number; end: number; moved: boolean } | null>(null);
  const width = range.to - range.from;
  const x = (at: number) => `${((at - range.from) / width) * 100}%`;
  const grid = useMemo(() => ticks(range), [range]);
  const step = bucketSeconds(range) * 1000;
  const marks = useMemo(() => tracks.flatMap((t) => t.marks).sort((a, b) => a.at - b.at), [tracks]);
  // Rows that count the same thing share one scale (both hardware rows count reports); every scale
  // has a floor of ten, so one report is a short bar and not a full-height one.
  const scales = useMemo(() => {
    const out: Record<string, number> = {};
    for (const track of tracks) out[track.tone] = Math.max(out[track.tone] ?? 10, ...track.bins.map((b) => b.count ?? 0));
    return out;
  }, [tracks]);

  const timeAt = (clientX: number) => {
    const box = overlay.current!.getBoundingClientRect();
    const share = Math.min(1, Math.max(0, (clientX - box.left) / box.width));
    return range.from + share * width;
  };

  /** The mark under the pointer in the row it is over, within a few pixels. */
  const markAt = (clientX: number, clientY: number): Mark | null => {
    const box = overlay.current!.getBoundingClientRect();
    const row = document.elementsFromPoint(clientX, clientY).map((el) => (el as HTMLElement).dataset?.track).find(Boolean);
    const track = tracks.find((t) => t.id === row);
    if (!track) return null;
    const tolerance = (6 / box.width) * width;
    const at = timeAt(clientX);
    let best: Mark | null = null;
    for (const mark of track.marks) {
      const lo = Math.min(...mark.times.map((t) => t.at)) - tolerance;
      const hi = Math.max(...mark.times.map((t) => t.at)) + tolerance;
      if (at >= lo && at <= hi && (!best || Math.abs(mark.at - at) < Math.abs(best.at - at))) best = mark;
    }
    return best;
  };

  const pickMark = (mark: Mark) => select({ kind: 'mark', id: mark.id, at: new Date(mark.at).toISOString() });

  function down(event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    overlay.current?.setPointerCapture(event.pointerId);
    const at = timeAt(event.clientX);
    setDrag({ start: at, end: at, moved: false });
  }
  function move(event: PointerEvent<HTMLDivElement>) {
    const at = timeAt(event.clientX);
    setHover(at);
    if (drag) {
      const box = overlay.current!.getBoundingClientRect();
      const moved = drag.moved || Math.abs(((at - drag.start) / width) * box.width) > 5;
      setDrag({ ...drag, end: at, moved });
    }
  }
  function up(event: PointerEvent<HTMLDivElement>) {
    if (!drag) return;
    if (drag.moved) {
      const from = Math.min(drag.start, drag.end);
      const to = Math.max(drag.start, drag.end);
      select({ kind: 'stretch', from, to });
    } else {
      const mark = markAt(event.clientX, event.clientY);
      if (mark) pickMark(mark);
      else select({ kind: 'moment', at: new Date(Math.round(timeAt(event.clientX) / 1000) * 1000).toISOString() });
    }
    setDrag(null);
  }

  function key(event: KeyboardEvent<HTMLDivElement>) {
    const here = cursor ?? selectedAt(selection) ?? range.to - step / 2;
    const stride = event.shiftKey ? step * 10 : step;
    let handled = true;
    if (event.key === 'ArrowLeft') setCursor(Math.max(range.from, here - stride));
    else if (event.key === 'ArrowRight') setCursor(Math.min(range.to, here + stride));
    else if (event.key === 'Home') setCursor(range.from);
    else if (event.key === 'End') setCursor(range.to);
    else if (event.key === 'Enter' || event.key === ' ') select({ kind: 'moment', at: new Date(Math.round(here / 1000) * 1000).toISOString() });
    else if (event.key === 'Escape') { select(null); setCursor(null); }
    else if (event.key === 'PageUp' || event.key === 'PageDown') {
      const next = event.key === 'PageDown' ? marks.find((m) => m.at > here + 1) : [...marks].reverse().find((m) => m.at < here - 1);
      if (next) { setCursor(next.at); pickMark(next); }
    } else handled = false;
    if (handled) event.preventDefault();
  }

  const chosen = selectedAt(selection);
  const stretch = drag?.moved ? { from: Math.min(drag.start, drag.end), to: Math.max(drag.start, drag.end) }
    : selection?.kind === 'stretch' ? selection : null;
  const markId = selection?.kind === 'mark' ? selection.id : null;
  const shownCursor = cursor ?? hover;
  // A time label near the right edge sits to the left of its line, so it never leaves the chart.
  const flip = (at: number) => (at - range.from) / width > 0.78;

  return (
    <figure className={styles.chart} aria-label={label}>
      <div className={styles.grid} style={{ gridTemplateRows: `32px ${tracks.map((t) => (t.id === 'log' && tracks.length < 4 ? '96px' : t.id === 'log' ? '60px' : '48px')).join(' ')}` }}>
        <div className={styles.axisLabel} aria-hidden="true" />
        <div className={styles.axis} aria-hidden="true">
          {grid.filter((t) => t.label).map((t) => <span key={t.at} className={`${styles.tick} readout ${t.major ? styles.tickMajor : ''}`} style={{ left: x(t.at) }}>{t.label}</span>)}
        </div>

        <div className={styles.rules} aria-hidden="true">
          {grid.map((t) => <span key={t.at} className={`${styles.rule} ${t.major ? styles.ruleMajor : ''}`} style={{ left: x(t.at) }} />)}
        </div>

        {tracks.map((track, index) => (
          <Row key={track.id} track={track} range={range} row={index + 2} x={x} markId={markId} scale={scales[track.tone]} />
        ))}

        <div
          ref={overlay}
          className={styles.overlay}
          role="slider"
          tabIndex={0}
          aria-label={`${label}: pick a moment`}
          aria-valuemin={range.from}
          aria-valuemax={range.to}
          aria-valuenow={Math.round(cursor ?? chosen ?? range.to)}
          aria-valuetext={cursor !== null ? fmt.whenExact(cursor) : chosen !== null ? `Picked ${fmt.whenExact(chosen)}` : 'No moment picked'}
          aria-keyshortcuts="ArrowLeft ArrowRight Enter PageUp PageDown Escape"
          onPointerDown={down}
          onPointerMove={move}
          onPointerUp={up}
          onPointerLeave={() => setHover(null)}
          onPointerCancel={() => setDrag(null)}
          onKeyDown={key}
          onBlur={() => setCursor(null)}
        >
          {stretch ? <span className={styles.stretch} style={{ left: x(stretch.from), width: `${((stretch.to - stretch.from) / width) * 100}%` }} /> : null}
          {chosen !== null && chosen >= range.from && chosen <= range.to ? (
            <span className={styles.chosen} style={{ left: x(chosen) }}><span className={`${styles.chosenLabel} ${flip(chosen) ? styles.flip : ''} readout`}>{fmt.when(chosen)}</span></span>
          ) : null}
          {shownCursor !== null && !drag?.moved ? (
            <span className={`${styles.cursor} ${cursor !== null ? styles.cursorKeyboard : ''}`} style={{ left: x(shownCursor) }}><span className={`${styles.cursorLabel} ${flip(shownCursor) ? styles.flip : ''} readout`}>{fmt.when(shownCursor)}</span></span>
          ) : null}
          {drag?.moved ? <span className={`${styles.dragLabel} readout`} style={{ left: x(Math.min(drag.start, drag.end)) }}>{span(Math.abs(drag.end - drag.start))}</span> : null}
        </div>
      </div>
      <figcaption className={styles.caption}>
        <Legend tracks={tracks} />
        <span className={styles.keys}>Click a moment or drag a stretch. Keys: arrows move, Enter picks, Page Up and Down step through marks, Esc clears.</span>
      </figcaption>
    </figure>
  );
}

function selectedAt(selection: Selection | null): number | null {
  if (!selection || selection.kind === 'stretch') return null;
  const t = Date.parse(selection.at);
  return Number.isFinite(t) ? t : null;
}

function Row({ track, range, row, x, markId, scale }: { track: Track; range: Range; row: number; x: (at: number) => string; markId: string | null; scale: number }) {
  const setView = useApp((s) => s.setView);
  const width = range.to - range.from;
  const gaps = unreadGaps(track, range.from, range.to, range);
  const max = track.id === 'log' ? Math.max(10, ...track.bins.map((b) => b.count ?? 0)) : scale;
  return (
    <>
      {track.view ? (
        <button className={`${styles.label} ${styles.labelLink}`} style={{ gridRow: row }} onClick={() => setView(track.view!)} aria-label={`${track.label}: open its view`}>
          <span className={styles.labelName}>{track.label}</span>
          <ReachCaption track={track} className={styles.labelCaption} short />
        </button>
      ) : (
        <div className={styles.label} style={{ gridRow: row }}>
          <span className={styles.labelName}>{track.label}</span>
          <ReachCaption track={track} className={styles.labelCaption} short />
        </div>
      )}
      <div className={`${styles.plot} ${styles[`tone_${track.tone}`]}`} style={{ gridRow: row }} data-track={track.id} aria-hidden="true">
        {track.reach.state === 'failed' ? (
          <span className={styles.failed}><span className={styles.failedText}>Could not be read</span></span>
        ) : track.reach.state === 'waiting' ? (
          <span className={styles.waiting}><span className={styles.failedText}>Reading…</span></span>
        ) : (
          <>
            <span className={styles.baseline} />
            {gaps.map(([from, to]) => (
              <span key={from} className={styles.notRead} style={{ left: x(from), width: `${((to - from) / width) * 100}%` }}>
                {(to - from) / width > 0.14 ? <span className={styles.notReadWords}>{track.kind === 'samples' ? 'not measured' : 'not read'}</span> : null}
              </span>
            ))}
            {track.bins.map((bin) => bin.count ? (
              <span key={bin.from} className={styles.bar} style={{ left: x(Math.max(bin.from, range.from)), width: `max(2px, calc(${((Math.min(bin.to, range.to) - Math.max(bin.from, range.from)) / width) * 100}% - 1px))`, height: `max(2px, ${(bin.count / max) * 100}%)` }}>
                {bin.levels ? <Levels levels={bin.levels} /> : null}
                {bin.previous ? <span className={styles.previous} style={{ height: `${(bin.previous / bin.count) * 100}%` }} title="Filed about an earlier Windows session" /> : null}
              </span>
            ) : null)}
            {track.samples?.length ? <Samples samples={track.samples} range={range} /> : null}
            {track.marks.map((mark) => <MarkGlyph key={mark.id} mark={mark} x={x} width={width} range={range} chosen={mark.id === markId} />)}
            {track.bins.length ? <span className={`${styles.scale} readout`}>{max} per {binWords(track)}</span> : null}
          </>
        )}
      </div>
    </>
  );
}

function binWords(track: Track): string {
  const ms = track.bins[0] ? track.bins[0].to - track.bins[0].from : 0;
  return ms >= 86_400_000 ? 'day' : span(ms);
}

function Levels({ levels }: { levels: [number, number, number, number] }) {
  const total = levels.reduce((a, b) => a + b, 0) || 1;
  return <>{levels.map((n, i) => n ? <span key={i} className={styles[`level${i}`]} style={{ height: `${(n / total) * 100}%` }} /> : null)}</>;
}

function Samples({ samples, range }: { samples: { at: number; value: number | null }[]; range: Range }) {
  const width = range.to - range.from;
  const points = samples.filter((s) => s.value !== null).map((s) => `${((s.at - range.from) / width) * 1000},${100 - (s.value ?? 0)}`).join(' ');
  return <svg className={styles.samples} viewBox="0 0 1000 100" preserveAspectRatio="none"><polyline points={points} /></svg>;
}

function MarkGlyph({ mark, x, width, range, chosen }: { mark: Mark; x: (at: number) => string; width: number; range: Range; chosen: boolean }) {
  const times = mark.times.filter((t) => t.at >= range.from && t.at <= range.to);
  if (mark.track === 'stops') {
    const lo = Math.min(...mark.times.map((t) => t.at));
    const hi = Math.max(...mark.times.map((t) => t.at));
    const estimate = mark.times.find((t) => t.label === "Windows' stop estimate")?.at;
    return (
      <span className={`${styles.stop} ${chosen ? styles.markChosen : ''}`}>
        {/* Before Windows' estimate the stop's time is uncertain; from the estimate to the next start the machine was down. */}
        {estimate !== undefined && estimate > lo ? <span className={styles.stopUncertain} style={{ left: x(Math.max(lo, range.from)), width: `${((Math.min(estimate, range.to) - Math.max(lo, range.from)) / width) * 100}%` }} /> : null}
        <span className={styles.stopSpan} style={{ left: x(Math.max(estimate ?? lo, range.from)), width: `max(3px, ${((Math.min(hi, range.to) - Math.max(estimate ?? lo, range.from)) / width) * 100}%)` }} />
        {times.map((t) => <span key={t.label} className={`${styles.stopTime} ${styles[`stopTime_${t.label === 'Next start' || t.label === 'Restart announced' ? 'start' : t.label === 'Last System record' ? 'last' : 'estimate'}`]}`} style={{ left: x(t.at) }} />)}
      </span>
    );
  }
  return <span className={`${styles.tickMark} ${chosen ? styles.markChosen : ''}`} style={{ left: x(mark.at) }} />;
}

function Legend({ tracks }: { tracks: Track[] }) {
  const hasStops = tracks.some((t) => t.id === 'stops');
  const hasLog = tracks.some((t) => t.id === 'log');
  return (
    <span className={styles.legend}>
      <span className={styles.legendItem}><span className={styles.swatchRead} />Read, nothing returned</span>
      <span className={styles.legendItem}><span className={styles.swatchNotRead} />Not read</span>
      {hasStops ? <span className={styles.legendItem}><span className={styles.swatchStop} />Stop: dotted from the last record to Windows' estimate, solid while down until the next start</span> : null}
      {tracks.some((t) => t.tone === 'hardware') ? <span className={styles.legendItem}><span className={styles.swatchPrevious} />Hollow: a report about an earlier Windows session</span> : null}
      {hasLog ? <span className={styles.legendItem}><span className={styles.swatchLevels} />Log: critical and error, warning, information</span> : null}
      <span className={styles.legendItem}>The number at a row's right is its scale's top. Both hardware rows share one scale; other rows do not compare.</span>
    </span>
  );
}
