import { VerdictGlyph } from './Glyphs.jsx'
import { verdictMeta } from '../lib/verdicts.js'

// A section callout, like the numbered bubbles that key a drawing set:
//   (01) ── WORKBENCH ────────────────────────────|
export function SectionMark({ n, label, id }) {
  return (
    <div className="section-mark" id={id}>
      <span className="section-mark__n">{String(n).padStart(2, '0')}</span>
      <span className="annot">{label}</span>
      <span className="section-mark__rule" aria-hidden="true" />
    </div>
  )
}

export function VerdictBadge({ label, compact = false }) {
  const v = verdictMeta(label)
  return (
    <span className={`badge tone-${v.tone}`} title={v.meaning}>
      <VerdictGlyph name={v.glyph} />
      {compact ? null : v.label}
    </span>
  )
}

export function Stat({ value, label, hint }) {
  return (
    <div className="stat">
      <div className="stat__value">{value}</div>
      <div className="annot">{label}</div>
      {hint ? <div className="stat__hint">{hint}</div> : null}
    </div>
  )
}
