import { observed, section } from '../api';
import { AddToStack } from '../AddToStack';
import { OutcomeLine, clock } from '../Outcome';
import { Facts, Head, RowList, SectionHead, ago, byDay, size } from '../Sections';
import { useReading } from '../useReading';
import styles from './Dumps.module.css';

/** One file under the Windows minidump, full dump and live kernel report locations. */
interface DumpFile {
  name: string;
  path: string;
  bytes: number;
  modified: string;
}

/**
 * Crash dumps: what Windows wrote down the last times it stopped. The inventory is the evidence,
 * and an empty one is a finding in both directions — either the machine has not bounced, or it is
 * not configured to keep a dump when it does. The view never guesses which; the outcome line says
 * the inventory was read and held nothing, and nothing else appears that could be read as data.
 *
 * The reading returns the files newest first. This view keeps that order rather than sorting its
 * own, and groups the runs of one day, because two dumps under one date is the shape of an evening
 * that crashed twice.
 */
export function Dumps() {
  const taken = useReading<DumpFile[]>('dumps');
  const files = section(taken.reading, 'files') ?? [];
  const days = byDay(files, (f) => f.modified);

  return (
    <section>
      <Head title="Crash dumps" />
      <OutcomeLine taken={taken} noun="dump files" emptyText="No dump files under the Windows dump locations" />
      {observed(taken.reading) && files.length > 0 ? (
        <>
          <SectionHead title="Files" cls="raw" note={`newest first · ${size(files.reduce((n, f) => n + f.bytes, 0))} on disk`}>
            {taken.reading ? <AddToStack item={{ kind: 'reading', envelope: taken.reading, title: 'Crash dump inventory' }} /> : null}
          </SectionHead>
          {days.map(([label, rows]) => (
            <div key={label}>
              <p className={`${styles.day} label`}>{label}</p>
              <RowList
                items={rows}
                layout={styles.row}
                cells={(f) => (
                  <>
                    <span className={`${styles.name} readout`}>{f.name}</span>
                    <span className={`${styles.size} readout`}>{size(f.bytes)}</span>
                    <span className={`${styles.when} readout`}>
                      {clock.format(new Date(f.modified))} · {ago(f.modified)}
                    </span>
                  </>
                )}
                inspect={(f) => (
                  <Facts
                    rows={[
                      ['Path', <span className={styles.path}>{f.path}</span>],
                      ['Size', `${f.bytes.toLocaleString()} bytes`],
                      ['Written', f.modified],
                    ]}
                  />
                )}
              />
            </div>
          ))}
        </>
      ) : null}
    </section>
  );
}
