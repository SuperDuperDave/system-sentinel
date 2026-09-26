/**
 * The case file's shared vocabulary: how a stop or a lead becomes an exhibit, and how the case
 * file writes a time. One place, so Home, Crashes and the case say the same thing the same way.
 */
import type { Reading } from './api';
import type { CaseSummary, NewExhibit, StopRef } from './cases';
import { duration } from './Sections';

/** The fields of a crash reading's stop this file needs; Crashes owns the full shape. */
export interface StopLike {
  started_at: string | null;
  announced_at: string | null;
  stopped_at: string | null;
  reported_at: string | null;
  down_seconds: number | null;
  bugcheck: { code: string | null; name: string | null } | null;
  no_bugcheck_recorded: boolean | null;
  dump: { name: string | null; matched_by: string } | null;
  last_record_before: { RecordId: number | string | null; TimeCreated: string | null; ProviderName: string | null; Id: number | null } | null;
  records: { start: number | string | null; power_41: number | string | null; eventlog_6008: number | string | null; wer_1001: number | string | null; report: (number | string)[] };
}

/** A crash-reading signal, as the signals reading returns it. */
export interface Lead {
  id: string;
  class: string;
  title: string;
  summary: string;
  evidence: Record<string, unknown>;
  readings: string[];
}

const DAY = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' });
const DAY_LONG = new Intl.DateTimeFormat(undefined, { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
const TIME = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', hour12: false });
const TIME_S = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$/;

export const isIso = (value: string) => ISO.test(value);

function date(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "Sep 12, 06:11" in the browser's local time. */
export function when(iso: string | null | undefined, seconds = false): string {
  const d = date(iso);
  return d ? `${DAY.format(d)}, ${(seconds ? TIME_S : TIME).format(d)}` : 'Not recorded';
}
export const dayOf = (iso: string) => { const d = date(iso); return d ? DAY.format(d) : 'undated'; };
export const dayLong = (iso: string) => { const d = date(iso); return d ? DAY_LONG.format(d) : 'Undated'; };
export const timeOf = (iso: string, seconds = true) => { const d = date(iso); return d ? (seconds ? TIME_S : TIME).format(d) : ''; };

/** How long something lasted; under a minute and a half the seconds are the point. */
export function lasted(seconds: number): string {
  if (seconds < 90) return `${seconds} s`;
  const m = Math.floor(seconds / 60);
  return seconds < 3600 ? `${m} min ${seconds % 60} s` : duration(seconds);
}

/** The gap between two moments on the case's timeline, in words. */
export function gap(fromIso: string, toIso: string): string | null {
  const s = Math.abs(Date.parse(toIso) - Date.parse(fromIso)) / 1000;
  if (!Number.isFinite(s)) return null;
  if (s < 1) return 'at the same moment';
  if (s < 90) return `${Math.round(s)} s later`;
  if (s < 5400) return `${Math.round(s / 60)} min later`;
  if (s < 129600) return `${Math.round(s / 3600)} h later`;
  return `${Math.round(s / 86400)} days later`;
}

export function stopMoment(stop: StopLike): string | null {
  return stop.stopped_at ?? stop.started_at ?? stop.announced_at ?? stop.reported_at;
}

export function bugcheckWords(stop: StopLike): string {
  const named = [stop.bugcheck?.code, stop.bugcheck?.name].filter(Boolean).join(' · ');
  if (named) return named;
  return stop.no_bugcheck_recorded === null ? 'Bug check status unknown' : stop.no_bugcheck_recorded ? 'No bug check recorded' : 'Bug check not named';
}

export function stopRef(stop: StopLike): StopRef {
  return { started_at: stop.started_at, stopped_at: stop.stopped_at, records: { start: stop.records.start, power_41: stop.records.power_41, eventlog_6008: stop.records.eventlog_6008 } };
}

/** Whether two references name the same stop: the same start or the same Kernel-Power 41. */
export function sameStop(a: StopRef | undefined | null, b: StopRef | undefined | null): boolean {
  if (!a || !b) return false;
  if (a.records.start != null && String(a.records.start) === String(b.records.start)) return true;
  return a.records.power_41 != null && String(a.records.power_41) === String(b.records.power_41);
}

/** The open case that was opened from this stop, if any. */
export function caseForStop(cases: CaseSummary[], stop: StopLike): CaseSummary | undefined {
  const ref = stopRef(stop);
  return cases.find((c) => c.state === 'open' && sameStop(c.opened_from.stop, ref));
}

export function stopTitle(stop: StopLike): string {
  const at = stopMoment(stop);
  return `The stop on ${at ? dayOf(at) : 'an unknown day'}${stop.bugcheck?.name ? `: ${stop.bugcheck.name}` : ''}`;
}

/** A stop as an exhibit: the crash reading's derived stop, with the records it rests on. */
export function stopExhibit(stop: StopLike, envelope: Reading): NewExhibit {
  const ids = [stop.records.start, stop.records.power_41, stop.records.eventlog_6008, stop.records.wer_1001, ...(stop.records.report ?? [])].filter((id): id is number | string => id != null);
  const last = stop.last_record_before;
  const facts: [string, string][] = [
    ['Windows stop estimate', stop.stopped_at ?? 'Not recorded'],
    ['Next start', stop.started_at ?? 'Not recorded'],
    ['Down for', stop.down_seconds == null ? 'Not recorded' : lasted(stop.down_seconds)],
    ['Bug check', bugcheckWords(stop)],
    ['Dump', stop.dump?.name ? `${stop.dump.name} · matched by ${stop.dump.matched_by}` : 'None matched'],
  ];
  if (last?.TimeCreated) facts.push(['Last System record before restart', `${last.ProviderName ?? 'Unknown source'} ${last.Id ?? ''} · ${last.TimeCreated}`]);
  return {
    kind: 'selection', title: stopTitle(stop), reading: envelope.reading, params: envelope.params, asked_at: envelope.asked_at,
    outcome: envelope.outcome, classes: ['derived'], ids, moment: stopMoment(stop), facts, note: null, citations: null, stop: stopRef(stop),
  };
}

/** A lead as an exhibit: inferred, with its rule in the open. */
export function leadExhibit(lead: Lead, envelope: Reading): NewExhibit {
  const times = Object.values(lead.evidence).flat().filter((v): v is string => typeof v === 'string' && isIso(v)).sort();
  return {
    kind: 'selection', title: lead.title, reading: envelope.reading, params: envelope.params, asked_at: envelope.asked_at,
    outcome: envelope.outcome, classes: ['inferred'], ids: null, moment: times[times.length - 1] ?? null,
    facts: [['What the rule says', lead.summary], ['Drawn from', lead.readings.join(', ')]], note: null, citations: null,
  };
}
