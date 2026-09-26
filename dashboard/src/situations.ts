/**
 * The situations: what a person arrives with, the one observed fact each door carries, and the
 * fixed order of readings behind it. Defined once, here, so the door a person opens and the calls
 * an agent is handed are the same object.
 *
 * Every function in this file is pure. A door's fact is read off the envelope the door took; it
 * adds no measurement of its own. The one rule the dashboard adds is the attention mark: a stop or
 * a report newer than the last time this person opened that door on this browser. It only ever
 * points at a record that is there, and opening the door acknowledges it.
 */
import type { EventRecord, Reading } from './api';
import { knownOfReading, knownWord, type Attention, type Known } from './Marks';
import type { ViewId } from './store';
import type { StackState } from './stack';

export type DoorId = 'stopped' | 'programs' | 'slow' | 'disk' | 'hardware' | 'agent';


type Params = Record<string, string | number | boolean | number[]>;

/** One reading an agent would call, in order, with what it answers. */
export interface RecipeStep { reading: string; params: Params; answers: string }

export interface Door {
  id: DoorId;
  view: ViewId;
  /** The person's words. Never the name of a cause. */
  question: string;
  /** The door's own reading, taken for the home and the rail. Null for the handoff door. */
  reading: { name: string; params: Params } | null;
  /** What sits behind the door, in the order it is read. */
  recipe: RecipeStep[];
}

export const DOORS: Door[] = [
  {
    id: 'stopped', view: 'stopped', question: 'It stopped or restarted by itself',
    reading: { name: 'crash', params: { count: 5 } },
    recipe: [
      { reading: 'crash', params: { count: 5 }, answers: 'the unplanned stops, newest first; choose one' },
      { reading: 'record', params: { before: '<next start>', count: 25 }, answers: 'what the System log held before it' },
      { reading: 'changes', params: { before: '<stop estimate>', hours: 168, count: 100 }, answers: 'updates, drivers and installs in the week before' },
      { reading: 'whea_window', params: { source: 'system', since: '<stop estimate − 1 h>', before: '<next start + 1 h>', order: 'oldest', count: 50 }, answers: 'hardware error reports around it, one call per source' },
    ],
  },
  {
    id: 'programs', view: 'programs', question: 'A program crashed or froze',
    reading: { name: 'faults', params: { count: 30 } },
    recipe: [
      { reading: 'faults', params: { count: 30 }, answers: 'crashes, hangs and the kernel’s live reports, by when Windows filed them' },
      { reading: 'record', params: { before: '<fault time>', count: 25 }, answers: 'what the System log held before one of them' },
      { reading: 'dumps', params: {}, answers: 'the dump files on disk' },
    ],
  },
  {
    id: 'slow', view: 'performance', question: 'It feels slow',
    reading: { name: 'system', params: {} },
    recipe: [
      { reading: 'system', params: {}, answers: 'processor load, memory and uptime now' },
      { reading: 'processes', params: {}, answers: 'who is using the machine now' },
      { reading: 'performance_history', params: { hours: 6 }, answers: 'the samples Sentinel kept, with their gaps' },
    ],
  },
  {
    id: 'disk', view: 'space', question: 'The disk is filling up',
    reading: { name: 'hardware.storage', params: {} },
    recipe: [
      { reading: 'hardware.storage', params: {}, answers: 'free space on each volume' },
      { reading: 'space', params: {}, answers: 'a bounded walk of the home folder, on request' },
    ],
  },
  {
    id: 'hardware', view: 'errors', question: 'The hardware reported errors',
    reading: { name: 'whea', params: { count: 50 } },
    recipe: [
      { reading: 'whea', params: { count: 50 }, answers: 'the newest reports from both logs' },
      { reading: 'storms', params: { hours: 24 }, answers: 'System report traffic by the minute, with its lead' },
      { reading: 'whea_reports', params: {}, answers: 'the separate Kernel-WHEA channel by report time' },
    ],
  },
  {
    id: 'agent', view: 'stack', question: 'I want my agent to look',
    reading: null,
    recipe: [
      { reading: 'stack_list', params: {}, answers: 'what the person handed over' },
      { reading: 'compose', params: {}, answers: 'the handoff as one document, with provenance' },
    ],
  },
];

export const doorOf = (id: DoorId): Door => DOORS.find((door) => door.id === id)!;
export const doorForView = (view: ViewId): Door | undefined => DOORS.find((door) => door.view === view);

