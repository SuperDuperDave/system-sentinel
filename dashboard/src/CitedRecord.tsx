import { useState } from 'react';
import { AddToStack } from './AddToStack';
import { EventRecord, Reading, RecordId, observed, section } from './api';
import { useReading } from './useReading';
import styles from './CitedRecord.module.css';

export interface EventRef {
  role: string;
  reading: 'event_record';
  params: { log: 'System' | 'Application'; record_id: RecordId; time_created: string };
}

const MAX_RECORD_ID = 9_007_199_254_740_991;
const EXACT_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?(?:Z|[+-]\d{2}:\d{2})$/;
const ROLE: Record<string, string> = {
  start: 'Next start · Kernel-General 12',
  power_41: 'Restart announcement · Kernel-Power 41',
  eventlog_6008: 'Previous shutdown estimate · EventLog 6008',
  wer_1001: 'Rebooted from a bug check · WER-SystemErrorReporting 1001',
  report: 'Application log error report',
  display_reset: 'Display reset · Display 4101',
  last_before_restart: 'Last System record before restart',
};

/** Reject incomplete citations instead of silently looking up a different row. */
export function eventRef(value: unknown): EventRef | null {
  if (!value || typeof value !== 'object') return null;
  const candidate = value as Partial<EventRef>;
  const params = candidate.params;
  if (candidate.reading !== 'event_record' || !params || typeof params !== 'object') return null;
  if (params.log !== 'System' && params.log !== 'Application') return null;
  const id = params.record_id;
  const validId = typeof id === 'number' ? Number.isSafeInteger(id) && id >= 1 && id <= MAX_RECORD_ID
    : typeof id === 'string' && /^\d{1,16}$/.test(id) && BigInt(id) >= 1n && BigInt(id) <= BigInt(MAX_RECORD_ID);
  if (!validId) return null;
  if (typeof params.time_created !== 'string' || !EXACT_TIME.test(params.time_created) ||
      !Number.isFinite(Date.parse(params.time_created)) || Date.parse(params.time_created) < Date.UTC(1601, 0, 1)) return null;
  return { role: typeof candidate.role === 'string' ? candidate.role : 'event', reading: 'event_record', params: params as EventRef['params'] };
}

export interface RefGroup { label: string | null; refs: EventRef[]; unusable: number }

function group(value: unknown, label: string | null): RefGroup | null {
  if (!value || typeof value !== 'object' || !('refs' in value) || !Array.isArray(value.refs)) return null;
  const found = value.refs.map(eventRef);
  return { label, refs: found.filter((ref): ref is EventRef => ref !== null), unusable: found.filter((ref) => ref === null).length };
}

/** The two source shapes: direct references, or references under each returned stop. */
export function eventRefGroups(evidence: Record<string, unknown>): RefGroup[] {
  const direct = group(evidence, null);
  const stops = Array.isArray(evidence.stops) ? evidence.stops : [];
  return [direct, ...stops.map((stop, index) => group(stop, stop && typeof stop === 'object' && 'anchor_at' in stop && typeof stop.anchor_at === 'string'
    ? `Stop ${index + 1} · ${stop.anchor_at}` : `Stop ${index + 1}`))].filter((value): value is RefGroup => value !== null);
}

/** A scan keeps the lead's facts and omission counts; the separate full disclosure retains every ref. */
export function evidenceWithoutRefRows(evidence: Record<string, unknown>): Record<string, unknown> {
  const rest = { ...evidence };
  delete rest.refs;
  if (!Array.isArray(rest.stops)) return rest;
  return { ...rest, stops: rest.stops.map((stop: unknown) => {
    if (!stop || typeof stop !== 'object' || Array.isArray(stop)) return stop;
    const other = { ...stop } as Record<string, unknown>;
    delete other.refs;
    return other;
  }) };
}

interface ReferenceResult { status: 'same' | 'id_reused' | 'not_returned'; retention: 'before_retained' | 'within_retained' | 'unknown'; found_time_created?: string }

