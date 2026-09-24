/**
 * The client of the one boundary. Every reading arrives as the envelope docs/API.md describes;
 * nothing here interprets it. A 401 anywhere means the session is closed.
 */

export type Outcome = 'ok' | 'empty' | 'failed' | 'unavailable' | 'denied' | 'timeout';
export type Cls = 'raw' | 'derived' | 'invariant' | 'inferred';
/** Event-log record identity. Values beyond JavaScript's safe integer range arrive as exact decimal strings. */
export type RecordId = number | string;

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
  /** One query, several, or (for an inferred reading) the readings it drew on and what each answered. */
  method: { kind: string; query?: string; queries?: string[]; readings?: unknown; source?: string };
  count: number | null;
  sections: Section<T>[];
  error: { kind: string; detail: string } | null;
  warnings: string[];
  redacted: string[];
}

/** One record from a Windows log, as the events and record readings return it. */
export interface EventRecord {
  RecordId: RecordId;
  /** Present when a reading combines records from several Windows logs. */
  Log?: string;
  Id: number;
  /** Added to newer readings so a positional property layout can be identified explicitly. */
  ProviderId?: string | null;
  Version?: number | null;
  Level: number;
  LevelDisplayName?: string;
  ProviderName: string;
  MachineName: string;
  TaskDisplayName: string | null;
  TimeCreated: string;
  Message: string | null;
  /** Exact rows include Properties; bounded WHEA previews leave them for whea_record. */
  Properties?: unknown[] | null;
  MessageChars?: number | null;
  HeaderHex?: string | null;
  PayloadBytes?: number | null;
}

export interface CatalogEntry {
  name: string;
  description: string;
  classes: Cls[];
  params: { name: string; type: string; default: unknown; description: string; choices?: unknown[] }[];
  private: string[];
  heavy: boolean;
}

/** The catalog as the route returns it: every reading, and the version of the tool answering. */
export interface Catalog {
  readings: CatalogEntry[];
  version: string;
}

export interface PerformanceCollection {
  settings: { enabled: boolean; interval_seconds: number; config_error: boolean };
  last_attempt: { at: string | null; outcome: string; took_ms: number | null };
  retention_days: number;
}

/**
 * A way in for another device: what this machine publishes on a private network, and a one-time
 * link to it. `outcome` is the reading's: `ok` when there is an address, `empty` when the machine
 * answered and publishes nothing (`installed` says whether Tailscale is there at all, `port` what
 * it would have to publish), `unavailable` or `failed` when it could not be asked. `url`,
 * `expires_at` and `qr` are null unless it is `ok`. The token is never in it.
 */
export interface SignInLink {
  outcome: string;
  installed: boolean | null;
  port: number;
  address: string | null;
  via: string | null;
  detail: string;
  url: string | null;
  expires_at: string | null;
  ttl_seconds: number;
  qr: string | null;
}

export class Unauthorized extends Error {
  constructor() {
    super('unauthorized');
  }
}

export class HttpError extends Error {
  constructor(readonly status: number, detail: string) {
    super(`${status}: ${detail}`);
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
    throw new HttpError(res.status, detail);
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

export function catalog(): Promise<Catalog> {
  return request<Catalog>('/api/readings');
}

export function performanceCollection(): Promise<PerformanceCollection> {
  return request<PerformanceCollection>('/api/performance/collection');
}

export function setPerformanceCollection(enabled: boolean, interval_seconds: number): Promise<PerformanceCollection> {
  return request<PerformanceCollection>('/api/performance/collection', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled, interval_seconds }),
  });
}

export function clearPerformanceHistory(): Promise<{ cleared_files: number }> {
  return request<{ cleared_files: number }>('/api/performance/history', { method: 'DELETE' });
}

/** Exchange the token for the session cookie. Only a rejected token resolves false. */
export async function openSession(token: string): Promise<boolean> {
  try {
    await request<{ ok: boolean }>('/api/session', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: token.trim() }),
    });
    return true;
  } catch (error) {
    if (error instanceof Unauthorized) return false;
    throw error;
  }
}

/** Ask this machine for a sign-in link for another device. Each call mints a fresh code. */
export function signInLink(): Promise<SignInLink> {
  return request<SignInLink>('/api/session/link', { method: 'POST' });
}

export async function closeSession(): Promise<void> {
  await fetch('/api/session', { method: 'DELETE', credentials: 'same-origin' });
}
