import { useState } from 'react';
import { AddToStack } from '../AddToStack';
import { observed, type RecordId, type Section as SectionData } from '../api';
import { OutcomeLine, firstLine, clock } from '../Outcome';
import { Head, RowList, Section, Tree, part } from '../Sections';
import { useReading } from '../useReading';
import { MemoryMap, type MemorySummary } from './MemoryMap';
import { PcieMap, type PcieCoverage, type PcieGroup } from './PcieMap';
import styles from './Diagnostics.module.css';

/**
 * Diagnostics: the four readings that ask the machine about itself rather than about its log.
 *
 * Three are heavy — several queries, seconds of the machine's attention — so none is taken until
 * the person asks for it. That is the whole design of this view: four offers, and what came back
 * under each one, with what Windows said kept apart from what the tool computed from it.
 */
export function Diagnostics() {
  return (
    <section>
      <Head title="Diagnostics" />
      <p className={styles.lede}>
        Four readings taken only when you ask. Each runs its queries against the machine there and then; nothing here is cached from before.
      </p>
      <Panel name="pcie" title="PCIe" what="The present PCI device inventory, with upstream groups where Windows reported enough parent relationships to establish them." />
      <Panel name="power" title="Power" what="The sleep states the firmware offers, what Windows chose, what may wake the machine, and how it has moved between states." />
      <Panel name="memory" title="Memory" what="Which modules Windows returned, their reported capacity and speed, and Windows' own memory test result. Hardware error records are in Hardware errors." />
      <Panel name="constraints" title="Constraints" what="What the machine is not using and why: devices that are present and not operating, with the problem each one reports." />
    </section>
  );
}

/** One reading: the offer, then the outcome, then its sections, each labelled with its class. */
function Panel({ name, title, what }: { name: string; title: string; what: string }) {
  const [asked, setAsked] = useState(false);
  const taken = useReading<unknown>(name, {}, asked);
  // Only a reading that observed the machine has anything to show. Whatever an unobserved one
  // carries, nothing of it is rendered: the outcome line is the whole answer.
  const sections = observed(taken.reading) ? taken.reading?.sections ?? [] : [];
  const orderedSections = name === 'pcie' ? [...sections].sort((a, b) => Number(b.name === 'groups') - Number(a.name === 'groups')) : sections;
  const pcieCoverage = name === 'pcie' ? (sections.find((s) => s.name === 'coverage')?.data as PcieCoverage | undefined) ?? null : null;
  const memory = name === 'memory' && observed(taken.reading) ? part<MemorySummary>(taken.reading, 'derived') : null;

  return (
    <article className={styles.panel} aria-labelledby={`panel-${name}`}>
      <div className={styles.panelHead}>
        <h2 className={`${styles.panelTitle} display`} id={`panel-${name}`}>{title}</h2>
        <span className={`${styles.reading} readout`}>{name}</span>
        {taken.reading ? <AddToStack item={{ kind: 'reading', envelope: taken.reading }} label="Stack this reading" /> : null}
      </div>
      <p className={styles.what}>{what}</p>
      {asked ? <OutcomeLine taken={taken} noun={nounFor(name, taken.reading?.count ?? null)} emptyText={emptyFor(name)} /> : (
        <p className={styles.takeLine}>
          <button className={styles.take} onClick={() => setAsked(true)}>Take the reading</button>
        </p>
      )}
      {memory ? <MemoryMap data={memory} /> : null}
      {orderedSections.map((s) => (
        <div key={s.name} className={styles.section} id={`diagnostic-${name}-${s.name}`}>
          <Section title={sectionTitle(s.name, name)} cls={s.class} basis={s.basis}>
            <Payload name={name} section={s} pcieCoverage={pcieCoverage} />
          </Section>
        </div>
      ))}
    </article>
  );
}

/**
 * A section's payload. Devices and log records are the two shapes this reading family returns
 * that a plain tree renders badly — one is a list to scan, the other a run of the log — so each
 * gets its own rows. Everything else is shown field for field, in the machine's own names.
 */
