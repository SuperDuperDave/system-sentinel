/**
 * The one time range every view on the time axis shares, and the words and numbers for it.
 *
 * A range is either live (the last seven days, anchored to when it was chosen) or fixed (a
 * stretch someone dragged or zoomed to). The address carries it: `range=7d`, or
 * `range=<from>/<to>` in ISO 8601 interval form, so a copied link opens the same stretch.
 */

export type Preset = '30d' | '7d' | '24h' | '6h' | '1h';

export interface Range {
  /** Set when the range is "the last N"; null for a fixed stretch. */
  preset: Preset | null;
  from: number;
  to: number;
}

const HOUR = 3_600_000;
const DAY = 24 * HOUR;

export const PRESETS: { id: Preset; label: string; long: string; ms: number }[] = [
  { id: '30d', label: '30 d', long: 'the last 30 days', ms: 30 * DAY },
  { id: '7d', label: '7 d', long: 'the last 7 days', ms: 7 * DAY },
  { id: '24h', label: '24 h', long: 'the last 24 hours', ms: DAY },
  { id: '6h', label: '6 h', long: 'the last 6 hours', ms: 6 * HOUR },
  { id: '1h', label: '1 h', long: 'the last hour', ms: HOUR },
];

export const DEFAULT_PRESET: Preset = '7d';
/** The narrowest stretch the chart zooms to. */
export const MIN_SPAN = 60_000;
export const MAX_SPAN = 30 * DAY;

export function presetRange(preset: Preset, now = Date.now()): Range {
  const ms = PRESETS.find((p) => p.id === preset)!.ms;
  // Round the live end up to the next minute so the same choice made twice asks the same question.
  const to = Math.ceil(now / 60_000) * 60_000;
  return { preset, from: to - ms, to };
}

export function fixedRange(from: number, to: number): Range {
  const span = Math.min(MAX_SPAN, Math.max(MIN_SPAN, to - from));
  const middle = (from + to) / 2;
  const start = Math.round((middle - span / 2) / 1000) * 1000;
  return { preset: null, from: start, to: start + Math.round(span / 1000) * 1000 };
}

export function rangeToAddress(range: Range): string {
  return range.preset ?? `${iso(range.from)}/${iso(range.to)}`;
}

export function rangeFromAddress(value: string | null, now = Date.now()): Range {
  if (value && PRESETS.some((p) => p.id === value)) return presetRange(value as Preset, now);
  const parts = value?.split('/') ?? [];
  if (parts.length === 2) {
    const from = Date.parse(parts[0]);
    const to = Date.parse(parts[1]);
    if (Number.isFinite(from) && Number.isFinite(to) && to > from) return fixedRange(from, to);
  }
  return presetRange(DEFAULT_PRESET, now);
}

/** Bucket widths the chart counts in: the smallest that keeps a range to about 180 columns. */
const LADDER = [30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400];

export function bucketSeconds(range: Range): number {
  const seconds = (range.to - range.from) / 1000;
  return LADDER.find((width) => seconds / width <= 180) ?? 86400;
}

/** Whole hours a reading must ask for to reach the start of the range. */
export function rangeHours(range: Range): number {
  return Math.max(1, Math.ceil((range.to - range.from) / HOUR));
}

/** How far either side of a moment counts as near it: one bucket, never under a minute. */
export function nearWindow(range: Range): number {
  return Math.max(60_000, bucketSeconds(range) * 1000);
}

export function iso(ms: number): string {
  return new Date(ms).toISOString().replace(/\.000Z$/, 'Z');
}

// ------------------------------------------------------------------ words

const dayShort = new Intl.DateTimeFormat(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
const dayLong = new Intl.DateTimeFormat(undefined, { weekday: 'long', day: 'numeric', month: 'long' });
const dateOnly = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short' });
const tickDay = new Intl.DateTimeFormat(undefined, { weekday: 'short', day: 'numeric' });
const clockMinute = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', hour12: false });
const clockSecond = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });

export const fmt = {
  day: (ms: number) => dayShort.format(ms),
  dayLong: (ms: number) => dayLong.format(ms),
  date: (ms: number) => dateOnly.format(ms),
  minute: (ms: number) => clockMinute.format(ms),
  second: (ms: number) => clockSecond.format(ms),
  /** "Fri 26 Sep, 14:32" */
  when: (ms: number) => `${dayShort.format(ms)}, ${clockMinute.format(ms)}`,
  whenExact: (ms: number) => `${dayShort.format(ms)}, ${clockSecond.format(ms)}`,
};

/** "4 min", "2 h 10 min", "3 days" */
export function span(ms: number): string {
  const abs = Math.abs(ms);
  if (abs < 60_000) return `${Math.max(1, Math.round(abs / 1000))} s`;
  if (abs < HOUR) return `${Math.round(abs / 60_000)} min`;
  if (abs < DAY) {
    const hours = Math.floor(abs / HOUR);
    const minutes = Math.round((abs - hours * HOUR) / 60_000);
    return minutes && hours < 6 ? `${hours} h ${minutes} min` : `${Math.round(abs / HOUR)} h`;
  }
  const days = Math.round(abs / DAY);
  return `${days} ${days === 1 ? 'day' : 'days'}`;
}

export function rangeTitle(range: Range): string {
  if (range.preset) return PRESETS.find((p) => p.id === range.preset)!.long;
  return `${fmt.when(range.from)} to ${fmt.when(range.to)}`;
}

/** Local midnights inside the range, for the ruled grid and the day list. */
export function dayStarts(from: number, to: number): number[] {
  const first = new Date(from);
  first.setHours(0, 0, 0, 0);
  const out: number[] = [];
  for (let t = first.getTime(); t < to; ) {
    out.push(t);
    const next = new Date(t);
    next.setDate(next.getDate() + 1);
    t = next.getTime();
  }
  return out;
}

export function startOfDay(ms: number): number {
  const d = new Date(ms);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

/** Grid ticks for the axis: days for long ranges, hours or minutes for short ones. */
export function ticks(range: Range): { at: number; label: string; major: boolean }[] {
  const width = range.to - range.from;
  if (width > 2 * DAY) {
    // A week names weekdays; a month labels every third day by number and names the month on the
    // first label and wherever the month changes between labels.
    const days = dayStarts(range.from, range.to).filter((t) => t >= range.from);
    const stride = width > 14 * DAY ? 3 : 1;
    let month = -1;
    return days.map((t, i) => {
      const date = new Date(t);
      if (stride === 1) return { at: t, label: tickDay.format(t), major: true };
      if (i % stride) return { at: t, label: '', major: date.getDay() === 1 };
      const label = date.getMonth() !== month ? dateOnly.format(t) : String(date.getDate());
      month = date.getMonth();
      return { at: t, label, major: true };
    });
  }
  const step = width > 12 * HOUR ? 3 * HOUR : width > 3 * HOUR ? HOUR : width > HOUR ? 15 * 60_000 : width > 20 * 60_000 ? 5 * 60_000 : 60_000;
  const out: { at: number; label: string; major: boolean }[] = [];
  const offset = new Date(range.from).getTimezoneOffset() * 60_000;
  for (let t = Math.ceil((range.from - offset) / step) * step + offset; t < range.to; t += step) {
    const midnight = startOfDay(t) === t;
    out.push({ at: t, label: midnight ? tickDay.format(t) : fmt.minute(t), major: midnight });
  }
  return out;
}
