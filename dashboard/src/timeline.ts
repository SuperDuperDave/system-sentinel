/**
 * The timeline: every source the machine keeps, projected onto one time range.
 *
 * Nothing here asks Windows anything new. Each track is an existing reading taken over the shared
 * range, and each carries its own reach: where that reading actually looked. A track that was read
 * and found nothing is a different fact from a track that was not read there, and a failed reading
 * is a third. The chart draws all three, so aligned emptiness across tracks is never shown as
 * agreement. Counts are what the readings returned, never a lifetime total.
 *
 * The API asked for in docs/design/2026-09-26-directions/B/README.md (`timeline`) would replace the
 * client-side counting here with server-side counts that are not capped by a row limit.
 */
import { EventRecord, Reading, observed } from './api';
import { part } from './Sections';
import { Taken, useReading } from './useReading';
import { Range, bucketSeconds, iso, rangeHours } from './time';
import type { ViewId } from './store';
import type { Fault, Stop } from './views/Crashes';
import { appOf, exceptionOf, moduleOf } from './views/Crashes';

export type TrackId = 'stops' | 'hardware' | 'kernel' | 'faults' | 'log' | 'changes' | 'performance' | 'reliability';

/** Where a reading looked. `spans` are the stretches it covered; the rest of the range was not read. */
export type Reach =
  | { state: 'waiting' }
  | { state: 'failed'; outcome: string; detail: string }
  | { state: 'read'; spans: [number, number][]; note: string | null };

export interface Bin {
  from: number;
  to: number;
  /** null: this bucket's coverage is unknown. */
  count: number | null;
  /** Critical, error, warning, information: only for the System log. */
  levels?: [number, number, number, number];
}

export type Tone = 'stop' | 'hardware' | 'ink';

export interface Mark {
  id: string;
  track: TrackId;
  /** Where the mark sits: the earliest of its evidence times. */
  at: number;
  /** Every evidence time the mark carries, in order; a stop has up to three and none is dropped. */
  times: { label: string; at: number }[];
  title: string;
  line: string;
  tone: Tone;
  stop?: Stop;
  fault?: Fault;
  record?: EventRecord;
}

export interface Track {
  id: TrackId;
  label: string;
  /** Where the source lives, in Windows' words. */
  source: string;
  /** The view that holds this source's full evidence. */
  view: ViewId | null;
  /** Events: an empty stretch inside the reach means nothing was recorded. Samples: it means nothing was measured. */
  kind: 'events' | 'samples';
  tone: Tone;
  taken: Taken<unknown>;
  reach: Reach;
  bins: Bin[];
  marks: Mark[];
  /** Returned inside the range; null when the reading did not answer. */
  total: number | null;
  noun: [string, string];
  samples?: { at: number; value: number | null }[];
}

type Taking = Taken<unknown>;
const HOUR = 3_600_000;
const DAY = 24 * HOUR;
const LEVEL_INDEX: Record<number, 0 | 1 | 2 | 3> = { 1: 0, 2: 1, 3: 2, 4: 3, 0: 3, 5: 3 };

// ------------------------------------------------------------------ taking

