import { ViewId } from './store';

/** Quiet line drawings for recognition. The adjacent words remain the accessible names. */
export function NavIcon({ name }: { name: ViewId | 'device' }) {
  const shared = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.5, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const };
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true" focusable="false" {...shared}>
      {name === 'record' && <><path d="M3 4.5h14M3 8h9M3 11.5h14M3 15h7" /><circle cx="15" cy="15" r="1" fill="currentColor" stroke="none" /></>}
      {name === 'errors' && <><path d="M6 3.5h8l2.5 2.5v8L14 16.5H6L3.5 14V6z" /><path d="M7 10h2l1-2 1.5 4 1-2H14" /></>}
      {name === 'crashes' && <><path d="M10 2.5v3M10 14.5v3M2.5 10h3M14.5 10h3" /><path d="M7 6.5 13 13.5M13 6.5 7 13.5" /></>}
      {name === 'machine' && <><rect x="3" y="3" width="14" height="11" rx="1.5" /><path d="M7 17h6M10 14v3M6 6h8M6 9h5" /></>}
      {name === 'performance' && <><path d="M2.5 14.5h15M3 11.5l3-2 2 1.5 2.5-5 2 3 2.5-1.5 2 2" /><circle cx="10.5" cy="6" r="1" fill="currentColor" stroke="none" /></>}
      {name === 'diagnostics' && <><circle cx="8.5" cy="8.5" r="5.5" /><path d="m12.6 12.6 4 4M5.5 8.5h2l1-2 1.5 4 1-2h1" /></>}
      {name === 'signals' && <><path d="M2.5 13h3l2-5 3 7 2.5-6 1.5 4h3" /><path d="M3 4.5h4" /></>}
      {name === 'stack' && <><path d="m10 3 7 3.5-7 3.5-7-3.5zM3 10l7 3.5 7-3.5M3 13.5 10 17l7-3.5" /></>}
      {name === 'agents' && <><path d="M6 5.5H4.5A1.5 1.5 0 0 0 3 7v6a1.5 1.5 0 0 0 1.5 1.5H6M14 5.5h1.5A1.5 1.5 0 0 1 17 7v6a1.5 1.5 0 0 1-1.5 1.5H14M8 3v14M12 3v14" /></>}
      {name === 'device' && <><rect x="5" y="2.5" width="10" height="15" rx="2" /><path d="M8 5h4M9 14h2" /></>}
    </svg>
  );
}
