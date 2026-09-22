import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { Unauthorized } from '../api';
import { CopyButton } from '../Copy';
import { Head, Segmented, ago, size } from '../Sections';
import { useApp } from '../store';
import {
  Capture,
  Composed,
  Prompt,
  StackItem,
  StackState,
  Verbosity,
  addPrompt,
  clearStack,
  composed as composedText,
  createCapture,
  getCaptures,
  getPrompts,
  getStack,
  patchItem,
  patchPrompt,
  patchStack,
  removeItem,
  removePrompt,
  saveBlob,
} from '../stack';
import styles from './Stack.module.css';

/**
 * The stack: the evidence chosen for handoff, the prompt that leads it, and the text they
 * compose into.
 *
 * It lives on the server, so this view holds no copy of it: every change is a round trip and
 * what comes back is what a phone or an agent would see. The handoff below is the same text the
 * agent reads from the route — the clipboard is the person's way to it, not a second version of
 * it. Each item keeps the envelope it was added with, so an item whose reading never observed
 * the machine says so here and says so in the composed text.
 */
export function Stack() {
  const [stack, setStack] = useState<StackState | null>(null);
  const [prompts, setPrompts] = useState<Prompt[]>([]);
  const [handoff, setHandoff] = useState<Composed | null>(null);
  const [captures, setCaptures] = useState<Capture[]>([]);
  const [problem, setProblem] = useState<string | null>(null);
  const setSession = useApp((s) => s.setSession);

  const guard = useCallback(
    async (work: () => Promise<void>) => {
      try {
        setProblem(null);
        await work();
      } catch (err: unknown) {
        if (err instanceof Unauthorized) return setSession('closed');
        setProblem(err instanceof Error ? err.message : String(err));
      }
    },
    [setSession],
  );

  /** The stack and the text it composes into always arrive together: one is derived from the other. */
  const refresh = useCallback(
    () =>
      guard(async () => {
        const [state, text] = await Promise.all([getStack(), composedText()]);
        setStack(state);
        setHandoff(text);
      }),
    [guard],
  );

  const refreshPrompts = useCallback(() => guard(async () => setPrompts(await getPrompts())), [guard]);
  const refreshCaptures = useCallback(() => guard(async () => setCaptures(await getCaptures())), [guard]);

  useEffect(() => {
    void refresh();
    void refreshPrompts();
    void refreshCaptures();
  }, [refresh, refreshPrompts, refreshCaptures]);

  const change = (id: string, c: { rank?: number; verbosity?: Verbosity }) => guard(async () => { await patchItem(id, c); await refresh(); });
  const drop = (id: string) => guard(async () => { await removeItem(id); await refresh(); });
  const choose = (change_: { prompt_id?: string | null; system_prompt?: boolean }) => guard(async () => { await patchStack(change_); await refresh(); });

  const items = stack?.items ?? [];
  const leading = prompts.find((p) => p.id === stack?.prompt_id) ?? null;

  return (
    <section>
      <Head title="Stack" />
      <p className={`${styles.state} readout`}>
        {items.length === 0 ? 'Nothing on the stack' : `${items.length} ${items.length === 1 ? 'item' : 'items'}`}
        {stack?.system_prompt && leading ? ` · led by ${leading.name}` : ' · no prompt'}
      </p>
      {problem ? <p className={`${styles.problem} readout`} role="status">{problem}</p> : null}

      <div className={styles.block}>
        <h2 className={`${styles.blockTitle} label`}>Evidence</h2>
        {items.length === 0 ? (
          <p className={styles.empty}>
            Every view offers what it is showing to the stack, and a record you have open can go on its own. What is here composes the handoff below.
          </p>
        ) : (
          <>
            <ol className={styles.items}>
              {items.map((item) => (
                <Item
                  key={item.id}
                  item={item}
                  onChange={(c) => change(item.id, c)}
                  onRemove={() => drop(item.id)}
                />
              ))}
            </ol>
            <Clear onClear={() => guard(async () => { await clearStack(); await refresh(); })} />
          </>
        )}
      </div>

      <div className={styles.block}>
        <h2 className={`${styles.blockTitle} label`}>Prompt</h2>
        <label className={styles.toggle}>
          <input
            type="checkbox"
            checked={stack?.system_prompt ?? true}
            onChange={(e) => choose({ system_prompt: e.target.checked })}
          />
          <span>Lead the handoff with a prompt</span>
        </label>
        <Library
          prompts={prompts}
          chosen={stack?.prompt_id ?? null}
          onChoose={(id) => choose({ prompt_id: id })}
          onSave={(id, fields) => guard(async () => { await patchPrompt(id, fields); await refreshPrompts(); await refresh(); })}
          onAdd={(fields) => guard(async () => { await addPrompt(fields); await refreshPrompts(); })}
          onDelete={(id) => guard(async () => { await removePrompt(id); await refreshPrompts(); await refresh(); })}
        />
      </div>

      <div className={styles.block}>
        <h2 className={`${styles.blockTitle} label`}>Handoff</h2>
        <Handoff handoff={handoff} />
      </div>

      <div className={styles.block}>
        <h2 className={`${styles.blockTitle} label`}>Capture</h2>
        <Captures captures={captures} onTaken={refreshCaptures} guard={guard} />
      </div>
    </section>
  );
}

