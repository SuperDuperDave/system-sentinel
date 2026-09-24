import { useCallback, useEffect, useRef, useState } from 'react';
import { Reading, Unauthorized, observed, take } from './api';
import { useApp } from './store';

type ParamValue = string | number | boolean | number[];
type Params = Record<string, ParamValue>;
type Options = { hold?: 'same-params' | 'same-reading' };

export interface Taken<T> {
  /** idle: not asked yet; taking: in flight; done: an envelope arrived (any outcome); lost: the request itself failed. */
  state: 'idle' | 'taking' | 'done' | 'lost';
  reading: Reading<T> | null;
  problem: string | null;
  /** The visible envelope was observed earlier, rather than by the latest take. */
  held?: boolean;
  heldParams?: Params;
  requestedParams?: Params;
  /** A later envelope did not observe the machine; the visible evidence remains held. */
  latestFailure?: Reading<T> | null;
  retake: () => void;
}

type Result<T> = Pick<Taken<T>, 'state' | 'reading' | 'problem' | 'held' | 'heldParams' | 'latestFailure'> & { key: string };
type Cached = { name: string; key: string; params: Params; reading: Reading<unknown> };

/** Small, tab-only cache for views that explicitly opt in. It never enters a URL or storage. */
const heldReadings = new Map<string, Cached>();
const MAX_HELD = 16;

export function clearHeldReadings() { heldReadings.clear(); }

/** Whether a view can draw this exact source and scope before its first paint on return. */
export function hasHeldReading(name: string, params: Params = {}): boolean {
  return heldReadings.has(JSON.stringify([name, params]));
}

/** A return is stable only when the selected row and every panel above it can draw immediately. */
export function canRestoreCrashView(stopCount: number, faultCount: number, focus: 'stop' | 'fault' | 'dump' | null, changesBefore: string | null = null): boolean {
  return hasHeldReading('crash', { count: stopCount }) &&
    (!changesBefore || hasHeldReading('changes', { before: changesBefore, hours: 168, count: 100 })) &&
    (focus === 'stop' || hasHeldReading('reliability', { days: 30 })) &&
    (focus === 'stop' || hasHeldReading('faults', { count: faultCount })) &&
    ((focus !== 'dump' && focus !== null) || hasHeldReading('dumps'));
}

function sameParamNames(left: Params, right: Params): boolean {
  const a = Object.keys(left).sort();
  const b = Object.keys(right).sort();
  return a.length === b.length && a.every((name, index) => name === b[index]);
}

function peek(name: string, key: string, params: Params, acrossParams: boolean): Cached | undefined {
  const exact = heldReadings.get(key);
  if (exact || !acrossParams) return exact;
  return [...heldReadings.values()].reverse().find((item) => item.name === name && sameParamNames(item.params, params));
}

function held(name: string, key: string, params: Params, acrossParams: boolean): Cached | undefined {
  const item = peek(name, key, params, acrossParams);
  if (item) { heldReadings.delete(item.key); heldReadings.set(item.key, item); }
  return item;
}

function remember<T>(name: string, key: string, params: Params, reading: Reading<T>) {
  heldReadings.delete(key);
  heldReadings.set(key, { name, key, params: { ...params }, reading });
  if (heldReadings.size > MAX_HELD) heldReadings.delete(heldReadings.keys().next().value!);
}

function initial<T>(key: string, cached?: Cached): Result<T> {
  return cached
    ? { key, state: cached.key === key ? 'done' : 'taking', reading: cached.reading as Reading<T>, problem: null, held: true, heldParams: cached.params, latestFailure: null }
    : { key, state: 'idle', reading: null, problem: null, held: false, latestFailure: null };
}

/** Take on entry or changed parameters unless this view holds the exact observation; retake on demand. */
export function useReading<T = unknown>(name: string, params: Params = {}, enabled = true, options: Options = {}): Taken<T> {
  const hold = options.hold !== undefined;
  const holdAcrossParams = options.hold === 'same-reading';
  const key = JSON.stringify([name, params]);
  const [result, setResult] = useState<Result<T>>(() => initial<T>(key, hold ? peek(name, key, params, holdAcrossParams) : undefined));
  const [nonce, setNonce] = useState(0);
  const force = useRef(false);
  const setSession = useApp((s) => s.setSession);

  useEffect(() => {
    if (!enabled) return;
    const forced = force.current;
    force.current = false;
    const cached = hold ? held(name, key, params, holdAcrossParams) : undefined;
    if (cached?.key === key && !forced) {
      setResult(initial<T>(key, cached));
      return;
    }
    let active = true;
    setResult((previous) => ({
      key, state: 'taking', reading: hold ? (cached?.reading as Reading<T> | undefined) ?? null : previous.key === key ? previous.reading : null,
      problem: null, held: hold && Boolean(cached), heldParams: cached?.params, latestFailure: null,
    }));
    take<T>(name, params)
      .then((r) => {
        if (!active) return;
        // A concurrent request must not repopulate the cache or reopen a closed session
        // after another reading received a 401.
        if (useApp.getState().session === 'closed') return;
        if (hold && observed(r)) remember(name, key, params, r);
        const previous = hold ? held(name, key, params, holdAcrossParams) : undefined;
        if (hold && !observed(r) && previous) {
          setResult({ key, state: 'done', reading: previous.reading as Reading<T>, problem: null, held: true, heldParams: previous.params, latestFailure: r });
        } else {
          setResult({ key, state: 'done', reading: r, problem: null, held: false, latestFailure: null });
        }
        setSession('open');
      })
      .catch((err: unknown) => {
        if (!active) return;
        if (err instanceof Unauthorized) {
          clearHeldReadings();
          setSession('closed');
          setResult(initial<T>(key));
          return;
        }
        const previous = hold ? held(name, key, params, holdAcrossParams) : undefined;
        setResult({ key, state: 'lost', reading: previous?.reading as Reading<T> | undefined ?? null,
          problem: err instanceof Error ? err.message : String(err), held: Boolean(previous), heldParams: previous?.params, latestFailure: null });
      });
    return () => { active = false; };
    // params is captured by key, deliberately.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, nonce, enabled, hold, holdAcrossParams]);

  const retake = useCallback(() => { force.current = true; setNonce((n) => n + 1); }, []);
  const visible = result.key === key ? result : (() => {
    const next = initial<T>(key, hold ? peek(name, key, params, holdAcrossParams) : undefined);
    return next.reading ? next : { ...next, state: enabled ? 'taking' as const : 'idle' as const };
  })();
  return { state: visible.state, reading: visible.reading, problem: visible.problem, held: visible.held,
    heldParams: visible.heldParams, requestedParams: params, latestFailure: visible.latestFailure, retake };
}
