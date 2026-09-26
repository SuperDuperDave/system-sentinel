import { useState } from 'react';
import { AddToStack } from '../AddToStack';
import { CitedRecord, eventRef } from '../CitedRecord';
import { EventRecord, Reading, observed } from '../api';
import { useDoors } from '../doors';
import { AttentionGlyph, Known, LevelGlyph, OutcomeMark, isHole, knownOf, whyNot } from '../Marks';
import { MomentLink, basisOf, part } from '../Sections';
import { AgentRecipe, ReadingFooter, SituationHead, Step } from '../Situation';
import { RecipeStep, ageParts, doorOf, stopMoment } from '../situations';
import { useApp } from '../store';
import { Taken, useReading } from '../useReading';
import { ChangeEvidence } from './ChangesNearStop';
import { DumpFiles, DumpHeaderDetail, Stop, StopFacts, StopRawRows, howLong, lastRecordStatus, recordIds, recordToEstimate, stopIdentity } from './Crashes';
import { ReliabilityHistory } from './ReliabilityHistory';
import styles from './Stopped.module.css';

const DAY = new Intl.DateTimeFormat(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
const CLOCK = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
const SHORT = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', hour12: false });
const HOUR = 3_600_000;

/**
 * "It stopped or restarted by itself." Windows does not write down a freeze; the start after it
 * does. The door's own crash reading lists the stops; choosing one reads what surrounded it, in
 * the order the project's crash procedure reads it: the stop, the record before it, what changed,
 * the hardware's reports around it, and then the further readings when that runs out.
 *
 * Choosing a stop inspects; it does not navigate. The list stays where it is and only the
 * composition beside it changes. The newest stop is the composition on arrival, because the page
 * should answer before it is asked; a stop the person chose stays chosen across retakes, and a
 * chosen stop that a retake no longer returns is said to be missing rather than silently replaced.
 */
export function Stopped() {
  const door = doorOf('stopped');
  const doors = useDoors();
  const { stopCount, stopId } = useApp((s) => s.crashesView);
  const setCrashesView = useApp((s) => s.setCrashesView);
  const wider = useReading('crash', { count: stopCount }, stopCount !== 5, { hold: 'same-params' });
  const crash = (stopCount === 5 ? doors.taken.stopped : wider) as Taken<unknown>;
  const reading = crash.reading;
  const stops = observed(reading) ? part<Stop[]>(reading, 'stops') ?? [] : [];
  const chosen = stopId === null ? (stops.length ? 0 : -1) : stops.findIndex((stop) => stopIdentity(stop) === stopId);
  const stop = chosen >= 0 ? stops[chosen] : null;
  const known = knownOf(crash);
  const coverage = part<{ system?: { retained_from?: string | null }; reports?: { retained_from?: string | null } }>(reading, 'coverage');

  return (
    <section className={styles.situation}>
      <SituationHead
        question={door.question}
        lede="Windows does not write down a freeze; the start after it does. Sentinel composed these stops from the starts that followed them. Choose one to read what surrounded it, in order."
      >
        <p className={styles.headFinding}>
          <OutcomeMark known={known}>
            {known === 'observed' ? `${stops.length} unplanned ${stops.length === 1 ? 'stop' : 'stops'} returned, newest first`
              : known === 'zero' ? 'No unplanned stop in the records Windows keeps'
                : known === 'taking' ? 'Reading the stops…' : whyNot(reading, crash.problem)}
          </OutcomeMark>
          {coverage?.system?.retained_from ? <span className={styles.reach}>The System log reaches back to {DAY.format(Date.parse(coverage.system.retained_from))}; an older stop cannot appear here.</span> : null}
        </p>
        <ReadingFooter taken={crash} />
      </SituationHead>

      {stops.length ? (
        <div className={styles.layout}>
          <nav className={styles.stopList} aria-label="Unplanned stops">
            <ol>
              {stops.map((item, index) => {
                const at = stopMoment(item);
                const age = at ? ageParts(at) : null;
                // New means newer than what this person had acknowledged before arriving here.
                const acknowledged = doors.seenOnArrival.stopped;
                const recent = at && acknowledged !== undefined ? acknowledged === null || Date.parse(at) > Date.parse(acknowledged) : false;
                const selected = index === chosen;
                return (
                  <li key={stopIdentity(item)}>
                    <button className={`${styles.stopButton} ${selected ? styles.stopSelected : ''}`} aria-pressed={selected}
                      onClick={() => setCrashesView({ stopId: stopIdentity(item), focus: 'stop', changesStopId: null, changesBefore: null })}>
                      <span className={styles.stopWhen}>
                        {recent ? <AttentionGlyph kind="stop" /> : <span className={styles.stopDot} aria-hidden="true" />}

                        <span className="readout">{at ? `${DAY.format(Date.parse(at))} · ${SHORT.format(Date.parse(at))}` : 'time not recorded'}</span>
                        <span className={styles.stopAge}>{recent ? <span className={styles.newWord}>New · </span> : null}{age ? `${age.figure} ${age.unit}` : null}</span>
                      </span>
                      <span className={styles.stopName}>{bugcheckName(item)}</span>
                      <span className={styles.stopMeta}>{[item.down_seconds != null ? `down ${howLong(item.down_seconds)}` : null, item.dump?.name ? 'dump on disk' : null, !item.started_at && item.reported_at ? 'report only' : null].filter(Boolean).join(' · ') || 'no further detail returned'}</span>
                    </button>
                  </li>
                );
              })}
            </ol>
            <button className={`button quiet ${styles.more}`} onClick={() => setCrashesView({ stopCount: stopCount === 5 ? 20 : 5, stopId: null })}>
              {stopCount === 5 ? 'Read the newest 20 stops' : 'Back to the newest 5'}
            </button>
          </nav>

          <div className={styles.composition}>
            {stop ? <Composition key={stopIdentity(stop)} stop={stop} envelope={reading} />
              : <p className={styles.missing} role="status">The stop you chose is not in this reading. Choose one from the list; nothing was chosen for you.</p>}
          </div>
        </div>
      ) : null}

      {!stops.length && known !== 'taking' ? (
        <div className={styles.further}>
          <h2 className={styles.furtherTitle}>Other places a stop leaves evidence</h2>
          <FurtherReadings stop={null} />
        </div>
      ) : null}
    </section>
  );
}

function bugcheckName(stop: Stop): string {
  const named = [stop.bugcheck?.name, stop.bugcheck?.code].filter(Boolean).join(' · ');
  if (named) return named;
  if (stop.no_bugcheck_recorded === null) return 'Bug check status unknown';
  return stop.no_bugcheck_recorded ? 'No bug check recorded' : 'No bug check named';
}

/** The fixed order behind the door, for one stop. Every parameter comes from the stop itself. */
function stopRecipe(stop: Stop): { steps: RecipeStep[]; anchor: string | null; window: { since: string; before: string } | null } {
  const anchor = stop.started_at ?? stop.announced_at ?? stop.reported_at;
  const first = stop.stopped_at ?? stop.started_at;
  const last = stop.started_at ?? stop.stopped_at;
  const window = first && last ? { since: new Date(Date.parse(first) - HOUR).toISOString(), before: new Date(Date.parse(last) + HOUR).toISOString() } : null;
  const steps: RecipeStep[] = [
    { reading: 'crash', params: { count: 5 }, answers: 'the unplanned stops; this is the one chosen here' },
  ];
  if (anchor) steps.push({ reading: 'record', params: { before: anchor, count: 25 }, answers: 'what the System log held before the next start' });
  if (stop.stopped_at) steps.push({ reading: 'changes', params: { before: stop.stopped_at, hours: 168, count: 100 }, answers: 'updates, drivers and installs in the week before Windows’ stop estimate' });
  if (window) {
    steps.push({ reading: 'whea_window', params: { source: 'system', ...window, order: 'oldest', count: 50 }, answers: 'System hardware error reports from an hour before the stop to an hour after the start' });
    steps.push({ reading: 'whea_window', params: { source: 'kernel_whea', ...window, order: 'oldest', count: 50 }, answers: 'the same window in the separate Kernel-WHEA channel' });
  }
  return { steps, anchor, window };
}

function Composition({ stop, envelope }: { stop: Stop; envelope: Reading | null }) {
  const { steps, anchor, window } = stopRecipe(stop);
  const at = stopMoment(stop);
  const ids = recordIds(stop);
  const reportOnly = !stop.started_at && !stop.stopped_at && !stop.announced_at;

  return (
    <>
      <h2 className={styles.compositionTitle}>
        <span className={styles.compositionEyebrow}>The stop and what surrounded it</span>
        {at ? `${DAY.format(Date.parse(at))}, ${CLOCK.format(Date.parse(at))}` : 'A stop without a recorded time'}
      </h2>
      <ol className={styles.steps}>
        <Step n={1} id="step-stop" question="What Windows recorded about the stop" known="observed"
          finding={reportOnly ? 'Only a Windows error report was returned for this stop' : `${bugcheckName(stop)}${stop.down_seconds != null ? ` · down ${howLong(stop.down_seconds)}` : ''}`}
          reading="crash" cls="derived" basis={basisOf(envelope, 'stops')}>
          {!reportOnly ? <EvidenceTimes stop={stop} /> : null}
          <dl className={styles.keyFacts}>
            <div><dt>Bug check</dt><dd>{stop.bugcheck ? <>{bugcheckName(stop)}{stop.bugcheck.source ? <span className={styles.quiet}> · named by {stop.bugcheck.source}</span> : null}</> : bugcheckName(stop)}</dd></div>
            <div><dt>Dump</dt><dd>{stop.dump?.name ? <><span className="readout">{stop.dump.name}</span><span className={styles.quiet}> · matched by {stop.dump.matched_by}</span></> : <span className={styles.quiet}>{stop.dump_inventory_complete ? 'none matched on disk' : 'none matched; the inventory was incomplete'}</span>}</dd></div>
            <div><dt>Quiet before the start</dt><dd>{stop.quiet_seconds != null ? howLong(stop.quiet_seconds) : <span className={styles.quiet}>{stop.last_record_before ? 'not computed' : lastRecordStatus(stop).toLowerCase()}</span>}</dd></div>
          </dl>
          <details className={styles.exact}>
            <summary>Every field of this stop, and the records it was composed from</summary>
            <div className={styles.exactBody}>
              <StopFacts stop={stop} />
              <StopRawRows stop={stop} envelope={envelope} />
              {stop.last_record_before ? <CitedRecord key={`last:${stop.last_record_before.RecordId}`}
                citation={eventRef({ role: 'last_before_restart', reading: 'event_record', params: { log: 'System', record_id: stop.last_record_before.RecordId, time_created: stop.last_record_before.TimeCreated } })}
                held={stop.last_record_before} heldAt={envelope?.asked_at} heldKind="projection" /> : null}
            </div>
          </details>
        </Step>

        {anchor ? <RecordBefore anchor={anchor} stop={stop} /> : (
          <Step n={2} question="What the System log held before it" known="notasked" finding="There is no start or announcement time to read before" reading="record" />
        )}

        {stop.stopped_at ? <Changes stop={stop} before={stop.stopped_at} /> : (
          <Step n={3} question="What changed in the week before" known="notasked" finding="Windows returned no stop estimate, so a change window cannot be placed before this stop" reading="changes" />
        )}

        {window ? <HardwareAround window={window} /> : (
          <Step n={4} question="Hardware error reports around it" known="notasked" finding="This stop has no time to centre a window on" reading="whea_window" />
        )}

        <li className={styles.furtherStep}>
          <span className={styles.furtherMark} aria-hidden="true">…</span>
          <div>
            <h3 className={styles.furtherTitle}>When this evidence runs out</h3>
            <p className={styles.furtherLede}>Further readings that can speak to a stop. Each is read only when you open it.</p>
            <FurtherReadings stop={stop} />
          </div>
        </li>
      </ol>

      <AgentRecipe question="It stopped or restarted by itself" steps={steps}>
        {envelope && ids.length ? <AddToStack item={{ kind: 'selection', envelope, ids, title: `Stop at ${anchor ?? at ?? 'an unknown time'}` }} label="Stack this stop" /> : null}
      </AgentRecipe>
    </>
  );
}

/**
 * The stop's own evidence times on one line, placed by when they were written. The last record
 * can fall after Windows' estimate; the line shows that rather than tidying it away.
 */
function EvidenceTimes({ stop }: { stop: Stop }) {
  const points = [
    { role: 'Last System record', at: stop.last_record_before?.TimeCreated ?? null, note: stop.last_record_before ? [stop.last_record_before.ProviderName?.replace(/^Microsoft-Windows-/, ''), stop.last_record_before.Id == null ? null : `event ${stop.last_record_before.Id}`].filter(Boolean).join(' · ') : lastRecordStatus(stop) },
    { role: 'Windows’ stop estimate', at: stop.stopped_at, note: stop.stopped_at ? 'read from EventLog 6008' : 'no estimate returned' },
    { role: 'Next start', at: stop.started_at, note: stop.started_at ? 'Kernel-General start record' : 'no start returned' },
  ];
  const times = points.map((p) => (p.at ? Date.parse(p.at) : NaN)).filter(Number.isFinite);
  const min = Math.min(...times);
  const span = Math.max(...times) - min;
  const relation = recordToEstimate(stop.last_record_before?.TimeCreated, stop.stopped_at);
  return (
    <figure className={styles.times}>
      <div className={styles.timeLine} aria-hidden="true">
        {points.map((p, index) => {
          const t = p.at ? Date.parse(p.at) : NaN;
          if (!Number.isFinite(t)) return null;
          const left = span > 0 ? ((t - min) / span) * 100 : 50 + (index - 1) * 8;
          return <span key={p.role} className={`${styles.tick} ${index === 2 ? styles.tickStart : ''}`} style={{ left: `${left}%` }} />;
        })}
      </div>
      <ol className={styles.timeLabels}>
        {points.map((p) => (
          <li key={p.role}>
            <span className={styles.timeRole}>{p.role}</span>
            <strong className="readout">{p.at ? CLOCK.format(Date.parse(p.at)) : 'not recorded'}</strong>
            <span className={styles.timeNote}>{p.note}</span>
          </li>
        ))}
      </ol>
      <figcaption className={styles.timeCaption}>
        Placed by the time each was written{span > 0 ? `, across ${howLong(Math.round(span / 1000))}` : ''}.{relation ? ` ${relation}.` : ''} These times sit near each other; they do not establish a cause.
      </figcaption>
    </figure>
  );
}

function RecordBefore({ anchor, stop }: { anchor: string; stop: Stop }) {
  const taken = useReading<EventRecord[]>('record', { before: anchor, count: 25 }, true, { hold: 'same-params' });
  const rows = observed(taken.reading) ? part<EventRecord[]>(taken.reading, 'records') ?? [] : [];
  const known = knownOf(taken);
  const shown = rows.slice(-8);
  const oldest = rows[0]?.TimeCreated;
  const retained = part<{ log_oldest?: string | null }>(taken.reading, 'collection')?.log_oldest;
  return (
    <Step n={2} id="step-record" question="What the System log held before it" known={known} reading="record" cls="raw"
      finding={known === 'observed' ? `${rows.length} records before the next start at ${CLOCK.format(Date.parse(anchor))}${oldest ? `, from ${SHORT.format(Date.parse(oldest))}` : ''}`
        : known === 'zero' ? (retained && Date.parse(retained) >= Date.parse(anchor)
          ? `No record returned: the System log’s oldest retained record is from ${DAY.format(Date.parse(retained))}, after this stop. This is the log’s reach, not a quiet machine.`
          : 'No retained System record returned before this moment')
          : known === 'taking' ? 'Reading…' : whyNot(taken.reading, taken.problem)}>
      {shown.length ? (
        <>
          <ol className={styles.logRows} aria-label={`The last ${shown.length} records before the next start`}>
            {rows.length > shown.length ? <li className={styles.logEarlier}>{rows.length - shown.length} earlier records in this reading</li> : null}
            {shown.map((row) => (
              <li key={`${row.RecordId}`} className={styles.logRow}>
                <span className={`${styles.logTime} readout`}>{CLOCK.format(Date.parse(row.TimeCreated))}</span>
                <span className={styles.logLevel}><LevelGlyph level={row.Level} /><span>{row.LevelDisplayName}</span></span>
                <span className={styles.logSource}>{row.ProviderName.replace(/^Microsoft-Windows-/, '')} <span className="readout">· {row.Id}</span></span>
                <span className={styles.logMessage}>{row.Message ? row.Message.split('\n')[0] : 'no message text'}</span>
              </li>
            ))}
            <li className={styles.logMarker}>
              <span className="readout">{stop.stopped_at ? `stop estimate ${CLOCK.format(Date.parse(stop.stopped_at))} · ` : ''}next start {CLOCK.format(Date.parse(anchor))}</span>
            </li>
          </ol>
          <div className={styles.stepActions}>
            <MomentLink at={anchor} label="Open the whole frame in the System log" sourceKey={`stop:${stopIdentity(stop)}:${anchor}`} />
          </div>
        </>
      ) : null}
      <ReadingFooter taken={taken as Taken<unknown>} />
    </Step>
  );
}

function Changes({ stop, before }: { stop: Stop; before: string }) {
  const id = stopIdentity(stop);
  const open = useApp((s) => s.crashesView.changesStopId === id && s.crashesView.changesBefore === before);
  const setCrashesView = useApp((s) => s.setCrashesView);
  const taken = useReading('changes', { before, hours: 168, count: 100 }, open, { hold: 'same-params' });
  const known: Known = open ? knownOf(taken) : 'notasked';
  const count = observed(taken.reading) ? (part<unknown[]>(taken.reading, 'changes') ?? []).length : 0;
  return (
    <Step n={3} id="step-changes" question="What changed in the week before" known={known} reading="changes" cls="derived"
      finding={!open ? 'Not read yet. This reading takes a few seconds, so it waits for you.'
        : known === 'observed' ? `${count} update, device or installation ${count === 1 ? 'result' : 'results'} in the seven days before the estimate`
          : known === 'zero' ? 'No update, device or installation result in the seven days before the estimate'
            : known === 'taking' ? 'Reading…' : whyNot(taken.reading, taken.problem)}>
      {open ? <><ChangeEvidence before={before} taken={taken} /><ReadingFooter taken={taken} /></> : (
        <button className="button" onClick={() => setCrashesView({ changesStopId: id, changesBefore: before })}>Read what changed before {SHORT.format(Date.parse(before))}</button>
      )}
    </Step>
  );
}

interface WheaPreview { RecordId: number | string; Log?: string; Id: number; Level: number; TimeCreated: string; Message: string | null }

function HardwareAround({ window }: { window: { since: string; before: string } }) {
  const system = useReading('whea_window', { source: 'system', ...window, order: 'oldest', count: 50 }, true, { hold: 'same-params' });
  const channel = useReading('whea_window', { source: 'kernel_whea', ...window, order: 'oldest', count: 50 }, true, { hold: 'same-params' });
  const sides: [string, Taken<unknown>][] = [['System log', system], ['Kernel-WHEA channel', channel]];
  const knowns = sides.map(([, t]) => knownOf(t));
  // The step reports what was found; a log that was not read is named beside it, never folded into a zero.
  const known: Known = knowns.includes('taking') ? 'taking' : knowns.includes('observed') ? 'observed'
    : knowns.includes('zero') ? 'zero' : knowns[0];
  const count = sides.reduce((n, [, t]) => n + (observed(t.reading) ? (part<WheaPreview[]>(t.reading, 'records') ?? []).length : 0), 0);
  const partial = knowns.some(isHole) && !isHole(known);
  return (
    <Step n={4} id="step-hardware" question="Hardware error reports around it" known={known} reading="whea_window" cls="raw"
      finding={known === 'taking' ? 'Reading both logs…'
        : known === 'observed' ? `${count} ${count === 1 ? 'report' : 'reports'} filed from ${SHORT.format(Date.parse(window.since))} to ${SHORT.format(Date.parse(window.before))}${partial ? '; one log was not read' : ''}`
          : known === 'zero' ? `No report filed from ${SHORT.format(Date.parse(window.since))} to ${SHORT.format(Date.parse(window.before))} in either log${partial ? '; one log was not read' : ''}`
            : 'Neither log was read'}>
      <p className={styles.caveat}>Report times say when Windows filed each report. A report filed at the next start may describe an error from before the stop.</p>
      <div className={styles.sides}>
        {sides.map(([label, t]) => {
          const rows = observed(t.reading) ? part<WheaPreview[]>(t.reading, 'records') ?? [] : [];
          const k = knownOf(t);
          return (
            <div key={label} className={styles.side}>
              <p className={styles.sideHead}><OutcomeMark known={k}>{label} · {k === 'observed' ? `${rows.length} filed` : k === 'zero' ? 'none filed in this window' : k === 'taking' ? 'reading…' : whyNot(t.reading, t.problem)}</OutcomeMark></p>
              {rows.length ? (
                <ol className={styles.sideRows}>
                  {rows.slice(0, 6).map((row) => (
                    <li key={`${row.Log}:${row.RecordId}`}>
                      <span className="readout">{CLOCK.format(Date.parse(row.TimeCreated))}</span>
                      <span>event {row.Id}{row.Message ? ` · ${row.Message.split('\n')[0]}` : ''}</span>
                    </li>
                  ))}
                  {rows.length > 6 ? <li className={styles.quiet}>{rows.length - 6} more in this reading</li> : null}
                </ol>
              ) : null}
            </div>
          );
        })}
      </div>
    </Step>
  );
}

type Further = 'dump' | 'dumps' | 'reliability' | null;

function FurtherReadings({ stop }: { stop: Stop | null }) {
  const [open, setOpen] = useState<Further>(null);
  const setView = useApp((s) => s.setView);
  const toggle = (next: Further) => setOpen((current) => (current === next ? null : next));
  const readableDump = stop?.dump?.path && stop.dump.bytes != null ? stop.dump.path : null;
  return (
    <div className={styles.furtherList}>
      <div className={styles.furtherChoices}>
        {readableDump ? <button className="button" aria-expanded={open === 'dump'} onClick={() => toggle('dump')}>Inside this stop’s dump</button> : null}
        <button className="button" aria-expanded={open === 'dumps'} onClick={() => toggle('dumps')}>Dump files on disk</button>
        <button className="button" aria-expanded={open === 'reliability'} onClick={() => toggle('reliability')}>Windows’ reliability record</button>
        <button className="button quiet" onClick={() => setView('signals')}>Leads across readings ↗</button>
        <button className="button quiet" onClick={() => setView('diagnostics')}>Memory and power ↗</button>
        <button className="button quiet" onClick={() => setView('programs')}>Program crashes ↗</button>
      </div>
      {open === 'dump' && readableDump ? <div className={styles.furtherBody}><DumpHeaderDetail path={readableDump} /></div> : null}
      {open === 'dumps' ? <div className={styles.furtherBody}><DumpFiles /></div> : null}
      {open === 'reliability' ? <div className={styles.furtherBody}><ReliabilityHistory /></div> : null}
    </div>
  );
}
