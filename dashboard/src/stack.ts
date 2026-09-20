/**
 * The client of the stack, prompts and captures routes (docs/API.md, "The stack" and "Captures").
 * The shapes below are the contract; the functions are filled in phase 2 with the Stack view.
 */
import { Reading } from './api';

export type Verbosity = 'summary' | 'full';

export interface StackItem {
  id: string;
  added_at: string;
  kind: 'reading' | 'selection' | 'note';
  title: string;
  rank: number;
  verbosity: Verbosity;
  reading: Reading | null;
  ids: number[] | null;
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
  | { kind: 'selection'; title?: string; rank?: number; verbosity?: Verbosity; envelope: Reading; ids: number[] }
  | { kind: 'note'; title?: string; rank?: number; note: string };
