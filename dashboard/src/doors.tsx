/**
 * The doors' readings, taken once for the whole shell. The home, the rail and each situation read
 * the same envelopes, so a door's fact and the evidence behind it can never disagree: retaking
 * one retakes the other.
 */
import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { Unauthorized } from './api';
import { heldOver, knownOf, knownOfReading, knownWord } from './Marks';
import { DOORS, DoorFact, DoorId, agentFact, doorForView, factFor } from './situations';
import { getStack, StackState } from './stack';
import { useApp } from './store';
import { Taken, useReading } from './useReading';

const SEEN_KEY = 'sentinel.doors.seen.v1';

/**
 * When this person last opened each door, as the newest record it pointed at then. A per-browser
 * convenience, like a remembered filter. When storage is missing or blocked the acknowledgement
 * lasts only as long as this page, and every door with a record is marked again after a reload.
 */
function readSeen(): Partial<Record<DoorId, string>> {
  try {
    const stored: unknown = JSON.parse(localStorage.getItem(SEEN_KEY) ?? '{}');
    if (!stored || typeof stored !== 'object' || Array.isArray(stored)) return {};
    return Object.fromEntries(Object.entries(stored).filter(([, at]) => typeof at === 'string' && Number.isFinite(Date.parse(at)))) as Partial<Record<DoorId, string>>;
  } catch { return {}; }
}
function writeSeen(seen: Partial<Record<DoorId, string>>) {
  try { localStorage.setItem(SEEN_KEY, JSON.stringify(seen)); } catch { /* acknowledged for this page only */ }
}
const CLOCK = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', hour12: false });
const newer = (at: string, seen: string | undefined) => !seen || Date.parse(at) > Date.parse(seen);


interface Doors {
  facts: Record<DoorId, DoorFact>;
  taken: Partial<Record<DoorId, Taken<unknown>>>;
  /** What was already acknowledged when the person arrived at this door, so a page can say what was new. */
  seenOnArrival: Partial<Record<DoorId, string | null>>;
  /** The oldest answer among the doors that answered: the home says when it was read as a whole. */
  readAt: string | null;
  busy: boolean;
  retakeAll: () => void;
}

const DoorsContext = createContext<Doors | null>(null);

const HOLD = { hold: 'same-params' as const };

export function DoorsProvider({ children }: { children: ReactNode }) {
  const [stopped, programs, slow, disk, hardware] = DOORS.slice(0, 5).map((door) => door.reading!);
  const takenStopped = useReading(stopped.name, stopped.params, true, HOLD);
  const takenPrograms = useReading(programs.name, programs.params, true, HOLD);
  const takenSlow = useReading(slow.name, slow.params, true, HOLD);
  const takenDisk = useReading(disk.name, disk.params, true, HOLD);
  const takenHardware = useReading(hardware.name, hardware.params, true, HOLD);
  const view = useApp((s) => s.view);
  const setSession = useApp((s) => s.setSession);
  const [seen, setSeen] = useState<Partial<Record<DoorId, string>>>(readSeen);
  const [seenOnArrival, setSeenOnArrival] = useState<Partial<Record<DoorId, string | null>>>({});
  const [stack, setStack] = useState<StackState | null>(null);
  const [stackProblem, setStackProblem] = useState<string | null>(null);

  // Another tab acknowledged a door: take its record rather than overwrite it with a stale copy.
  useEffect(() => {
    const onStorage = (event: StorageEvent) => { if (event.key === SEEN_KEY) setSeen(readSeen()); };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);

  // The handoff is local state, not a machine reading: cheap to ask whenever the person moves.
  useEffect(() => {
    let active = true;
    getStack()
      .then((state) => { if (active) { setStack(state); setStackProblem(null); } })
      .catch((error: unknown) => {
        if (!active) return;
        if (error instanceof Unauthorized) { setSession('closed'); return; }
        setStackProblem(error instanceof Error ? error.message : String(error));
      });
    return () => { active = false; };
  }, [view, setSession]);

  const all = useMemo(() => [takenStopped, takenPrograms, takenSlow, takenDisk, takenHardware], [takenStopped, takenPrograms, takenSlow, takenDisk, takenHardware]);
  const retakeAll = useCallback(() => { for (const t of all) if (t.state !== 'taking') t.retake(); }, [all]);

  const raw = useMemo(() => {
    const ids: DoorId[] = ['stopped', 'programs', 'slow', 'disk', 'hardware'];
    const facts = Object.fromEntries(ids.map((id, index) => {
      const t = all[index];
      const known = knownOf(t);
      if (!heldOver(t)) return [id, factFor(id, t.reading, known)];
      // The latest take was a hole: keep the earlier evidence visible, say it is held, and let the
      // outcome be the hole's, so a failed retake never reads as a current observation.
      const held = factFor(id, t.reading, knownOfReading(t.reading));
      return [id, { ...held, known, exact: `Latest take: ${knownWord(known).toLowerCase()}. Showing what was read at ${CLOCK.format(Date.parse(t.reading!.asked_at))}.` }];
    })) as Record<DoorId, DoorFact>;
    facts.agent = agentFact(stack, stackProblem);
    return facts;
  }, [all, stack, stackProblem]);

  // Opening a door acknowledges what it points at. The value before this visit is kept for the
  // page, so it can still say which records were new when the person arrived.
  const open = doorForView(view);
  const newestHere = open ? raw[open.id].newest : null;
  useEffect(() => { setSeenOnArrival({}); }, [view]);
  useEffect(() => {
    if (!open || !newestHere) return;
    const id = open.id;
    setSeenOnArrival((before) => (id in before ? before : { ...before, [id]: seen[id] ?? null }));
    if (!newer(newestHere, seen[id])) return;
    const next = { ...readSeen(), ...seen, [id]: newestHere };
    writeSeen(next);
    setSeen(next);
  }, [open, newestHere, seen]);

  const value = useMemo<Doors>(() => {
    const facts = Object.fromEntries(Object.entries(raw).map(([id, fact]) => [id, {
      ...fact,
      attention: fact.attention && fact.newest && newer(fact.newest, seen[id as DoorId]) ? fact.attention : null,
    }])) as Record<DoorId, DoorFact>;
    const ids: DoorId[] = ['stopped', 'programs', 'slow', 'disk', 'hardware'];
    const answered = all.map((t) => t.reading?.asked_at).filter((at): at is string => !!at);
    return {
      facts,
      taken: Object.fromEntries(ids.map((id, index) => [id, all[index]])),
      readAt: answered.length ? answered.reduce((a, b) => (Date.parse(a) < Date.parse(b) ? a : b)) : null,
      busy: all.some((t) => t.state === 'taking'),
      retakeAll,
      seenOnArrival,
    };
  }, [all, raw, seen, seenOnArrival, retakeAll]);

  return <DoorsContext.Provider value={value}>{children}</DoorsContext.Provider>;
}

export function useDoors(): Doors {
  const doors = useContext(DoorsContext);
  if (!doors) throw new Error('useDoors outside DoorsProvider');
  return doors;
}
