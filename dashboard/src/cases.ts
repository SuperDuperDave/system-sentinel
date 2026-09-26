/**
 * The client of the case routes. They are a draft: interface direction C asks the API for them,
 * and today only the screenshot fixture server answers (docs/screens/fixtures/cases_draft.py).
 * A server without them answers 404, which this client reports as `CasesUnavailable` so the
 * dashboard can say so instead of pretending there are no cases.
 *
 * A case is an investigation a person keeps. Evidence enters it two ways: added in the dashboard,
 * or proposed by anything holding the token and accepted by a person. Every item records the
 * route it arrived by, never a claimed author, because one token opens every route today.
 */
import { Cls, EventRecord, Reading, Unauthorized } from './api';

export type Route = 'dashboard' | 'api';

export interface Citation {
  label: string;
  reading: string;
  params: Record<string, unknown>;
  ids?: (number | string)[];
  moment?: string | null;
  signal?: string;
  stop?: StopRef;
}

/** Enough of a stop to find it again in the crash reading. */
export interface StopRef {
  started_at: string | null;
  stopped_at: string | null;
  records: { start: number | string | null; power_41: number | string | null; eventlog_6008?: number | string | null };
}

export interface ReadRecord {
  reading: string;
  params: Record<string, unknown>;
  asked_at: string;
  outcome: string;
  /** For a reading composed from others, what each input answered. */
  inputs?: Record<string, string>;
}

export interface Exhibit {
  id: string;
  n: number;
  kind: 'reading' | 'selection' | 'note' | 'claim';
  title: string;
  reading: string | null;
  params: Record<string, unknown> | null;
  asked_at: string | null;
  outcome: string | null;
  classes: Cls[];
  ids: (number | string)[] | null;
  /** The moment the evidence is about, which orders the case's timeline. Null: not placed in time. */
  moment: string | null;
  facts: [string, string][] | null;
  note: string | null;
  citations: Citation[] | null;
  stop?: StopRef | null;
  added_at: string;
  route: Route;
  accepted_from: string | null;
}

export interface Proposal {
  id: string;
  claim: string;
  cites: Citation[];
  read: ReadRecord[];
  received_at: string;
  route: Route;
  state: 'pending' | 'accepted' | 'declined';
  decided_at: string | null;
  decline_reason: string | null;
}

export interface CaseRecordEntry { at: string; route: Route; what: string; ref: string | null }

export interface Case {
  id: string;
  title: string;
  state: 'open' | 'closed';
  opened_at: string;
  updated_at: string;
  closed_at: string | null;
  closing_note: string | null;
  opened_from: { kind: 'stop' | 'lead' | 'question' | 'reading'; label: string; stop?: StopRef };
  route: Route;
  notes: string;
  notes_updated_at: string | null;
  evidence: Exhibit[];
  proposals: Proposal[];
  record: CaseRecordEntry[];
}

export interface CaseSummary {
  id: string;
  title: string;
  state: 'open' | 'closed';
  opened_at: string;
  updated_at: string;
  closed_at: string | null;
  opened_from: Case['opened_from'];
  evidence: number;
  pending: { id: string; claim: string; received_at: string; route: Route; cites: number; read: number; not_observed: number }[];
}

/** What a view hands to a case: the fields of an exhibit the server does not assign. */
export type NewExhibit = Omit<Exhibit, 'id' | 'n' | 'added_at' | 'route' | 'accepted_from'>;

export class CasesUnavailable extends Error {
  constructor() { super('This server does not answer the case routes.'); }
}

export class AlreadyEvidence extends Error {
  constructor(readonly n: number) { super(`already exhibit ${n}`); }
}

async function send<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    credentials: 'same-origin',
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
  });
  if (res.status === 401) throw new Unauthorized();
  if (res.status === 404 && path === '/api/cases') throw new CasesUnavailable();
  const body = await res.json().catch(() => ({}));
  if (res.status === 409 && body.error === 'duplicate') throw new AlreadyEvidence(Number(body.n));
  if (!res.ok) throw new Error(body.detail ?? `${res.status}: ${res.statusText}`);
  return body as T;
}

const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });

export const listCases = () => send<{ cases: CaseSummary[] }>('/api/cases').then((b) => b.cases);
export const getCase = (id: string) => send<Case>(`/api/cases/${encodeURIComponent(id)}`);
export const openCase = (title: string, opened_from: Case['opened_from'], evidence: NewExhibit[] = []) =>
  send<Case>('/api/cases', json('POST', { title, opened_from, evidence }));
export const patchCase = (id: string, change: { title?: string; notes?: string; state?: 'open' | 'closed'; closing_note?: string }) =>
  send<Case>(`/api/cases/${encodeURIComponent(id)}`, json('PATCH', change));
export const addEvidence = (id: string, exhibit: NewExhibit) =>
  send<Case>(`/api/cases/${encodeURIComponent(id)}/evidence`, json('POST', exhibit));
export const decide = (id: string, proposal: string, decision: 'accept' | 'decline', reason?: string) =>
  send<Case>(`/api/cases/${encodeURIComponent(id)}/proposals/${encodeURIComponent(proposal)}/${decision}`, json('POST', reason ? { reason } : {}));
export const composedCase = (id: string) => send<{ text: string }>(`/api/cases/${encodeURIComponent(id)}/composed`).then((b) => b.text);

/** The classes an envelope holds, in the envelope's order: an exhibit states all of them. */
export function classesOf(reading: Reading, ids?: (number | string)[] | null): Cls[] {
  if (ids?.length) {
    const holding = reading.sections.filter((s) => containsIds(s.data, ids));
    if (holding.length) return [...new Set(holding.map((s) => s.class))];
  }
  return [...new Set(reading.sections.map((s) => s.class))];
}

/** Whether every cited identity appears as a RecordId somewhere in a payload. */
export function containsIds(value: unknown, ids: (number | string)[]): boolean {
  const found = new Set<string>();
  const walk = (node: unknown) => {
    if (Array.isArray(node)) { node.forEach(walk); return; }
    if (node && typeof node === 'object') {
      const record = node as Record<string, unknown>;
      if ('RecordId' in record) found.add(String(record.RecordId));
      Object.values(record).forEach(walk);
    }
  };
  walk(value);
  return ids.every((id) => found.has(String(id)));
}

/** Records with these identities, wherever the envelope holds them. */
export function recordsIn(reading: Reading, ids: (number | string)[]): EventRecord[] {
  const wanted = new Set(ids.map(String));
  const out = new Map<string, EventRecord>();
  const walk = (node: unknown) => {
    if (Array.isArray(node)) { node.forEach(walk); return; }
    if (node && typeof node === 'object') {
      const record = node as Record<string, unknown>;
      if ('RecordId' in record && wanted.has(String(record.RecordId)) && typeof record.TimeCreated === 'string' && !out.has(String(record.RecordId))) {
        out.set(String(record.RecordId), record as unknown as EventRecord);
      }
      Object.values(record).forEach(walk);
    }
  };
  walk(reading.sections.map((s) => s.data));
  return [...out.values()];
}
