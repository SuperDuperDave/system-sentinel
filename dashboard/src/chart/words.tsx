import { Range, fmt, startOfDay } from '../time';
import { Track, coverageOf, unreadGaps } from '../timeline';
import { useApp } from '../store';

/**
 * The sentences. A track's reach, the home's summary and a day's coverage are all written from the
 * same projection the chart draws, so the words and the drawing cannot disagree.
 */

const OUTCOME_WORD: Record<string, string> = {
  failed: 'the reading failed', denied: 'access was denied', timeout: 'the reading timed out',
  unavailable: 'this source is unavailable here', lost: 'the request did not complete',
};

export function count(n: number, noun: [string, string]): string {
  return `${n.toLocaleString()} ${n === 1 ? noun[0] : noun[1]}`;
}

/** Where a track looked, in a few words (the chart's row label) or a sentence (the ledger). */
export function reachWords(track: Track, range: Range, short = false): string {
  const reach = track.reach;
  if (reach.state === 'waiting') return 'Reading…';
  if (reach.state === 'failed') return short ? `Not read: ${OUTCOME_WORD[reach.outcome] ?? reach.outcome}` : `Not read: ${OUTCOME_WORD[reach.outcome] ?? reach.outcome}. ${reach.detail}`;
  if (!reach.spans.length) return track.kind === 'samples' ? (short ? 'Nothing measured' : 'Nothing measured in this range') : 'Not read in this range';
  const from = Math.min(...reach.spans.map((s) => s[0]));
  const to = Math.max(...reach.spans.map((s) => s[1]));
  const holes = unreadGaps(track, range.from, range.to, range);
  const whole = holes.length === 0;
  const inner = holes.filter(([s, e]) => s > from && e < to).length;
  const total = track.total ?? 0;
  const got = track.kind === 'samples' ? count(total, track.noun) : total ? count(total, track.noun) : `no ${track.noun[1]}`;
  if (short) return whole ? `${got}, whole range read` : from > range.from ? `${got}, read from ${sameDay(from, to) ? fmt.minute(from) : fmt.date(from)}` : `${got}, part read`;
  const where = whole ? 'Read across the whole range'
    : `Read from ${fmt.when(from)} to ${fmt.when(to)}${inner ? `, with ${count(inner, ['unread stretch', 'unread stretches'])} inside` : ' only'}`;
  return `${where}: ${got}.${reach.note ? ` ${reach.note}` : ''}`;
}

function sameDay(a: number, b: number): boolean {
  return startOfDay(a) === startOfDay(b);
}

export function ReachCaption({ track, className, short = false }: { track: Track; className?: string; short?: boolean }) {
  const range = useApp((s) => s.range);
  return <span className={className}>{reachWords(track, range, short)}</span>;
}

/**
 * The home's answer, in sentences: what happened in the range, when, and what was not read.
 * Numbers are what the readings returned. Nothing here ranks, scores or explains.
 */
export interface Line { tone: 'stop' | 'hardware' | 'ink' | 'unread'; text: string }