/** Take every source over the range. Views name the tracks they draw; the others are not asked. */
export function useTracks(range: Range, wanted: TrackId[]): Track[] {
  const live = range.preset !== null;
  const hours = rangeHours(range);
  const bucket = bucketSeconds(range);
  const before: Record<string, string> = live ? {} : { before: iso(range.to) };
  const on = (id: TrackId) => wanted.includes(id);
  const hold = { hold: 'same-params' as const };

  const stops = useReading('crash', { count: 20 }, on('stops'), hold);
  const hardware = useReading('storms', { hours, bucket_seconds: bucket, ...before }, on('hardware'), hold);
  const kernel = useReading('whea_reports', { hours, bucket_seconds: bucket, ...before }, on('kernel'), hold);
  const faults = useReading('faults', { count: 500, since: iso(range.from), ...before }, on('faults'), hold);
  const log = useReading('events', { log: 'System', levels: [], count: 2000, since: iso(range.from), ...before }, on('log'), hold);
  const changes = useReading('changes', { hours: Math.min(2160, hours), count: 100, ...before }, on('changes'), hold);
  const performance = useReading('performance_history', { hours: Math.min(48, hours), ...(live ? {} : { end: iso(range.to).replace('Z', '+00:00') }) }, on('performance'), hold);
  const reliability = useReading('reliability', { days: Math.min(366, Math.ceil((range.to - range.from) / DAY)) }, on('reliability'), hold);

  const all: Record<TrackId, () => Track> = {
    stops: () => stopTrack(stops, range),
    hardware: () => bucketTrack(hardware, range, 'hardware', 'system'),
    kernel: () => bucketTrack(kernel, range, 'kernel', 'kernel_whea'),
    faults: () => faultTrack(faults, range),
    log: () => logTrack(log, range),
    changes: () => changeTrack(changes, range),
    performance: () => performanceTrack(performance, range),
    reliability: () => reliabilityTrack(reliability, range),
  };
  return wanted.map((id) => all[id]());
}

// ------------------------------------------------------------------ reach

function when(value: unknown): number | null {
  if (typeof value !== 'string' || !value) return null;
  const t = Date.parse(value);
  return Number.isFinite(t) ? t : null;
}

function notRead(taken: Taking): Reach | null {
  if (taken.state === 'lost' && !taken.reading) return { state: 'failed', outcome: 'lost', detail: taken.problem ?? 'The request did not complete.' };
  if (!taken.reading) return { state: 'waiting' };
  if (!observed(taken.reading)) {
    return { state: 'failed', outcome: taken.reading.outcome, detail: taken.reading.error?.detail ?? `The reading answered ${taken.reading.outcome}.` };
  }
  return null;
}

/** A reading's reach clipped to the range, from its coverage section when it has one. */
function spanReach(from: number | null, to: number | null, range: Range, note: string | null): Reach {
  const start = Math.max(range.from, from ?? range.from);
  const end = Math.min(range.to, to ?? range.to);
  return { state: 'read', spans: end > start ? [[start, end]] : [], note };
}

interface Coverage { covered_from?: string | null; covered_until?: string | null; retained_from?: string | null; complete?: boolean | null }
interface Collection { truncated?: boolean | null; limit?: number; returned?: number }

function capNote(collection: Collection | null, noun: string): string | null {
  return collection?.truncated ? `Only the newest ${collection.limit?.toLocaleString() ?? ''} ${noun} were returned; older ones in this range were not read.` : null;
}

function bins(range: Range): Bin[] {
  const width = bucketSeconds(range) * 1000;
  const first = Math.floor(range.from / width) * width;
  const out: Bin[] = [];
  for (let t = first; t < range.to; t += width) out.push({ from: t, to: t + width, count: 0 });
  return out;
}

function place(list: Bin[], at: number): Bin | undefined {
  if (!list.length) return undefined;
  const width = list[0].to - list[0].from;
  return list[Math.floor((at - list[0].from) / width)];
}

/** Buckets outside the reach are unknown, not zero. */
function maskBins(list: Bin[], reach: Reach): Bin[] {
  if (reach.state !== 'read') return list.map((b) => ({ ...b, count: null }));
  return list.map((b) => reach.spans.some(([s, e]) => b.from < e && b.to > s) ? b : { ...b, count: null, levels: undefined });
}

function inRange(at: number | null, range: Range): at is number {
  return at !== null && at >= range.from && at < range.to;
}

function sum(list: Bin[]): number {
  return list.reduce((n, b) => n + (b.count ?? 0), 0);
}

// ------------------------------------------------------------------ tracks

function base(id: TrackId, taken: Taking): Pick<Track, 'id' | 'taken'> {
  return { id, taken };
}

