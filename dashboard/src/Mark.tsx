import { useId } from 'react';
import styles from './Mark.module.css';

/** The trace, on a 64-unit grid. Owned by docs/design/IDENTITY-DIRECTIONS-2026-09-20.md. */
export const S_PATH = 'M45 19C42 12 22 11 21 20C20 29 44 30 44 41C44 50 23 53 18 45';
const END = { cx: 18, cy: 45 };
const GRAT = 8;

/** The mark: the S as one oscilloscope trace with the beam's dot, on the field tile with its faint graticule. */
export function Mark({ className, title = 'System Sentinel', tile = true }: { className?: string; title?: string | null; tile?: boolean }) {
  const id = useId().replace(/:/g, '');
  return (
    <svg className={className} viewBox="0 0 64 64" role={title ? 'img' : undefined} aria-hidden={title ? undefined : true} aria-label={title ?? undefined}>
      <defs>
        <filter id={`${id}g`} x="-40%" y="-40%" width="180%" height="180%"><feGaussianBlur stdDeviation="2.6" /></filter>
        <pattern id={`${id}p`} width={GRAT} height={GRAT} patternUnits="userSpaceOnUse"><path d={`M0 .5H${GRAT}M.5 0V${GRAT}`} className={styles.gratLine} /></pattern>
      </defs>
      {tile && (
        <>
          <rect width="64" height="64" rx="14" className={styles.tile} />
          <rect x="1" y="1" width="62" height="62" rx="13" fill={`url(#${id}p)`} />
          <path d="M32 4v4M32 56v4M4 32h4M56 32h4" className={styles.gratTick} />
        </>
      )}
      <path d={S_PATH} className={styles.glow} filter={`url(#${id}g)`} />
      <path d={S_PATH} className={styles.trace} />
      <circle cx={END.cx} cy={END.cy} r="4.2" className={styles.dotGlow} filter={`url(#${id}g)`} />
      <circle cx={END.cx} cy={END.cy} r="2.6" className={styles.dot} />
    </svg>
  );
}

/** The mark beside the wordmark: the display face, tracked, in capitals. */
export function Lockup({ className }: { className?: string }) {
  return (
    <span className={`${styles.lockup} ${className ?? ''}`}>
      <Mark className={styles.mark} title={null} />
      <span className={styles.wordmark}>System Sentinel</span>
    </span>
  );
}
