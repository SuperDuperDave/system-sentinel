/**
 * What a reading-driven view is made of: a title with its controls, a section header that says
 * which class the data is and carries its basis, a key-value list, rows that inspect in place,
 * and a tree that renders a section's payload without knowing its fields.
 *
 * The class label is the point of this file. A view that shows what Windows said beside what the
 * tool computed has to keep the two apart, and the envelope already decides which is which; the
 * header only reads it off. A derived section keeps its basis one tap away; an inferred section
 * shows it, because a lead without its rule reads as a verdict.
 *
 * Two ways to render a payload, and the difference is deliberate. Where a view shapes a handful of
 * values it names them itself and gives them units. Where the tree renders whatever the machine
 * returned, it shows the machine's own field name in the readout face: that is the same vocabulary
 * the JSON hands an agent, and it cannot fall out of step with a field the reading adds.
 */
import { Fragment, ReactNode, useState } from 'react';
import { Cls, Reading, section } from './api';
import styles from './Sections.module.css';

/**
 * One section of a reading whose sections differ in shape. `section` in api.ts types every section
 * of a reading alike, which is right for a reading that returns one kind of record and wrong for
 * one that returns a bucket array, a signature list and a status object.
 */
/** A section's data by name, typed by the caller: the views hold an untyped envelope and name what they expect. One implementation, in api.ts. */
export function part<T>(reading: Reading | null | undefined, name: string): T | null {
  return section<T>(reading as Reading<T> | null | undefined, name);
}

/** The basis sentence a derived or inferred section carries, when it carries one. */
export function basisOf(reading: Reading | null | undefined, name: string): string | null {
  return reading?.sections.find((s) => s.name === name)?.basis ?? null;
}

/** The view's title, with its controls beside it on a wide screen and under it on a phone. */
export function Head({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className={styles.head}>
      <h1 className={`${styles.title} display`}>{title}</h1>
      {children ? <div className={styles.headControls}>{children}</div> : null}
    </div>
  );
}

/**
 * One section of a reading: its header, its content, and its rule.
 *
 * Where the rule goes is the whole reason this wraps the header rather than living in it. A
 * computed section keeps its basis one tap away under the header, where it is a control. A lead
 * carries its basis in the open and *after* what it claims, because a rule printed before the
 * finding reads as preamble and a rule printed after it reads as the reason.
 */
export function Section({
  title,
  cls,
  basis,
  note,
  controls,
  children,
}: {
  title?: string;
  cls: Cls;
  basis?: string | null;
  note?: ReactNode;
  controls?: ReactNode;
  children?: ReactNode;
}) {
  const lead = cls === 'inferred' && basis ? basis : null;
  return (
    <div>
      <SectionHead title={title} cls={cls} note={note} basis={lead ? null : basis}>
        {controls}
      </SectionHead>
      {children}
      {lead ? <Basis text={lead} shown /> : null}
    </div>
  );
}

/**
 * A section header on its own: what this part of the reading is, what class of evidence it holds,
 * and its basis one tap away. `children` is the section's own affordance, such as a control or a
 * hand-off to the stack.
 */
export function SectionHead({
  title,
  cls,
  basis,
  note,
  children,
}: {
  /** Left out where the reading's own section name is its class, so the label does not say it twice. */
  title?: string;
  cls: Cls;
  basis?: string | null;
  note?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className={styles.sectionHead}>
      <div className={styles.sectionLine}>
        {title ? <h2 className={styles.sectionTitle}>{title}</h2> : null}
        <span className={`${styles.cls} label`}>{cls}</span>
        {children ? <div className={styles.sectionControls}>{children}</div> : null}
      </div>
      {note ? <p className={`${styles.note} readout`}>{note}</p> : null}
      {basis ? <Basis text={basis} /> : null}
    </div>
  );
}