/** What a door says. Every field is read off an envelope; `attention` is the one stated rule. */
export interface DoorFact {
  known: Known;
  /** The one number, when there is one, and what it counts. */
  figure: string | null;
  unit: string | null;
  /** What the number is, or the whole answer when there is no number. */
  caption: string;
  /** The exact values behind it, in the readout face. */
  exact: string | null;
  /** How much was read: the scope an agent would need to repeat it. */
  scope: string;
  /** The kind of mark this door can carry; whether it is shown is the acknowledgement's to say. */
  attention: Attention | null;
  /** The newest record the mark would point at. */
  newest: string | null;
  /** The rail's compact form of the same fact. */
  mini: string;
}

const STAMP = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
const CLOCK = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', hour12: false });

export function stamp(iso: string | null | undefined): string {
  const at = iso ? Date.parse(iso) : NaN;
  return Number.isFinite(at) ? STAMP.format(at) : 'time not recorded';
}

/** An age as a figure and its unit, in the largest unit that still says something. */
export function ageParts(iso: string, now = Date.now()): { figure: string; unit: string; mini: string } | null {
  const at = Date.parse(iso);
  if (!Number.isFinite(at)) return null;
  const minutes = Math.max(0, (now - at) / 60_000);
  if (minutes < 1) return { figure: '<1', unit: 'min ago', mini: 'now' };
  if (minutes < 90) { const n = Math.round(minutes); return { figure: String(n), unit: 'min ago', mini: `${n} min` }; }
  const hours = minutes / 60;
  if (hours < 36) { const n = Math.round(hours); return { figure: String(n), unit: n === 1 ? 'hour ago' : 'hours ago', mini: `${n} h` }; }
  const days = hours / 24;
  if (days < 60) { const n = Math.round(days); return { figure: String(n), unit: n === 1 ? 'day ago' : 'days ago', mini: `${n} d` }; }
  const months = Math.round(days / 30.44);
  return { figure: String(months), unit: 'months ago', mini: `${months} mo` };
}

function sectionOf<T>(reading: Reading | null | undefined, name: string): T | null {
  return (reading?.sections.find((s) => s.name === name)?.data as T | undefined) ?? null;
}

const taken = (reading: Reading) => `read ${CLOCK.format(Date.parse(reading.asked_at))}`;

/** The shared shape of a door that did not observe the machine: its own word, never a zero. */
function unobserved(known: Known, reading: Reading | null, what: string): DoorFact {
  if (known === 'taking') return { known, figure: null, unit: null, caption: 'Reading…', exact: null, scope: what, attention: null, newest: null, mini: '…' };
  const why: Record<string, string> = {
    notasked: 'Not asked yet',
    unreached: 'Not reached: Sentinel could not get an answer from Windows',
    timeout: 'Timed out: the query did not finish in time',
    denied: 'Refused: Windows did not allow this reading',
    failed: 'Failed: Windows or PowerShell reported an error',
  };
  const detail = reading?.error?.detail ? reading.error.detail.split('\n')[0] : null;
  return { known, figure: null, unit: null, caption: why[known] ?? knownWord(known), exact: detail, scope: what, attention: null, newest: null, mini: knownWord(known).toLowerCase() };
}

interface StopLike { started_at: string | null; stopped_at: string | null; announced_at: string | null; reported_at: string | null; bugcheck: { name: string | null; code: string | null } | null; no_bugcheck_recorded: boolean | null }

/** The time a stop is placed at on the home: Windows' estimate, else the evidence that exists. */
export function stopMoment(stop: StopLike): string | null {
  return stop.stopped_at ?? stop.started_at ?? stop.announced_at ?? stop.reported_at;
}

export function stoppedFact(reading: Reading | null, known: Known): DoorFact {
  if (known !== 'observed' && known !== 'zero') return unobserved(known, reading, 'crash');
  const stops = sectionOf<StopLike[]>(reading, 'stops') ?? [];
  const coverage = sectionOf<{ system?: { retained_from?: string | null } }>(reading, 'coverage');
  const since = coverage?.system?.retained_from ? `the System log reaches back to ${stamp(coverage.system.retained_from)}` : 'within what the System log retains';
  if (!stops.length) {
    return { known: 'zero', figure: null, unit: null, caption: 'No unplanned stop returned', exact: since, scope: `crash · ${taken(reading!)}`, attention: null, newest: null, mini: 'none' };
  }
  const newest = stops[0];
  const at = stopMoment(newest);
  const age = at ? ageParts(at) : null;
  const named = newest.bugcheck?.name ?? newest.bugcheck?.code ?? (newest.no_bugcheck_recorded ? 'no bug check recorded' : null);
  return {
    known: 'observed',
    figure: age?.figure ?? null,
    unit: age?.unit ?? null,
    caption: age ? (newest.stopped_at ? 'since the last unplanned stop, by Windows’ estimate' : 'since the last unplanned stop, by its next start') : 'Unplanned stop, time not recorded',
    exact: [stamp(at), named].filter(Boolean).join(' · '),
    scope: `crash · ${stops.length} ${stops.length === 1 ? 'stop' : 'stops'} returned · ${taken(reading!)}`,
    attention: at ? 'stop' : null,
    newest: at,
    mini: age?.mini ?? 'stop',
  };
}

