/**
 * The client of the one boundary. Every reading arrives as the envelope docs/API.md describes;
 * nothing here interprets it. A 401 anywhere means the session is closed.
 */

export type Outcome = 'ok' | 'empty' | 'failed' | 'unavailable' | 'denied' | 'timeout';
export type Cls = 'raw' | 'derived' | 'invariant' | 'inferred';

export interface Section<T = unknown> {
  name: string;
  class: Cls;
  data: T;
  basis?: string;
}

export interface Reading<T = unknown> {
  reading: string;
  params: Record<string, unknown>;
  asked_at: string;
  took_ms: number;
  outcome: Outcome;
  method: { kind: string; query: string };
  count: number | null;
  sections: Section<T>[];
  error: { kind: string; detail: string } | null;
  warnings: string[];
  redacted: string[];
}

/** One record from a Windows log, as the events and record readings return it. */
export interface EventRecord {
  RecordId: number;
  Id: number;
  Level: number;
  LevelDisplayName: string;
  ProviderName: string;
  MachineName: string;
  TaskDisplayName: string | null;
  TimeCreated: string;
  Message: string | null;
  Properties: unknown[] | null;
}

export interface CatalogEntry {
  name: string;
  description: string;
  classes: Cls[];
  params: { name: string; type: string; default: unknown; description: string; choices?: unknown[] }[];
  private: string[];
  heavy: boolean;
}

export class Unauthorized extends Error {
  constructor() {
    super('unauthorized');
  }
}

export const observed = (r: Reading | null | undefined): boolean => !!r && (r.outcome === 'ok' || r.outcome === 'empty');

export function section<T>(r: Reading<T> | null | undefined, name: string): T | null {
  const s = r?.sections.find((x) => x.name === name);
  return s ? (s.data as T) : null;
}

type ParamValue = string | number | boolean | number[];

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { ...init, credentials: 'same-origin' });
  if (res.status === 401) throw new Unauthorized();
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? body.error ?? detail;
    } catch {
      /* the status is the message */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return (await res.json()) as T;
}

/** Take a reading by name. Parameters become query parameters; lists are comma-joined. */
export function take<T = unknown>(name: string, params: Record<string, ParamValue> = {}): Promise<Reading<T>> {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) q.set(k, Array.isArray(v) ? v.join(',') : String(v));
  const qs = q.toString();
  return request<Reading<T>>(`/api/readings/${name}${qs ? `?${qs}` : ''}`);
}

export async function catalog(): Promise<CatalogEntry[]> {
  const body = await request<{ readings: CatalogEntry[] }>('/api/readings');
  return body.readings;
}

/** Exchange the token for the session cookie. Resolves false when the token is wrong. */
export async function openSession(token: string): Promise<boolean> {
  const res = await fetch('/api/session', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token: token.trim() }),
    credentials: 'same-origin',
  });
  return res.ok;
}

export async function closeSession(): Promise<void> {
  await fetch('/api/session', { method: 'DELETE', credentials: 'same-origin' });
}
