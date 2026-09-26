import { Line } from './words';
import styles from './Summary.module.css';

/** The sentences above a chart, one per source, each led by that source's mark. */
export function Summary({ lines }: { lines: Line[] }) {
  return (
    <ul className={styles.summary}>
      {lines.map((line) => (
        <li key={line.text} className={styles.line}>
          <span className={`${styles.glyph} ${styles[`tone_${line.tone}`]}`} aria-hidden="true" />
          <span>{line.text}</span>
        </li>
      ))}
    </ul>
  );
}