function stopTrack(taken: Taking, range: Range): Track {
  const meta = { ...base('stops', taken), label: 'Unplanned stops', source: 'System log and Windows Error Reporting', view: 'crashes' as ViewId, kind: 'events' as const, tone: 'stop' as const, noun: ['stop', 'stops'] as [string, string] };
  const missing = notRead(taken);
  if (missing) return { ...meta, reach: missing, bins: [], marks: [], total: null };
  const reading = taken.reading as Reading;
  const stops = part<Stop[]>(reading, 'stops') ?? [];
  const coverage = part<{ system?: Coverage }>(reading, 'coverage');
  let from = when(coverage?.system?.retained_from);
  let note: string | null = null;
  const count = typeof reading.params.count === 'number' ? reading.params.count : 20;
  if (stops.length >= count) {
    const oldest = stops[stops.length - 1];
    const earliest = Math.min(...stopTimes(oldest).map((t) => t.at));
    from = Math.max(from ?? earliest, earliest);
    note = `Only the newest ${count} stops were returned; older ones were not read.`;
  }
  const reach = spanReach(from, when(reading.asked_at), range, note);
  const marks = stops.map((stop, index): Mark | null => {
    const times = stopTimes(stop);
    if (!times.length || !times.some((t) => inRange(t.at, range))) return null;
    const bug = stop.bugcheck ? stop.bugcheck.name ?? stop.bugcheck.code : null;
    const down = stop.down_seconds != null ? `down ${stop.down_seconds < 90 ? `${stop.down_seconds} s` : `${Math.round(stop.down_seconds / 60)} min`}` : null;
    return {
      id: `stop:${stop.records.start ?? stop.records.power_41 ?? index}:${stop.started_at ?? stop.reported_at}`,
      track: 'stops', at: Math.min(...times.map((t) => t.at)), times, tone: 'stop', stop,
      title: 'Unplanned stop',
      line: [bug ? `bug check ${bug}` : stop.no_bugcheck_recorded ? 'no bug check recorded' : null, down, stop.dump ? 'a dump on disk' : null].filter(Boolean).join(' · '),
    };
  }).filter((m): m is Mark => m !== null);
  return { ...meta, reach, bins: [], marks, total: marks.length };
}

/** A stop's evidence times. Each is Windows' own, and they can disagree; all are kept. */
export function stopTimes(stop: Stop): { label: string; at: number }[] {
  const out: { label: string; at: number }[] = [];
  const last = when(stop.last_record_before?.TimeCreated);
  const estimate = when(stop.stopped_at);
  const start = when(stop.started_at);
  if (last !== null) out.push({ label: 'Last System record', at: last });
  if (estimate !== null) out.push({ label: "Windows' stop estimate", at: estimate });
  if (start !== null) out.push({ label: 'Next start', at: start });
  // A restart Windows announced without a returned start still happened; its announcement is its time.
  const announced = when(stop.announced_at);
  if (start === null && announced !== null) out.push({ label: 'Restart announced', at: announced });
  if (!out.length) {
    const reported = when(stop.reported_at);
    if (reported !== null) out.push({ label: 'Report filed', at: reported });
  }
  return out;
}

interface Buckets { from: string; bucket_seconds: number; totals: (number | null)[] }

function bucketTrack(taken: Taking, range: Range, id: 'hardware' | 'kernel', coverageKey: string): Track {
  const meta = id === 'hardware'
    ? { label: 'Hardware errors', source: 'System log, WHEA-Logger' }
    : { label: 'Hardware reports', source: 'Kernel-WHEA channel' };
  const common = { ...base(id, taken), ...meta, view: 'errors' as ViewId, kind: 'events' as const, tone: 'hardware' as const, noun: ['report', 'reports'] as [string, string] };
  const missing = notRead(taken);
  if (missing) return { ...common, reach: missing, bins: [], marks: [], total: null };
  const reading = taken.reading as Reading;
  const buckets = part<Buckets>(reading, 'buckets');
  const coverage = part<Record<string, Coverage>>(reading, 'coverage')?.[coverageKey];
  const reach = spanReach(when(coverage?.covered_from), when(coverage?.covered_until), range, null);
  const start = when(buckets?.from) ?? range.from;
  const width = (buckets?.bucket_seconds ?? bucketSeconds(range)) * 1000;
  const list: Bin[] = (buckets?.totals ?? []).map((count, i) => ({ from: start + i * width, to: start + (i + 1) * width, count }))
    .filter((b) => b.to > range.from && b.from < range.to);
  const masked = maskBins(list, reach);
  return { ...common, reach, bins: masked, marks: [], total: sum(masked) };
}