interface FaultLike { RecordId: number | string; Log?: string; kind: string; fields: Record<string, unknown>; report?: { name: string | null; code: string | null } }

const FAULT_WORD: Record<string, string> = { 'application crash': 'program crash', 'application hang': 'program hang', 'live kernel event': 'live kernel report' };

export function programsFact(reading: Reading | null, known: Known): DoorFact {
  if (known !== 'observed' && known !== 'zero') return unobserved(known, reading, 'faults');
  const decoded = sectionOf<FaultLike[]>(reading, 'decoded') ?? [];
  const records = sectionOf<EventRecord[]>(reading, 'records') ?? [];
  const coverage = sectionOf<{ retained_from?: string | null }>(reading, 'coverage');
  if (!decoded.length) {
    return { known: 'zero', figure: null, unit: null, caption: 'No crash, hang or live kernel report returned',
      exact: coverage?.retained_from ? `the Application log reaches back to ${stamp(coverage.retained_from)}` : null,
      scope: `faults · ${taken(reading!)}`, attention: null, newest: null, mini: 'none' };
  }
  const times = new Map(records.map((r) => [`${r.Log ?? 'Application'}:${r.RecordId}`, r.TimeCreated]));
  const timed = decoded.map((fault) => ({ fault, at: times.get(`${fault.Log ?? 'Application'}:${fault.RecordId}`) ?? null }));
  const newest = timed.filter((t) => t.at).sort((a, b) => Date.parse(b.at!) - Date.parse(a.at!))[0] ?? timed[0];
  const age = newest.at ? ageParts(newest.at) : null;
  const byKind = sectionOf<{ by_kind: Record<string, number> }>(reading, 'summary')?.by_kind ?? {};
  const subject = newest.fault.report?.name ?? newest.fault.report?.code ?? (typeof newest.fault.fields.AppName === 'string' ? newest.fault.fields.AppName : null);
  return {
    known: 'observed',
    figure: age?.figure ?? null,
    unit: age?.unit ?? null,
    caption: `since the last ${FAULT_WORD[newest.fault.kind] ?? newest.fault.kind}`,
    exact: [subject, newest.at ? stamp(newest.at) : null].filter(Boolean).join(' · '),
    scope: `faults · ${Object.values(byKind).reduce((a, b) => a + b, 0) || decoded.length} reports in ${records.length} records`,
    attention: newest.at ? 'report' : null,
    newest: newest.at,
    mini: age?.mini ?? 'report',
  };
}

interface Snapshot { processor_load_percent?: number | null; memory_total_kb?: number | null; memory_free_kb?: number | null; uptime_seconds?: number | null }

export function slowFact(reading: Reading | null, known: Known): DoorFact {
  if (known !== 'observed' && known !== 'zero') return unobserved(known, reading, 'system');
  const snap = sectionOf<Snapshot>(reading, 'snapshot') ?? {};
  const load = snap.processor_load_percent;
  const used = snap.memory_total_kb && snap.memory_free_kb != null ? Math.round((1 - snap.memory_free_kb / snap.memory_total_kb) * 100) : null;
  const up = snap.uptime_seconds != null ? upFor(snap.uptime_seconds) : null;
  return {
    known: load == null ? 'zero' : 'observed',
    figure: load == null ? null : String(load),
    unit: load == null ? null : '%',
    caption: load == null ? 'Processor load not reported' : 'processor load when read',
    exact: [used == null ? null : `memory ${used}% in use`, up ? `up ${up}` : null].filter(Boolean).join(' · ') || null,
    scope: `system · ${taken(reading!)}`,
    attention: null, newest: null,
    mini: load == null ? '—' : `${load}%`,
  };
}

function upFor(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return d ? `${d} d ${h} h` : h ? `${h} h ${m} min` : `${m} min`;
}

interface StorageDerived { disks?: { volumes?: { drive_letter?: string | null; free_gb?: number | null; used_percent?: number | null }[] }[] }