/** The rule that produced a section: in the open for a lead, one tap away for anything computed. */
export function Basis({ text, shown = false }: { text: string; shown?: boolean }) {
  const [open, setOpen] = useState(shown);
  if (shown) return <p className={styles.basis}>{text}</p>;
  return (
    <p className={styles.basisLine}>
      <button className={styles.basisButton} onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        {open ? 'Hide basis' : 'Basis'}
      </button>
      {open ? <span className={styles.basis}>{text}</span> : null}
    </p>
  );
}

/** A key-value list that stacks at phone width. The keys are the view's own words. */
export function Facts({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className={styles.facts}>
      {rows.map(([key, value]) => (
        <Fragment key={key}>
          <dt className="label">{key}</dt>
          <dd className="readout">{value}</dd>
        </Fragment>
      ))}
    </dl>
  );
}

/**
 * Rows that inspect in place: the list is the glance, the open row is the detail, and nothing
 * moves. `layout` is the caller's grid class, so a view decides its own columns.
 *
 * By default a row is identified by its place in the list, because two rows can be identical in
 * every field the machine returned — two USB hubs on one driver, dated the same day — and
 * identifying those by content would open both at one tap. The list is replaced whole on every
 * take, so a position is stable for exactly as long as the reading is. Pass `idOf` where the
 * payload does carry a unique key and an open row should survive a retake.
 */
export function RowList<T>({
  items,
  idOf,
  layout,
  cells,
  inspect,
}: {
  items: T[];
  idOf?: (item: T) => string | number;
  layout?: string;
  cells: (item: T) => ReactNode;
  inspect?: (item: T) => ReactNode;
}) {
  const [open, setOpen] = useState<string | number | null>(null);
  return (
    <ol className={styles.rows}>
      {items.map((item, position) => {
        const index = idOf ? idOf(item) : position;
        const isOpen = open === index;
        return (
          <li key={index} className={`${styles.row} ${isOpen ? styles.rowOpen : ''}`}>
            {inspect ? (
              <button
                className={`${styles.rowBody} ${styles.rowButton} ${layout ?? ''}`}
                onClick={() => setOpen(isOpen ? null : index)}
                aria-expanded={isOpen}
              >
                {cells(item)}
              </button>
            ) : (
              <div className={`${styles.rowBody} ${layout ?? ''}`}>{cells(item)}</div>
            )}
            {inspect && isOpen ? <div className={styles.inspect}>{inspect(item)}</div> : null}
          </li>
        );
      })}
    </ol>
  );
}