function faultTrack(taken: Taking, range: Range): Track {
  const meta = { ...base('faults', taken), label: 'Program faults', source: 'Application log', view: 'crashes' as ViewId, kind: 'events' as const, tone: 'ink' as const, noun: ['fault', 'faults'] as [string, string] };
  const missing = notRead(taken);
  if (missing) return { ...meta, reach: missing, bins: [], marks: [], total: null };
  const reading = taken.reading as Reading;
  const records = part<EventRecord[]>(reading, 'records') ?? [];
  const decoded = part<Fault[]>(reading, 'decoded') ?? [];
  const coverage = part<Coverage>(reading, 'coverage');
  const collection = part<Collection>(reading, 'collection');
  const note = capNote(collection, 'faults');
  let from = when(coverage?.covered_from);
  if (collection?.truncated && records.length) from = Math.max(from ?? 0, Math.min(...records.map((r) => Date.parse(r.TimeCreated))));
  const reach = spanReach(from, when(coverage?.covered_until) ?? when(reading.asked_at), range, note);
  const timeOf = new Map(records.map((r) => [`${r.Log ?? 'Application'}:${r.RecordId}`, Date.parse(r.TimeCreated)]));
  const marks: Mark[] = [];
  for (const fault of decoded) {
    const at = timeOf.get(`${fault.Log ?? 'Application'}:${fault.RecordId}`) ?? null;
    if (!inRange(at, range)) continue;
    const kind = fault.kind === 'application crash' ? 'Program crash' : fault.kind === 'application hang' ? 'Program hang' : fault.kind === 'live kernel event' ? 'Live kernel report' : 'Fault report';
    const detail = [moduleOf(fault), exceptionOf(fault)].filter(Boolean).join(' · ');
    marks.push({ id: `fault:${fault.Log ?? 'Application'}:${fault.RecordId}`, track: 'faults', at, times: [{ label: 'Filed', at }], tone: 'ink', fault,
      title: `${kind}: ${appOf(fault)}`, line: detail });
  }
  // Faults are drawn as marks, one per filing; counting them into bars as well would draw them twice.
  return { ...meta, reach, bins: [], marks, total: marks.length };
}

function logTrack(taken: Taking, range: Range): Track {
  const meta = { ...base('log', taken), label: 'System log', source: 'Every level', view: 'record' as ViewId, kind: 'events' as const, tone: 'ink' as const, noun: ['record', 'records'] as [string, string] };
  const missing = notRead(taken);
  if (missing) return { ...meta, reach: missing, bins: [], marks: [], total: null };
  const reading = taken.reading as Reading;
  const records = part<EventRecord[]>(reading, 'records') ?? [];
  const coverage = part<Coverage>(reading, 'coverage');
  const collection = part<Collection>(reading, 'collection');
  let from = when(coverage?.covered_from);
  if (collection?.truncated && records.length) from = Math.max(from ?? 0, Math.min(...records.map((r) => Date.parse(r.TimeCreated))));
  const reach = spanReach(from, when(coverage?.covered_until) ?? when(reading.asked_at), range, capNote(collection, 'records'));
  const list = bins(range);
  const marks: Mark[] = [];
  let total = 0;
  for (const record of records) {
    const at = Date.parse(record.TimeCreated);
    if (!inRange(at, range)) continue;
    total += 1;
    const bin = place(list, at);
    if (bin) {
      bin.count = (bin.count ?? 0) + 1;
      bin.levels ??= [0, 0, 0, 0];
      bin.levels[LEVEL_INDEX[record.Level] ?? 3] += 1;
    }
    if (record.Level === 1) {
      marks.push({ id: `log:${record.RecordId}`, track: 'log', at, times: [{ label: 'Written', at }], tone: 'ink', record,
        title: `Critical: ${shortProvider(record.ProviderName)} ${record.Id}`, line: firstLine(record.Message) });
    }
  }
  return { ...meta, reach, bins: maskBins(list, reach), marks, total };
}