function Payload({ name, section, pcieCoverage }: { name: string; section: SectionData<unknown>; pcieCoverage: PcieCoverage | null }) {
  const data = section.data;
  if (name === 'pcie' && section.name === 'groups') return <PcieMap groups={data as PcieGroup[] | null} coverage={pcieCoverage} />;
  if (isDevices(data)) return <Devices devices={data} />;
  if (isRecords(data)) return <Records records={data} />;

  if (data && typeof data === 'object' && !Array.isArray(data)) {
    const fields = Object.entries(data as Record<string, unknown>);
    const runs = fields.filter(([, v]) => isRecords(v) && v.length > 0);
    if (runs.length) {
      const rest = Object.fromEntries(fields.filter(([k]) => !runs.some(([rk]) => rk === k)));
      return (
        <>
          <Tree value={rest} />
          {runs.map(([key, value]) => (
            <div key={key} className={styles.run}>
              <p className="label">{key}</p>
              <Records records={value as LogRecord[]} />
            </div>
          ))}
        </>
      );
    }
  }
  return <Tree value={data} />;
}

interface Device {
  Name: string;
  InstanceId: string;
  Class: string;
  Status: string;
  Problem: string;
  ProblemDescription?: string;
}

interface LogRecord {
  RecordId: RecordId;
  Id: number;
  ProviderName: string;
  TimeCreated: string;
  LevelDisplayName?: string;
  Message?: string | null;
}

/** A device list: the name to scan, the state beside it, and the whole record on inspect. */
function Devices({ devices }: { devices: Device[] }) {
  return (
    <RowList
      items={devices}
      idOf={(d) => d.InstanceId}
      layout={styles.deviceRow}
      cells={(d) => (
        <>
          <span className={styles.deviceName}>{d.Name}</span>
          <span className={`${styles.deviceClass} readout`}>{d.Class}</span>
          <span className={`${styles.state} readout`}>{d.Status === 'OK' && d.Problem === 'CM_PROB_NONE' ? 'ok' : d.Problem.replace(/^CM_PROB_/, '').toLowerCase().replace(/_/g, ' ')}</span>
        </>
      )}
      inspect={(d) => <Tree value={d} />}
    />
  );
}

/** A run of the log carried inside a reading: the same records, read the same way. */
function Records({ records }: { records: LogRecord[] }) {
  return (
    <RowList
      items={records}
      idOf={(r) => r.RecordId}
      layout={styles.recordRow}
      cells={(r) => (
        <>
          <span className={`${styles.when} readout`}>{stamp(r.TimeCreated)}</span>
          <span className={`${styles.provider} readout`}>{r.ProviderName.replace(/^Microsoft-Windows-/, '')}</span>
          <span className={`${styles.eventId} readout`}>{r.Id}</span>
          <span className={styles.message}>{r.Message ? firstLine(r.Message) : ''}</span>
        </>
      )}
      inspect={(r) => <Tree value={r} />}
    />
  );
}

const isDevices = (v: unknown): v is Device[] =>
  Array.isArray(v) && v.length > 0 && v.every((x) => !!x && typeof x === 'object' && 'InstanceId' in x && 'Name' in x);

const isRecords = (v: unknown): v is LogRecord[] =>
  Array.isArray(v) && v.length > 0 && v.every((x) => !!x && typeof x === 'object' && 'RecordId' in x && 'TimeCreated' in x);

const stamp = (iso: string): string => {
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? iso : `${at.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })} ${clock.format(at)}`;
};

/** A section named for what it holds keeps its name; one named for its class says what that means. */
function sectionTitle(name: string, reading: string): string {
  if (name === 'raw') return 'As Windows reported it';
  if (name === 'derived') return 'Computed from it';
  if (name === 'groups') return 'Reported upstream groups';
  if (name === 'devices') return 'PCI devices Windows returned';
  if (name === 'coverage') return 'Parent relation coverage';
  if (name === 'collection') return reading === 'pcie' ? 'Relation source' : 'Query outcomes';
  return name;
}

const NOUNS: Record<string, [string, string]> = {
  pcie: ['PCI device', 'PCI devices'],
  power: ['setting', 'settings'],
  memory: ['module', 'modules'],
  constraints: ['device', 'devices'],
};
const EMPTY: Record<string, string> = {
  pcie: 'No PCI devices were enumerated',
  power: 'Nothing was configured to report',
  memory: 'No memory modules were reported',
  constraints: 'Every present device is working: nothing is disabled or in error',
};
const nounFor = (name: string, count: number | null): string => {
  if (name === 'memory' && count == null) return 'module inventory not observed';
  const pair = NOUNS[name] ?? ['record', 'records'];
  return count === 1 ? pair[0] : pair[1];
};
const emptyFor = (name: string): string => EMPTY[name] ?? 'Nothing came back';