/** A held projection and an optional exact recheck stay beside the finding they support. */
export function CitedRecord({ citation, held, heldAt, heldKind = 'raw' }: { citation: EventRef | null; held?: object | null; heldAt?: string; heldKind?: 'raw' | 'projection' }) {
  const [check, setCheck] = useState(false);
  const taken = useReading<EventRecord[]>('event_record', citation?.params ?? {}, check && citation !== null);
  const reading = taken.reading;
  const reference = section<ReferenceResult>(reading as Reading<ReferenceResult> | null, 'reference');
  const records = section(reading, 'records') ?? [];
  const record = reading?.outcome === 'ok' && reference?.status === 'same' && records.length === 1 ? records[0] : null;
  const current = check && citation;
  const previous = taken.state === 'taking' && reading !== null;
  const when = reading?.asked_at ? ` · checked ${reading.asked_at}${previous ? ' (previous check)' : ''}` : '';
  const failed = reading?.outcome === 'failed' ? 'failed' : reading?.outcome === 'denied' ? 'was denied by Windows' : reading?.outcome === 'timeout' ? 'timed out' : 'could not reach Windows';
  const status = taken.state === 'lost' ? `The exact lookup did not complete: ${taken.problem}. The cited record has not been checked again.`
    : reading && !observed(reading) ? `The exact lookup ${failed}: ${reading.error?.detail ?? 'the source did not answer'}. The cited record has not been checked again.${when}`
    : reference?.status === 'id_reused' ? `This ID now names a different event${reference.found_time_created ? ` created ${reference.found_time_created}` : ''}. The cited row was not returned.${when}`
    : reference?.status === 'not_returned' ? `The cited row was not returned by the current log. ${reference.retention === 'before_retained' ? 'Its cited time predates the oldest retained row.' : reference.retention === 'within_retained' ? 'Its cited time is within the reported retention span, but that does not establish why it is missing.' : 'The retention boundary could not be established.'}${when}`
    : reading && (reading.outcome === 'ok' || reading.outcome === 'empty') && !reference ? 'The exact lookup did not return an identity comparison. Its result cannot confirm this citation.'
    : reference?.status === 'same' && !record ? 'The identity matched, but one raw row was not returned in this answer.'
    : reference?.status === 'same' ? `The same record was returned${when}.` : '';

  return <div className={styles.citation}>
    {citation ? <p className={`${styles.identity} readout`}>{ROLE[citation.role] ?? citation.role} · {citation.params.log} record {citation.params.record_id} · {citation.params.time_created}</p> : null}
    {held ? <details className={styles.raw}>
      <summary>{heldKind === 'projection' ? 'Derived last-record projection' : 'Raw record held in this crash reading'}</summary>
      <p className={`${styles.note} readout`}>{heldKind === 'projection' ? 'The crash reading kept six fields and the first line of the message.' : 'These raw fields were returned with the crash reading.'}{heldAt ? ` Reading taken ${heldAt}.` : ''} This is the held observation, not a current log lookup. Default redaction applies.</p>
      <pre className="readout">{JSON.stringify(held, null, 2)}</pre>
    </details> : null}
    {citation ? <button type="button" className={`${styles.check} readout`} onClick={() => { if (taken.state === 'taking') return; if (check) taken.retake(); else setCheck(true); }} aria-disabled={taken.state === 'taking'}>
      {taken.state === 'taking' ? 'Checking this record…' : check ? 'Check current log again' : 'Check this exact record in the current log'}
    </button> : null}
    <p className={styles.status} role="status" aria-live="polite">{current ? taken.state === 'taking' ? `Checking the current log.${reading ? ` The previous check was taken ${reading.asked_at}.` : ''}` : status : ''}</p>
    {current && reference?.status === 'same' && record ? <details className={styles.raw}>
      <summary>{previous ? 'Previous check · same record · raw fields' : 'Same record returned · raw fields'}</summary>
      <p className={`${styles.note} readout`}>Exact lookup taken {reading?.asked_at}. This is a new observation; the fields below have default redaction.</p>
      <pre className="readout">{JSON.stringify(record, null, 2)}</pre>
    </details> : null}
    {current && reading && observed(reading) && reference ? <AddToStack item={{ kind: 'reading', envelope: reading, title: `${ROLE[citation.role] ?? citation.role} · ${citation.params.log} record ${citation.params.record_id}` }} label={previous ? 'Stack previous exact check' : 'Stack this exact check'} /> : null}
    {!citation && held ? <p className={`${styles.note} readout`}>No usable exact record reference is available for a current-log check.</p> : null}
  </div>;
}
