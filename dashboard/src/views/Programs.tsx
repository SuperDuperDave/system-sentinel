import { useState } from 'react';
import { AddToStack } from '../AddToStack';
import { EventRecord, observed } from '../api';
import { useDoors } from '../doors';
import { OutcomeMark, knownOf, whyNot } from '../Marks';
import { RowList, basisOf, part } from '../Sections';
import { AgentRecipe, ReadingFooter, SituationHead, Step } from '../Situation';
import { doorOf } from '../situations';
import { useApp } from '../store';
import { Taken } from '../useReading';
import { DumpFiles, Fault, FaultDetail, FaultOverview, FaultSummary, appOf, at, exceptionOf, faultIdentity, moduleOf } from './Crashes';
import { ReliabilityHistory } from './ReliabilityHistory';
import crashStyles from './Crashes.module.css';
import styles from './Stopped.module.css';

const KIND_WORD: Record<string, string> = { 'application crash': 'crash', 'application hang': 'hang', 'live kernel event': 'kernel' };

/**
 * "A program crashed or froze." Windows files a report when a program crashes or stops
 * responding, and when the kernel recovers from a fault the machine survived. The door's own
 * faults reading is the first step; each report opens in place, and its record-before link is the
 * one control that leaves the page, saying where it goes.
 */
export function Programs() {
  const door = doorOf('programs');
  const { taken } = useDoors();
  const faults = taken.programs as Taken<unknown>;
  const { faultKind, faultId } = useApp((s) => s.crashesView);
  const setCrashesView = useApp((s) => s.setCrashesView);
  const setView = useApp((s) => s.setView);
  const [further, setFurther] = useState<'dumps' | 'reliability' | null>(null);
  const reading = faults.reading;
  const records = observed(reading) ? part<EventRecord[]>(reading, 'records') ?? [] : [];
  const decoded = observed(reading) ? part<Fault[]>(reading, 'decoded') ?? [] : [];
  const summary = observed(reading) ? part<FaultSummary>(reading, 'summary') : null;
  const kind = faultKind && summary?.by_kind[faultKind] ? faultKind : null;
  const shown = kind ? decoded.filter((fault) => fault.kind === kind) : decoded;
  const times = new Map(records.map((r) => [r.RecordId, r.TimeCreated]));
  const known = knownOf(faults);

  return (
    <section className={styles.situation}>
      <SituationHead
        question={door.question}
        lede="Windows files a report when a program crashes or stops responding, and when the kernel recovers from a fault the machine survived. These are the reports in the Application log, newest first. A program that froze without Windows filing a report is not visible here."
      >
        <p className={styles.headFinding}>
          <OutcomeMark known={known}>
            {known === 'observed' ? `${decoded.length} ${decoded.length === 1 ? 'report' : 'reports'} decoded from ${records.length} Application-log records`
              : known === 'zero' ? 'No crash, hang or live kernel report in the Application log'
                : known === 'taking' ? 'Reading the reports…' : whyNot(reading, faults.problem)}
          </OutcomeMark>
        </p>
        <ReadingFooter taken={faults} />
      </SituationHead>

      <ol className={styles.steps}>
        <Step n={1} question="What Windows reported" known={known} reading="faults" cls="derived" basis={basisOf(reading, 'decoded')}
          finding={known === 'observed' ? 'Choose a kind to narrow the list; open a report to read it in place' : known === 'zero' ? 'Nothing to list' : 'Nothing to list until the reading answers'}>
          {summary && decoded.length ? (
            <FaultOverview summary={summary} rawCount={records.length} decodedCount={decoded.length} basis={basisOf(reading, 'summary')} selected={kind}
              onChoose={(next) => setCrashesView({ faultKind: next, faultId: null, focus: null })} />
          ) : null}
          {decoded.length ? (
            <div className={crashStyles.faultRows}>
              <p className={`${crashStyles.faultRowsCount} readout`} role="status">{shown.length} of {decoded.length} shown{kind ? '' : ' · every kind'}</p>
              <RowList
                items={shown}
                idOf={faultIdentity}
                openId={faultId}
                onOpenChange={(id) => setCrashesView({ faultId: id === null ? null : String(id), focus: id === null ? null : 'fault' })}
                layout={crashStyles.faultRow}
                cells={(f) => (
                  <>
                    <span className={`${crashStyles.time} readout`}>{at(times.get(f.RecordId))}</span>
                    <span className={`${crashStyles.kind} readout`}>{KIND_WORD[f.kind] ?? f.kind}</span>
                    <span className={crashStyles.app}>{appOf(f)}</span>
                    <span className={`${crashStyles.module} readout`}>{moduleOf(f)}</span>
                    <span className={`${crashStyles.exception} readout`}>{exceptionOf(f)}</span>
                  </>
                )}
                inspect={(f) => <FaultDetail fault={f} at={times.get(f.RecordId)} envelope={reading} rawRecords={records} />}
              />
            </div>
          ) : null}
        </Step>
        <li className={styles.furtherStep}>
          <span className={styles.furtherMark} aria-hidden="true">…</span>
          <div>
            <h3 className={styles.furtherTitle}>When this evidence runs out</h3>
            <p className={styles.furtherLede}>Each is read only when you open it.</p>
            <div className={styles.furtherChoices}>
              <button className="button" aria-expanded={further === 'dumps'} onClick={() => setFurther(further === 'dumps' ? null : 'dumps')}>Dump files on disk</button>
              <button className="button" aria-expanded={further === 'reliability'} onClick={() => setFurther(further === 'reliability' ? null : 'reliability')}>Windows’ reliability record</button>
              <button className="button quiet" onClick={() => setView('stopped')}>If the whole machine stopped ↗</button>
              <button className="button quiet" onClick={() => setView('performance')}>If it was slow first ↗</button>
            </div>
            {further === 'dumps' ? <div className={styles.furtherBody}><DumpFiles /></div> : null}
            {further === 'reliability' ? <div className={styles.furtherBody}><ReliabilityHistory /></div> : null}
          </div>
        </li>
      </ol>

      <AgentRecipe question={door.question} steps={door.recipe}>
        {reading && observed(reading) ? <AddToStack item={{ kind: 'reading', envelope: reading, title: 'Program crashes, hangs and kernel reports' }} label="Stack these reports" /> : null}
      </AgentRecipe>
    </section>
  );
}