interface Change { at: string | null; source: string; ref: { log: string; record_id: number | string }; kind: string; subject: string | null; version: string | null }
const CHANGE_WORDS: Record<string, string> = {
  update_installed: 'Update installed', update_failed: 'Update failed', device_configured: 'Device configured',
  msi_install_succeeded: 'Installed', msi_install_failed: 'Installation failed', msi_install_unknown: 'Installation, result unknown',
  msi_removal_succeeded: 'Removed', msi_removal_failed: 'Removal failed', msi_removal_unknown: 'Removal, result unknown', unmapped_event: 'Change, not decoded',
};

function changeTrack(taken: Taking, range: Range): Track {
  const meta = { ...base('changes', taken), label: 'Changes', source: 'Windows Update, device setup, installers', view: null, kind: 'events' as const, tone: 'ink' as const, noun: ['change', 'changes'] as [string, string] };
  const missing = notRead(taken);
  if (missing) return { ...meta, reach: missing, bins: [], marks: [], total: null };
  const reading = taken.reading as Reading;
  const changes = part<Change[]>(reading, 'changes') ?? [];
  const coverage = part<Coverage>(reading, 'coverage');
  const reach = spanReach(when(coverage?.covered_from) ?? range.from, when(coverage?.covered_until) ?? when(reading.asked_at), range,
    coverage?.complete === false ? 'Windows did not return every change in this range; the reach shows what was read.' : null);
  const marks = changes.map((change): Mark | null => {
    const at = when(change.at);
    if (!inRange(at, range)) return null;
    return { id: `change:${change.ref.log}:${change.ref.record_id}`, track: 'changes', at, times: [{ label: 'Recorded', at }], tone: 'ink',
      title: CHANGE_WORDS[change.kind] ?? 'Change', line: [change.subject, change.version].filter(Boolean).join(' · ') };
  }).filter((m): m is Mark => m !== null);
  return { ...meta, reach, bins: [], marks, total: marks.length };
}

interface Sample { at: string; cpu_percent: number | null }

function performanceTrack(taken: Taking, range: Range): Track {
  const meta = { ...base('performance', taken), label: 'Processor load', source: 'Samples this computer stored', view: 'performance' as ViewId, kind: 'samples' as const, tone: 'ink' as const, noun: ['sample', 'samples'] as [string, string] };
  const missing = notRead(taken);
  if (missing) return { ...meta, reach: missing, bins: [], marks: [], total: null };
  const reading = taken.reading as Reading;
  const samples = (part<Sample[]>(reading, 'samples') ?? []).map((s) => ({ at: Date.parse(s.at), value: s.cpu_percent })).filter((s) => inRange(s.at, range));
  const interval = (part<{ settings?: { interval_seconds?: number } }>(reading, 'collection')?.settings?.interval_seconds ?? 60) * 1000;
  // A sample covers the stretch up to the next one only when the next arrived on time; a longer
  // interval is a gap, and nothing is said about what happened inside it.
  const spans: [number, number][] = [];
  for (const sample of samples) {
    const last = spans[spans.length - 1];
    if (last && sample.at - last[1] <= interval * 2.5) last[1] = sample.at;
    else spans.push([sample.at, sample.at + interval]);
  }
  const windowHours = typeof reading.params.hours === 'number' ? reading.params.hours : 48;
  const note = samples.length ? (rangeHours(range) > windowHours ? `Stored samples are read for ${windowHours} hours at most.` : null)
    : 'No samples are stored for this range, so nothing was measured here.';
  return { ...meta, reach: { state: 'read', spans, note }, bins: [], marks: [], total: samples.length, samples };
}

