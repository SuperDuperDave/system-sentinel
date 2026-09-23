/**
 * The client of the stack, prompts and captures routes (docs/API.md, "The stack" and "Captures").
 *
 * The stack lives on the server, so every function here is a round trip and nothing is cached:
 * the desktop, the phone and the agent see one stack, and a view that holds a copy would be
 * showing a stack that may no longer exist. Each mutation returns what the server now holds.
 *
 * Adding the same evidence twice is refused by the boundary, not by the caller, so the refusal
 * arrives as its own error and a view can say "already in the stack" instead of "failed".
 */
import { Reading, Unauthorized } from './api';

export type Verbosity = 'summary' | 'full';

export interface StackItem {
  id: string;
  added_at: string;
  kind: 'reading' | 'selection' | 'note';
  title: string;
  rank: number;
  verbosity: Verbosity;
  reading: Reading | null;
  ids: (number | string)[] | null;
  note: string | null;
}

export interface StackState {
  items: StackItem[];
  prompt_id: string | null;
  system_prompt: boolean;
}

export interface Prompt {
  id: string;
  name: string;
  description: string;
  content: string;
  builtin: boolean;
}

/** What a view hands to the stack: a reading it holds, some of its records, or a note. */
export type NewItem =
  | { kind: 'reading'; title?: string; rank?: number; verbosity?: Verbosity; envelope: Reading }
  | { kind: 'reading'; title?: string; rank?: number; verbosity?: Verbosity; take: { name: string; params: Record<string, unknown> } }
  | { kind: 'selection'; title?: string; rank?: number; verbosity?: Verbosity; envelope: Reading; ids: (number | string)[] }
  | { kind: 'note'; title?: string; rank?: number; note: string };

/** The handoff as the server rendered it, with what redaction removed from it. */
export interface Composed {
  text: string;
  items: number;
  redacted: string[];
}

/** One capture on disk. The tool never deletes them. */
export interface Capture {
  name: string;
  bytes: number;
  /** File modification time. The manifest's captured_at is the original capture time when readable. */
  created_at: string;
  manifest?:
    | { status: 'read'; captured_at: string; unredacted: boolean; readings: number; outcomes: Record<string, number>; unavailable?: string[] }
    | { status: 'missing' | 'unreadable' | 'limit' };
}

/** The boundary refused this evidence because the stack already holds it. */
export class Duplicate extends Error {
  readonly id: string;
  constructor(id: string) {
    super('already in the stack');
    this.id = id;
  }
}

async function send<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    credentials: 'same-origin',
    headers: init?.body ? { 'Content-Type': 'application/json', ...(init?.headers ?? {}) } : init?.headers,
  });
  if (res.status === 401) throw new Unauthorized();
  if (res.status === 409) {
    const body = await res.json().catch(() => ({}));
    throw new Duplicate(String(body.id ?? ''));
  }
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

const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });

export const getStack = (): Promise<StackState> => send<StackState>('/api/stack');

/** Add evidence. The server takes the reading when the item carries a `take`, and stores the envelope as given when it carries one. */
export const addItem = (item: NewItem): Promise<StackItem> => send<StackItem>('/api/stack/items', json('POST', item));

/** Change one item's place in the handoff, how much of it is rendered, or what it is called. */
export const patchItem = (id: string, change: { rank?: number; verbosity?: Verbosity; title?: string }): Promise<StackItem> =>
  send<StackItem>(`/api/stack/items/${id}`, json('PATCH', change));

export const removeItem = (id: string): Promise<StackState> => send<StackState>(`/api/stack/items/${id}`, { method: 'DELETE' });

export const clearStack = (): Promise<StackState> => send<StackState>('/api/stack', { method: 'DELETE' });

/** Choose the prompt that leads the handoff, or whether one does at all. */
export const patchStack = (change: { prompt_id?: string | null; system_prompt?: boolean }): Promise<StackState> =>
  send<StackState>('/api/stack', json('PATCH', change));

export const composed = (): Promise<Composed> => send<Composed>('/api/stack/composed');

export const getPrompts = (): Promise<Prompt[]> => send<{ prompts: Prompt[] }>('/api/prompts').then((b) => b.prompts);

export const addPrompt = (prompt: { name: string; description?: string; content?: string }): Promise<Prompt> =>
  send<Prompt>('/api/prompts', json('POST', prompt));

export const patchPrompt = (id: string, change: { name?: string; description?: string; content?: string }): Promise<Prompt> =>
  send<Prompt>(`/api/prompts/${id}`, json('PATCH', change));

export const removePrompt = (id: string): Promise<Prompt[]> =>
  send<{ prompts: Prompt[] }>(`/api/prompts/${id}`, { method: 'DELETE' }).then((b) => b.prompts);

export const getCaptures = (): Promise<Capture[]> => send<{ captures: Capture[] }>('/api/captures').then((b) => b.captures);

/**
 * Take readings that need no exact selection and hand back the ZIP. Heavy readings are included,
 * so the caller shows that it is running.
 * The file is returned, not saved: nothing leaves the machine unless the person sends it.
 */
export async function createCapture(): Promise<{ name: string; blob: Blob }> {
  const res = await fetch('/api/captures', { method: 'POST', credentials: 'same-origin' });
  if (res.status === 401) throw new Unauthorized();
  if (!res.ok) throw new Error(`${res.status}: ${res.statusText}`);
  const name = res.headers.get('X-Capture-Name') ?? 'capture.zip';
  return { name, blob: await res.blob() };
}

/** Hand a file to the browser's own download, from a blob the tool already holds. */
export function saveBlob(name: string, blob: Blob): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = name;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}