/** One item: what it is, where it came from, where it sits in the handoff and how much of it is rendered. */
function Item({ item, onChange, onRemove }: { item: StackItem; onChange: (c: { rank?: number; verbosity?: Verbosity }) => void; onRemove: () => void }) {
  const envelope = item.reading;
  const lost = envelope ? envelope.outcome !== 'ok' && envelope.outcome !== 'empty' : false;
  return (
    <li className={styles.item}>
      <p className={styles.itemTitle}>{item.title}</p>
      <p className={`${styles.itemMeta} readout`}>
        {item.kind}
        {item.kind === 'selection' ? ` · ${item.ids?.length ?? 0} records` : ''}
        {envelope ? ` · ${envelope.reading}` : ''}
        {envelope ? <span className={lost ? styles.lost : styles.fine}> · {lost ? `not observed: ${envelope.outcome}` : envelope.outcome}</span> : null}
        {` · added ${ago(item.added_at)}`}
      </p>
      <div className={styles.itemControls}>
        <span className={styles.rank}>
          <span className={`${styles.rankLabel} label`}>rank</span>
          <button className={styles.step} onClick={() => onChange({ rank: item.rank - 1 })} disabled={item.rank <= 1} aria-label="Earlier in the handoff">−</button>
          <span className={`${styles.rankValue} readout`}>{item.rank}</span>
          <button className={styles.step} onClick={() => onChange({ rank: item.rank + 1 })} disabled={item.rank >= 5} aria-label="Later in the handoff">+</button>
        </span>
        {item.kind === 'note' ? null : (
          <Segmented
            value={item.verbosity}
            onChange={(v) => onChange({ verbosity: v })}
            options={[
              { value: 'summary' as Verbosity, label: 'summary' },
              { value: 'full' as Verbosity, label: 'full' },
            ]}
            label="How much of this item the handoff carries"
          />
        )}
        <button className={styles.remove} onClick={onRemove}>Remove</button>
      </div>
    </li>
  );
}

/** Clearing the stack throws evidence away, so it asks once, in place. */
function Clear({ onClear }: { onClear: () => void }) {
  const [asking, setAsking] = useState(false);
  if (!asking) return <button className={styles.quiet} onClick={() => setAsking(true)}>Clear the stack</button>;
  return (
    <p className={styles.confirm}>
      <span className="readout">Remove every item?</span>
      <button className={styles.remove} onClick={() => { setAsking(false); onClear(); }}>Clear</button>
      <button className={styles.quiet} onClick={() => setAsking(false)}>Keep</button>
    </p>
  );
}

interface Fields {
  name: string;
  description: string;
  content: string;
}