interface ReliabilityDay { day: string; index_min: number | null; records: Record<string, number> | null }

function reliabilityTrack(taken: Taking, range: Range): Track {
  const meta = { ...base('reliability', taken), label: 'Reliability record', source: "Windows' Reliability Monitor", view: 'crashes' as ViewId, kind: 'events' as const, tone: 'ink' as const, noun: ['record', 'records'] as [string, string] };
  const missing = notRead(taken);
  if (missing) return { ...meta, reach: missing, bins: [], marks: [], total: null };
  const reading = taken.reading as Reading;
  const rollup = part<{ days: ReliabilityDay[] }>(reading, 'days');
  const asked = when(reading.asked_at) ?? range.to;
  const days = typeof reading.params.days === 'number' ? reading.params.days : 30;
  const list: Bin[] = (rollup?.days ?? []).map((d) => {
    const from = Date.parse(`${d.day}T00:00:00Z`);
    return { from, to: from + DAY, count: d.records ? Object.values(d.records).reduce((a, b) => a + b, 0) : null };
  }).filter((b) => b.to > range.from && b.from < range.to);
  const reach = spanReach(asked - days * DAY, asked, range, null);
  const masked = maskBins(list, reach);
  return { ...meta, reach, bins: masked, marks: [], total: sum(masked) };
}

// ------------------------------------------------------------------ reading the tracks

export function shortProvider(name: string): string {
  return name.replace(/^Microsoft-Windows-/, '');
}

export function firstLine(text: string | null | undefined): string {
  return text ? text.split('\n')[0].trim() : 'No message text';
}

/** Whether a moment falls inside the stretches a track read. */
export function covers(track: Track, at: number): boolean {
  return track.reach.state === 'read' && track.reach.spans.some(([s, e]) => at >= s && at <= e);
}

/** The part of [from, to] a track read, as a fraction from 0 to 1. */
export function coveredShare(track: Track, from: number, to: number): number {
  if (track.reach.state !== 'read' || to <= from) return 0;
  const covered = track.reach.spans.reduce((n, [s, e]) => n + Math.max(0, Math.min(e, to) - Math.max(s, from)), 0);
  return covered / (to - from);
}

/** What a track returned in a stretch: marks, and the counted buckets that overlap it. */
export function within(track: Track, from: number, to: number): { marks: Mark[]; count: number | null; binFrom: number; binTo: number; levels: [number, number, number, number] | null } {
  const marks = track.marks.filter((m) => m.times.some((t) => t.at >= from && t.at <= to));
  const overlapping = track.bins.filter((b) => b.to > from && b.from < to);
  const known = overlapping.filter((b) => b.count !== null);
  const count = known.length ? sum(known) : track.bins.length ? null : marks.length;
  const levels = overlapping.some((b) => b.levels) ? overlapping.reduce<[number, number, number, number]>((acc, b) => {
    b.levels?.forEach((n, i) => { acc[i] += n; });
    return acc;
  }, [0, 0, 0, 0]) : null;
  return { marks, count, binFrom: overlapping[0]?.from ?? from, binTo: overlapping[overlapping.length - 1]?.to ?? to, levels };
}

/** The System log records a reading returned inside a stretch, newest first. */
export function logRecords(track: Track, from: number, to: number): EventRecord[] {
  const records = part<EventRecord[]>(track.taken.reading, 'records') ?? [];
  return records.filter((r) => {
    const t = Date.parse(r.TimeCreated);
    return t >= from && t <= to;
  });
}