/** A choice between a few named values, as the Record view offers one. */
export function Segmented<V extends string | number>({
  value,
  onChange,
  options,
  label,
}: {
  value: V;
  onChange: (v: V) => void;
  options: { value: V; label: string }[];
  label: string;
}) {
  return (
    <div className={styles.segmented} role="group" aria-label={label}>
      {options.map((o) => (
        <button
          key={String(o.value)}
          className={`${styles.segment} ${o.value === value ? styles.segmentOn : ''}`}
          onClick={() => onChange(o.value)}
          aria-pressed={o.value === value}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// The tree: a section's payload, field for field
// ---------------------------------------------------------------------------

const PLACEHOLDER = /^<[a-z_]+>$/;

/** One value as the machine returned it. Absent, empty and removed are three different things. */
export function Value({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span className={styles.absent}>not reported</span>;
  if (typeof value === 'boolean') return <span className="readout">{value ? 'yes' : 'no'}</span>;
  if (typeof value === 'number') return <span className="readout">{value.toLocaleString()}</span>;
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className={styles.absent}>none</span>;
    return <span className={`readout ${styles.wrap}`}>{value.map((v) => String(v)).join(' · ')}</span>;
  }
  const text = String(value);
  if (text === '') return <span className={styles.absent}>empty</span>;
  if (PLACEHOLDER.test(text)) {
    return (
      <span className={`readout ${styles.placeholder}`} title="the machine has a value here; the tool's redaction removed it">
        {text}
      </span>
    );
  }
  return <span className={`readout ${styles.wrap}`}>{text}</span>;
}

const scalar = (v: unknown): boolean => v === null || typeof v !== 'object' || (Array.isArray(v) && v.every((x) => x === null || typeof x !== 'object'));

/** The name a list entry answers to, when it has one; otherwise its place in the list. */
function entryName(entry: unknown, index: number): string {
  if (entry && typeof entry === 'object' && !Array.isArray(entry)) {
    const record = entry as Record<string, unknown>;
    for (const key of ['name', 'friendly_name', 'device_name', 'interface_description', 'drive_letter', 'device_id', 'id']) {
      const value = record[key];
      if (typeof value === 'string' && value.trim()) return value.trim();
    }
  }
  return `#${index + 1}`;
}

/** A section's payload rendered field for field: the machine's own names, nested as it nested them. */
export function Tree({ value }: { value: unknown }) {
  if (value === null || value === undefined || typeof value !== 'object') return <Value value={value} />;

  if (Array.isArray(value)) {
    if (value.length === 0) return <span className={styles.absent}>none</span>;
    if (scalar(value)) return <Value value={value} />;
    return (
      <ol className={styles.entries}>
        {value.map((entry, i) => (
          <li key={i} className={styles.entry}>
            <p className={`${styles.entryName} readout`}>{entryName(entry, i)}</p>
            <Tree value={entry} />
          </li>
        ))}
      </ol>
    );
  }

  const fields = Object.entries(value as Record<string, unknown>);
  if (fields.length === 0) return <span className={styles.absent}>nothing</span>;
  return (
    <dl className={styles.tree}>
      {fields.map(([key, v]) =>
        scalar(v) ? (
          <Fragment key={key}>
            <dt className={`${styles.key} readout`}>{key}</dt>
            <dd className={styles.value}>
              <Value value={v} />
            </dd>
          </Fragment>
        ) : (
          <Fragment key={key}>
            <dt className={`${styles.key} ${styles.branchKey} readout`}>{key}</dt>
            <dd className={styles.branchBody}>
              <Tree value={v} />
            </dd>
          </Fragment>
        ),
      )}
    </dl>
  );
}

// ---------------------------------------------------------------------------
// Units and moments
// ---------------------------------------------------------------------------

const KB = 1024;
const UNITS = ['bytes', 'KB', 'MB', 'GB', 'TB'];

/** A byte count for a glance, over 1024. The exact number stays available on inspect. */
export function size(bytes: number): string {
  let n = bytes;
  let unit = 0;
  while (n >= KB && unit < UNITS.length - 1) {
    n /= KB;
    unit += 1;
  }
  return `${unit === 0 ? n : n.toFixed(n < 10 ? 2 : n < 100 ? 1 : 0)} ${UNITS[unit]}`;
}

/** A span of seconds as days, hours and minutes: how long the machine has been up. */
export function duration(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return d ? `${d} d ${h} h` : h ? `${h} h ${m} min` : `${m} min`;
}

export const day = new Intl.DateTimeFormat(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
export const shortDay = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short' });

/** How long ago, in the largest unit that still says something. */
export function ago(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  const s = Math.max(0, (Date.now() - then) / 1000);
  if (s < 90) return 'just now';
  const m = s / 60;
  if (m < 90) return `${Math.round(m)} min ago`;
  const h = m / 60;
  if (h < 36) return `${Math.round(h)} h ago`;
  const days = h / 24;
  if (days < 45) return `${Math.round(days)} days ago`;
  return `${Math.round(days / 30.44)} months ago`;
}

/** Group items into runs of one day, keeping the order they arrived in. */
export function byDay<T>(items: T[], at: (item: T) => string): [string, T[]][] {
  const out: [string, T[]][] = [];
  for (const item of items) {
    const stamp = new Date(at(item));
    const label = Number.isNaN(stamp.getTime()) ? 'undated' : day.format(stamp);
    const last = out[out.length - 1];
    if (last && last[0] === label) last[1].push(item);
    else out.push([label, [item]]);
  }
  return out;
}
