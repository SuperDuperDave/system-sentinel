/**
 * Exact identifiers inside prose (a bug check code, a Windows constant, an event ID) set in the
 * readout face, so 0x133 never reads as "Ox133" in a serif sentence.
 */
const CODE = /(0x[0-9a-fA-F]+|\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b)/g;

export function WithCodes({ text }: { text: string }) {
  const parts = text.split(CODE);
  return <>{parts.map((part, index) => (index % 2 ? <code key={index} className="readout code">{part}</code> : part))}</>;
}
