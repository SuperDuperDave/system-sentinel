import { useEffect, useRef, useState } from 'react';
import { useDoors } from '../doors';
import { AttentionMark, OutcomeGlyph, isHole, type Known as KnownState } from '../Marks';
import { DOORS, Door, DoorFact } from '../situations';
import { useApp } from '../store';
import styles from './Home.module.css';

const CLOCK = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });

/**
 * Home: the doors a person arrives through. Each holds one thing Sentinel read and when; none of
 * them is a verdict. The order is fixed, so a door is always where it was, and every door is one
 * step from every other.
 */
export function Home() {
  const { facts, readAt, busy, retakeAll } = useDoors();
  const setView = useApp((s) => s.setView);
  const marked = DOORS.filter((door) => facts[door.id].attention);

  return (
    <section className={styles.home} aria-labelledby="home-title">
      <header className={styles.head}>
        <h1 id="home-title" className={`${styles.title} display`}>Start with what you noticed</h1>
        <p className={styles.lede}>Each door holds one thing Sentinel read from this machine, and when. Open the one closest to what brought you here; the others stay one step away.</p>
      </header>

      <p className={styles.summary} role="status">
        {marked.length
          ? <>{marked.length === 1 ? 'One door holds' : `${marked.length} doors hold`} a record you have not opened on this browser: {marked.map((door, index) => <span key={door.id}>{index ? ', ' : ''}<strong>{door.question.toLowerCase()}</strong></span>)}.</>
          : busy ? 'Reading the doors…' : 'Nothing new since you last opened each door on this browser. The facts below still stand.'}
      </p>

      <ol className={styles.doors}>
        {DOORS.map((door) => (
          <li key={door.id} className={door.id === 'agent' ? styles.handoffCell : undefined}>
            <DoorCard door={door} fact={facts[door.id]} onOpen={() => setView(door.view)} />
          </li>
        ))}
      </ol>

      <footer className={styles.foot}>
        <p className={styles.legend}>
          <span className={styles.legendItem}><AttentionMark kind="stop">New stop</AttentionMark> or <AttentionMark kind="report">New reports</AttentionMark> a record newer than the last time you opened that door here. Opening it clears the mark.</span>
          <span className={styles.legendNote}>A mark points at a record to look at. It is not a judgement of the machine, and a door without one is not a clean bill.</span>
        </p>
        <p className={styles.legend}>
          <span className={styles.legendItem}><Known known="observed" /> observed</span>
          <span className={styles.legendItem}><Known known="zero" /> none returned</span>
          <span className={styles.legendItem}><Known known="notasked" /> not asked yet</span>
          <span className={styles.legendItem}><Known known="unreached" /> not reached or timed out</span>
          <span className={styles.legendItem}><Known known="denied" /> refused</span>
          <span className={styles.legendItem}><Known known="failed" /> failed</span>
        </p>
        <div className={styles.readAgain}>
          <span className={styles.readAt}>{readAt ? `Doors read from ${CLOCK.format(Date.parse(readAt))}` : 'Doors not read yet'}</span>
          <button className="button" onClick={retakeAll} aria-disabled={busy}>{busy ? 'Reading…' : 'Read the doors again'}</button>
        </div>
      </footer>

      <nav className={styles.places} aria-label="Places">
        <h2 className={styles.placesTitle}>Places, for what the machine is rather than what happened</h2>
        <ul>
          <li><button onClick={() => setView('machine')}><strong>Machine</strong><span>Processor, graphics, board, storage and network as Windows reports them</span></button></li>
          <li><button onClick={() => setView('record')}><strong>The System log</strong><span>Every record Windows wrote, newest first, with the record before any of them</span></button></li>
          <li><button onClick={() => setView('signals')}><strong>Signals</strong><span>Leads Sentinel noticed across readings, each with its rule</span></button></li>
          <li><button onClick={() => setView('diagnostics')}><strong>Diagnostics</strong><span>Memory, power and the PCIe fabric, read on request</span></button></li>
        </ul>
      </nav>
    </section>
  );
}

function Known({ known }: { known: KnownState }) {
  return <span className={styles.legendGlyph}><OutcomeGlyph known={known} /></span>;
}

/** One door: the question, the fact, the reading it came from. The whole card opens the situation. */
export function DoorCard({ door, fact, onOpen }: { door: Door; fact: DoorFact; onOpen: () => void }) {
  const changed = useChanged(`${fact.figure}|${fact.caption}|${fact.attention}`, fact.known !== 'taking');
  const handoff = door.id === 'agent';
  return (
    <button
      className={`${styles.door} ${handoff ? styles.handoff : ''} ${fact.attention ? styles[fact.attention] : ''} ${isHole(fact.known) ? styles.hole : styles[fact.known] ?? ''} ${changed ? styles.changed : ''}`}
      onClick={onOpen}
      aria-describedby={`door-${door.id}-fact`}
    >
      <span className={styles.doorTop}>
        <span className={styles.question}>{door.question}</span>
        {fact.attention ? <AttentionMark kind={fact.attention}>{fact.attention === 'stop' ? 'New stop' : 'New reports'}</AttentionMark> : null}
      </span>
      <span id={`door-${door.id}-fact`} className={styles.fact}>
        {fact.figure ? (
          <span className={styles.figureLine}>
            <span className={`${styles.figure} figure`}>{fact.figure}</span>
            {fact.unit ? <span className={fact.unit === '%' ? styles.unitTight : styles.unit}>{fact.unit}</span> : null}
          </span>
        ) : null}
        <span className={fact.figure ? styles.caption : styles.answer}>{fact.caption}</span>
        {fact.exact ? <span className={`${styles.exact} readout`}>{fact.exact}</span> : null}
      </span>
      <span className={styles.source}>
        <OutcomeGlyph known={fact.known} />
        <span className={styles.sourceText} title={fact.scope}>{fact.scope}</span>
        <span className={styles.open} aria-hidden="true">→</span>
      </span>
    </button>
  );
}

/** True for a moment after a door's fact changes; never on the first answer. */
export function useChanged(key: string, ready: boolean): boolean {
  const last = useRef<string | null>(null);
  const [changed, setChanged] = useState(false);
  useEffect(() => {
    if (!ready) return;
    const before = last.current;
    last.current = key;
    if (before === null || before === key) return;
    setChanged(true);
    const timer = window.setTimeout(() => setChanged(false), 1200);
    return () => window.clearTimeout(timer);
  }, [key, ready]);
  return changed;
}