export function diskFact(reading: Reading | null, known: Known): DoorFact {
  if (known !== 'observed' && known !== 'zero') return unobserved(known, reading, 'hardware.storage');
  const volumes = (sectionOf<StorageDerived>(reading, 'derived')?.disks ?? []).flatMap((disk) => disk.volumes ?? [])
    .filter((v) => typeof v.free_gb === 'number');
  if (!volumes.length) {
    return { known: 'zero', figure: null, unit: null, caption: 'No volume with a free-space figure returned', exact: null, scope: `hardware.storage · ${taken(reading!)}`, attention: null, newest: null, mini: '—' };
  }
  const fullest = [...volumes].sort((a, b) => (b.used_percent ?? 0) - (a.used_percent ?? 0))[0];
  const free = fullest.free_gb!;
  const figure = free >= 100 ? String(Math.round(free)) : free.toFixed(1);
  const letter = fullest.drive_letter ? `${fullest.drive_letter}:` : 'a volume without a letter';
  return {
    known: 'observed',
    figure,
    unit: 'GB free',
    caption: volumes.length > 1 ? `on ${letter}, the fullest of ${volumes.length} volumes` : `on ${letter}`,
    exact: fullest.used_percent != null ? `${fullest.used_percent}% used` : null,
    scope: `hardware.storage · ${taken(reading!)}`,
    attention: null, newest: null,
    mini: `${figure} GB`,
  };
}

interface WheaCoverage { sources?: Record<string, { log?: string; answered?: boolean; shown?: number }> }

export function hardwareFact(reading: Reading | null, known: Known): DoorFact {
  if (known !== 'observed' && known !== 'zero') return unobserved(known, reading, 'whea');
  const records = sectionOf<EventRecord[]>(reading, 'records') ?? [];
  const sources = Object.values(sectionOf<WheaCoverage>(reading, 'coverage')?.sources ?? {});
  const unanswered = sources.filter((s) => s.answered === false).length;
  const gap = unanswered ? ` · ${unanswered} of ${sources.length} logs not read` : '';
  if (!records.length) {
    return { known: 'zero', figure: null, unit: null, caption: 'No hardware error report returned', exact: `from either log's retained record${gap}`, scope: `whea · ${taken(reading!)}`, attention: null, newest: null, mini: 'none' };
  }
  const newest = records.reduce((a, b) => Date.parse(b.TimeCreated) > Date.parse(a.TimeCreated) ? b : a);
  const age = ageParts(newest.TimeCreated);
  const perLog = sources.map((s) => `${s.log === 'System' ? 'System' : 'Kernel-WHEA'} ${s.shown ?? 0}`).join(', ');
  return {
    known: 'observed',
    figure: age?.figure ?? null,
    unit: age?.unit ?? null,
    caption: 'since the newest report was filed',
    exact: `${records.length} ${records.length === 1 ? 'report' : 'reports'} returned${perLog ? ` · ${perLog}` : ''}${gap}`,
    scope: `whea · ${taken(reading!)}`,
    attention: 'report',
    newest: newest.TimeCreated,
    mini: age?.mini ?? 'reports',
  };
}

export function agentFact(stack: StackState | null, problem: string | null): DoorFact {
  if (!stack) {
    return problem
      ? { known: 'unreached', figure: null, unit: null, caption: 'Not reached: the handoff could not be read', exact: problem, scope: 'stack_list', attention: null, newest: null, mini: 'not reached' }
      : { known: 'taking', figure: null, unit: null, caption: 'Reading…', exact: null, scope: 'stack_list', attention: null, newest: null, mini: '…' };
  }
  const n = stack.items.length;
  return {
    known: n ? 'observed' : 'zero',
    figure: n ? String(n) : null,
    unit: n ? (n === 1 ? 'item' : 'items') : null,
    caption: n ? 'in the shared handoff' : 'The shared handoff is empty',
    exact: 'one handoff per machine; an agent at /mcp reads the same readings',
    scope: 'stack_list',
    attention: null, newest: null,
    mini: n ? `${n}` : 'empty',
  };
}

export function factFor(id: DoorId, reading: Reading | null, known: Known): DoorFact {
  switch (id) {
    case 'stopped': return stoppedFact(reading, known);
    case 'programs': return programsFact(reading, known);
    case 'slow': return slowFact(reading, known);
    case 'disk': return diskFact(reading, known);
    case 'hardware': return hardwareFact(reading, known);
    default: return unobserved('notasked', null, '');
  }
}

export { knownOfReading };

/** The recipe as text an agent can be handed: the same calls, in order, with any values filled in. */
export function recipeText(question: string, steps: RecipeStep[]): string {
  const lines = steps.map((step, index) => `${index + 1}. ${step.reading} ${JSON.stringify(step.params)} — ${step.answers}`);
  return [
    `Situation: "${question}". Read these System Sentinel readings in this order, through its MCP tools or /api/readings/<name>.`,
    ...lines,
    'Read each envelope’s outcome first: ok and empty observed the machine (empty is a finding); failed, unavailable, denied and timeout are holes to name, never zeros.',
  ].join('\n');
}