export function summary(tracks: Track[], range: Range): Line[] {
  const by = Object.fromEntries(tracks.map((t) => [t.id, t])) as Partial<Record<Track['id'], Track>>;
  const out: Line[] = [];
  const push = (tone: Line['tone'], text: string) => out.push({ tone, text });

  const stops = by.stops;
  if (stops?.reach.state === 'read') {
    const all = stopsOnRecord(stops);
    const readFrom = stops.reach.spans.length ? Math.min(...stops.reach.spans.map((s) => s[0])) : range.to;
    const scope = coverageOf(stops, range.from, range.to, range) === 'read' ? '' : `, in the part read (from ${fmt.date(readFrom)})`;
    if (stops.marks.length) {
      const newest = stops.marks.reduce((a, b) => (b.at > a.at ? b : a));
      push('stop', `${capital(count(stops.marks.length, ['unplanned stop', 'unplanned stops']))}${scope}, the latest on ${fmt.when(newest.at)}.`);
    } else if (all.length) {
      const newest = Math.max(...all);
      push('stop', `No unplanned stop in this range${scope}. The latest on record is ${fmt.day(newest)}, before it.`);
    } else {
      push('stop', 'No unplanned stop was returned.');
    }
  } else if (stops?.reach.state === 'failed') push('unread', 'Unplanned stops could not be read.');

  const hardware = by.hardware;
  if (hardware?.lead && hardware.lead.state !== 'quiet') push('hardware', `A live lead, inferred from the last minutes of the System log: ${hardware.lead.reason}.`);
  if (hardware?.reach.state === 'read') {
    const days = busyDays(hardware);
    const kernel = by.kernel;
    const kernelWords = kernel?.reach.state === 'read' ? ` The Kernel-WHEA channel, read separately, returned ${count(kernel.total ?? 0, ['report', 'reports'])}.` : '';
    push('hardware', hardware.total
      ? `${capital(count(hardware.total, ['hardware error report', 'hardware error reports']))} in the System log, ${days.length === 1 ? `all on ${fmt.day(days[0][0])}` : `most on ${fmt.day(days[0][0])}`}.${kernelWords}`
      : `No hardware error report in the System log.${kernelWords || ' That does not clear the separate Kernel-WHEA channel.'}`);
  }
  const faults = by.faults;
  if (faults?.reach.state === 'read' && faults.total) {
    const days = busyDays(faults);
    const capped = faults.reach.note?.startsWith('Only the newest') ? ' (the newest 500 read)' : '';
    push('ink', `${capital(count(faults.total, ['program fault', 'program faults']))} filed in what was read${capped}, ${days.length === 1 ? `all on ${fmt.day(days[0][0])}` : `most on ${fmt.day(days[0][0])}`}.`);
  }
  const log = by.log;
  if (log?.reach.state === 'read') {
    const critical = log.marks.length;
    const first = log.reach.spans[0]?.[0];
    const partial = first !== undefined && coverageOf(log, range.from, range.to, range) !== 'read';
    push('ink', `${capital(count(log.total ?? 0, ['System log record', 'System log records']))} returned${critical ? `, ${critical} of them critical` : ''}${partial ? `. The log reaches back only to ${fmt.when(first)}` : ''}.`);
  }

  const unread = tracks.filter((t) => t.reach.state === 'failed').map((t) => lower(t.label));
  const unmeasured = tracks.filter((t) => t.kind === 'samples' && t.reach.state === 'read' && !t.reach.spans.length).map((t) => lower(t.label));
  const parts = [unread.length ? `${list(unread)} could not be read` : '', unmeasured.length ? `${list(unmeasured)} has no stored samples` : ''].filter(Boolean);
  if (parts.length) push('unread', `${capital(parts.join('; '))}.`);
  return out;
}

function stopsOnRecord(track: Track): number[] {
  const stops = (track.taken.reading?.sections.find((s) => s.name === 'stops')?.data ?? []) as { started_at: string | null; stopped_at: string | null }[];
  return stops.map((s) => Date.parse(s.stopped_at ?? s.started_at ?? '')).filter(Number.isFinite);
}

/** Days with returned items, busiest first. */
export function busyDays(track: Track): [number, number][] {
  const days = new Map<number, number>();
  for (const bin of track.bins) if (bin.count) days.set(startOfDay(bin.from), (days.get(startOfDay(bin.from)) ?? 0) + bin.count);
  if (!track.bins.length) for (const mark of track.marks) days.set(startOfDay(mark.at), (days.get(startOfDay(mark.at)) ?? 0) + 1);
  return [...days.entries()].sort((a, b) => b[1] - a[1]);
}

export function list(items: string[]): string {
  if (items.length < 2) return items.join('');
  return `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`;
}

export function capital(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** A label inside a sentence: lower-case its first word unless that word is a name (Kernel-WHEA, System). */
export function lower(label: string): string {
  const first = label.split(' ')[0];
  return /[A-Z].*[A-Z]|^System$/.test(first) ? label : label.charAt(0).toLowerCase() + label.slice(1);
}