/** The library: which prompt leads the handoff, and the text of each one, editable where it sits. */
function Library({
  prompts,
  chosen,
  onChoose,
  onSave,
  onAdd,
  onDelete,
}: {
  prompts: Prompt[];
  chosen: string | null;
  onChoose: (id: string) => void;
  onSave: (id: string, fields: Fields) => void;
  onAdd: (fields: Fields) => void;
  onDelete: (id: string) => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const open = (id: string | null) => { setEditing(id); setAdding(false); };

  return (
    <>
      <ul className={styles.prompts}>
        {prompts.map((prompt) =>
          editing === prompt.id ? (
            <li key={prompt.id} className={styles.prompt}>
              <PromptForm
                initial={prompt}
                onCancel={() => open(null)}
                onSave={(fields) => { open(null); onSave(prompt.id, fields); }}
              />
            </li>
          ) : (
            <li key={prompt.id} className={`${styles.prompt} ${chosen === prompt.id ? styles.promptChosen : ''}`}>
              <button className={styles.promptChoose} onClick={() => onChoose(prompt.id)} aria-pressed={chosen === prompt.id}>
                <span className={styles.promptName}>{prompt.name}</span>
                <span className={styles.promptWhat}>{prompt.description}</span>
              </button>
              <div className={styles.promptActions}>
                <button className={styles.quiet} onClick={() => open(prompt.id)}>Edit</button>
                <Delete onDelete={() => onDelete(prompt.id)} />
              </div>
            </li>
          ),
        )}
      </ul>
      {adding ? (
        <PromptForm
          initial={{ name: '', description: '', content: '' }}
          onCancel={() => setAdding(false)}
          onSave={(fields) => { setAdding(false); onAdd(fields); }}
        />
      ) : (
        <button className={styles.quiet} onClick={() => { setAdding(true); setEditing(null); }}>Add a prompt</button>
      )}
    </>
  );
}

function Delete({ onDelete }: { onDelete: () => void }) {
  const [asking, setAsking] = useState(false);
  if (!asking) return <button className={styles.quiet} onClick={() => setAsking(true)}>Delete</button>;
  return (
    <>
      <button className={styles.remove} onClick={() => { setAsking(false); onDelete(); }}>Delete it</button>
      <button className={styles.quiet} onClick={() => setAsking(false)}>Keep</button>
    </>
  );
}

function PromptForm({ initial, onSave, onCancel }: { initial: Fields; onSave: (f: Fields) => void; onCancel: () => void }) {
  const id = useId();
  const [fields, setFields] = useState<Fields>({ name: initial.name, description: initial.description, content: initial.content });
  const set = (key: keyof Fields) => (e: { target: { value: string } }) => setFields((f) => ({ ...f, [key]: e.target.value }));
  const nameField = useRef<HTMLInputElement>(null);

  // The form appears where a button was, so the focus that button held has to go somewhere.
  useEffect(() => { nameField.current?.focus(); }, []);

  return (
    <form
      className={styles.form}
      onSubmit={(e) => { e.preventDefault(); onSave(fields); }}
    >
      <label className="label" htmlFor={`${id}-name`}>Name</label>
      <input id={`${id}-name`} className={styles.input} ref={nameField} value={fields.name} onChange={set('name')} />
      <label className="label" htmlFor={`${id}-what`}>What it is for</label>
      <input id={`${id}-what`} className={styles.input} value={fields.description} onChange={set('description')} />
      <label className="label" htmlFor={`${id}-content`}>The prompt</label>
      <textarea id={`${id}-content`} className={`${styles.input} ${styles.textarea} readout`} value={fields.content} onChange={set('content')} rows={10} />
      <div className={styles.formActions}>
        <button className={styles.save} type="submit" disabled={!fields.name.trim()}>Save</button>
        <button className={styles.quiet} type="button" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

/** The composed text, as the agent reads it: copy or save the same returned handoff. */
function Handoff({ handoff }: { handoff: Composed | null }) {
  const preview = useRef<HTMLPreElement>(null);
  if (!handoff) return <p className={`${styles.empty} readout`}>Composing…</p>;
  return (
    <>
      <p className={`${styles.handoffLine} readout`}>
        {handoff.items} {handoff.items === 1 ? 'item' : 'items'} · {handoff.text.length.toLocaleString()} characters
        {handoff.redacted.length ? ` · redacted: ${handoff.redacted.join(', ')}` : ''}
        <span className={styles.spacer} />
        <CopyButton text={handoff.text} selectRef={preview} />
        <button className={`${styles.action} ${styles.saveHandoff}`} onClick={() => saveBlob('system-sentinel-handoff.md', new Blob([handoff.text], { type: 'text/markdown;charset=utf-8' }))}>Download handoff</button>
      </p>
      <pre className={`${styles.preview} readout`} ref={preview} tabIndex={0} role="region" aria-label="The composed handoff">{handoff.text}</pre>
    </>
  );
}

/** A capture is every reading at one moment, written to disk. It is the slowest thing here. */
function Captures({ captures, onTaken, guard }: { captures: Capture[]; onTaken: () => void; guard: (work: () => Promise<void>) => Promise<void> }) {
  const [taking, setTaking] = useState(false);
  return (
    <>
      <p className={styles.what}>
        Every reading in the catalog, taken now and written into the data directory as one ZIP: an envelope for each, the stack, and the handoff.
        It takes as long as the slowest query on this machine. Nothing is sent anywhere.
      </p>
      <p className={styles.handoffLine}>
        <button
          className={styles.action}
          disabled={taking}
          onClick={() =>
            guard(async () => {
              setTaking(true);
              try {
                const made = await createCapture();
                saveBlob(made.name, made.blob);
                onTaken();
              } finally {
                setTaking(false);
              }
            })
          }
        >
          {taking ? 'Taking every reading…' : 'Capture'}
        </button>
      </p>
      {captures.length ? (
        <ul className={styles.captures}>
          {captures.map((c) => (
            <li key={c.name} className={styles.capture}>
              <a className={`${styles.captureName} readout`} href={`/api/captures/${c.name}`} download>{c.name}</a>
              <span className={`${styles.captureMeta} readout`}>{size(c.bytes)} · {ago(c.created_at)}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className={`${styles.empty} readout`}>No capture on disk.</p>
      )}
    </>
  );
}
