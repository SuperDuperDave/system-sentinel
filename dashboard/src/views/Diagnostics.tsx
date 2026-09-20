import { useState } from 'react';
import { AddToStack } from '../AddToStack';
import { observed, type Section as SectionData } from '../api';
import { OutcomeLine, firstLine, clock } from '../Outcome';
import { Head, RowList, Section, Tree, Value } from '../Sections';
import { useReading } from '../useReading';
import styles from './Diagnostics.module.css';

/**
 * Diagnostics: the four readings that ask the machine about itself rather than about its log.
 *
 * Each is heavy — several queries, seconds of the machine's attention — so none is taken until
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
      <Panel name="pcie" title="PCIe" what="Every device on the PCI bus: the bridges that carry the tree, the endpoints hanging off them, and which endpoints share an upstream link." />
      <Panel name="power" title="Power" what="The sleep states the firmware offers, what Windows chose, what may wake the machine, and how it has moved between states." />
      <Panel name="memory" title="Memory" what="What is in each slot, how fast it is running against its rating, and what the log holds about memory faults." />
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
      {sections.map((s) => (
        <div key={s.name} className={styles.section}>
          <Section title={sectionTitle(s.name)} cls={s.class} basis={s.basis}>
            <Payload name={name} section={s} />
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
function Payload({ name, section }: { name: string; section: SectionData<unknown> }) {
  const data = section.data;
  if (name === 'pcie' && section.name === 'groups') return <Groups groups={data as Group[]} />;
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
  RecordId: number;
  Id: number;
  ProviderName: string;
  TimeCreated: string;
  LevelDisplayName?: string;
  Message?: string | null;
}

interface Group {
  root_port: { instance_id: string; name: string };
  members: { name: string; instance_id: string; class: string; status: string; problem: string; address: { address?: string } | null }[];
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

/** Endpoints that share an upstream link: a fault on one can present on another. */
function Groups({ groups }: { groups: Group[] }) {
  return (
    <RowList
      items={groups}
      idOf={(g) => g.root_port.instance_id}
      layout={styles.groupRow}
      cells={(g) => (
        <>
          <span className={styles.deviceName}>{g.root_port.name}</span>
          <span className={`${styles.state} readout`}>{g.members.length} behind it</span>
        </>
      )}
      inspect={(g) => (
        <ul className={styles.members}>
          {g.members.map((m) => (
            <li key={m.instance_id} className={styles.member}>
              <span className={styles.deviceName}>{m.name}</span>
              <span className={`${styles.address} readout`}><Value value={m.address?.address ?? null} /></span>
              <span className={`${styles.state} readout`}>{m.status === 'OK' ? 'ok' : m.status.toLowerCase()}</span>
              <span className={`${styles.instance} readout`}>{m.instance_id}</span>
            </li>
          ))}
        </ul>
      )}
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
function sectionTitle(name: string): string {
  if (name === 'raw') return 'As Windows reported it';
  if (name === 'derived') return 'Computed from it';
  return name;
}

const NOUNS: Record<string, [string, string]> = {
  pcie: ['endpoint', 'endpoints'],
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
  const pair = NOUNS[name] ?? ['record', 'records'];
  return count === 1 ? pair[0] : pair[1];
};
const emptyFor = (name: string): string => EMPTY[name] ?? 'Nothing came back';
