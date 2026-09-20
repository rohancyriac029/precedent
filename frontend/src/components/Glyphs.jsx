// Small line glyphs, drawn at 16x16 with a hairline stroke so they sit in the
// same visual register as the rest of the drawing.

const base = {
  width: 16,
  height: 16,
  viewBox: '0 0 16 16',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.5,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': true,
}

export function VerdictGlyph({ name }) {
  switch (name) {
    case 'check':
      return (
        <svg {...base}>
          <path d="M3.5 8.5 6.5 11.5 12.5 4.5" />
        </svg>
      )
    case 'half':
      return (
        <svg {...base}>
          <circle cx="8" cy="8" r="5" />
          <path d="M8 3a5 5 0 0 1 0 10Z" fill="currentColor" stroke="none" />
        </svg>
      )
    case 'ring':
      return (
        <svg {...base}>
          <circle cx="8" cy="8" r="5" strokeDasharray="2.2 2.2" />
        </svg>
      )
    case 'cross':
      return (
        <svg {...base}>
          <path d="M4.5 4.5l7 7M11.5 4.5l-7 7" />
        </svg>
      )
    case 'question':
      return (
        <svg {...base}>
          <path d="M6 6a2 2 0 1 1 2.8 1.8c-.5.3-.8.7-.8 1.2v.5" />
          <circle cx="8" cy="12" r=".6" fill="currentColor" />
        </svg>
      )
    default:
      return (
        <svg {...base}>
          <circle cx="8" cy="8" r="1.4" fill="currentColor" />
        </svg>
      )
  }
}

// Architectural symbols for service categories, in the spirit of plan-drawing
// legend marks rather than product logos.
export function CategoryGlyph({ category, size = 16 }) {
  const p = { ...base, width: size, height: size, strokeWidth: 1.3 }
  switch (category) {
    case 'compute': // a structural bay with a brace
      return (
        <svg {...p}>
          <rect x="2.5" y="2.5" width="11" height="11" rx="1" />
          <path d="M2.5 13.5 13.5 2.5" />
        </svg>
      )
    case 'edge': // an archway: the way in
      return (
        <svg {...p}>
          <path d="M3 14V8a5 5 0 0 1 10 0v6" />
          <path d="M1.5 14h13" />
        </svg>
      )
    case 'messaging': // parallel ducts with flow
      return (
        <svg {...p}>
          <path d="M2 5.5h9M2 10.5h9" />
          <path d="M10 3.5 13.5 5.5 10 7.5M10 8.5l3.5 2-3.5 2" />
        </svg>
      )
    case 'storage': // a silo in section
      return (
        <svg {...p}>
          <ellipse cx="8" cy="4" rx="5" ry="1.8" />
          <path d="M3 4v8c0 1 2.2 1.8 5 1.8s5-.8 5-1.8V4" />
        </svg>
      )
    case 'database': // stacked floor plates
      return (
        <svg {...p}>
          <path d="M8 2 14 5 8 8 2 5Z" />
          <path d="M2 8l6 3 6-3M2 11l6 3 6-3" />
        </svg>
      )
    case 'analytics': // an elevation of three towers
      return (
        <svg {...p}>
          <path d="M1.5 14.5h13" />
          <rect x="2.5" y="8" width="3" height="6.5" />
          <rect x="6.5" y="4" width="3" height="10.5" />
          <rect x="10.5" y="10" width="3" height="4.5" />
        </svg>
      )
    case 'identity': // a keyhole
      return (
        <svg {...p}>
          <circle cx="8" cy="6" r="3" />
          <path d="M8 9v5M8 12h2" />
        </svg>
      )
    case 'ai': // a hexagonal cell
      return (
        <svg {...p}>
          <path d="M8 1.8 13.4 5v6L8 14.2 2.6 11V5Z" />
          <circle cx="8" cy="8" r="1.6" />
        </svg>
      )
    case 'platform': // a column with capital and base
      return (
        <svg {...p}>
          <path d="M3.5 2.5h9M3.5 13.5h9M5.5 2.5v11M10.5 2.5v11" />
        </svg>
      )
    default: // unmapped: a dashed outline
      return (
        <svg {...p}>
          <rect x="2.5" y="2.5" width="11" height="11" rx="1" strokeDasharray="2 2" />
        </svg>
      )
  }
}

export function Arrow({ size = 14 }) {
  return (
    <svg {...base} width={size} height={size}>
      <path d="M3 8h10M9.5 4.5 13 8l-3.5 3.5" />
    </svg>
  )
}

export function External({ size = 12 }) {
  return (
    <svg {...base} width={size} height={size}>
      <path d="M9 3h4v4M13 3 7.5 8.5M11.5 9.5V13H3V4.5h3.5" />
    </svg>
  )
}

export function Mark() {
  // The logotype mark: an axonometric cube, hairline construction lines visible.
  return (
    <svg viewBox="0 0 32 32" width="28" height="28" aria-hidden="true">
      <path d="M16 3 28 10v12L16 29 4 22V10Z" fill="var(--powder)" stroke="var(--ink)" strokeWidth="1.4" strokeLinejoin="round" />
      <path d="M16 3 28 10 16 17 4 10Z" fill="var(--card)" stroke="var(--ink)" strokeWidth="1.4" strokeLinejoin="round" />
      <path d="M16 17v12" stroke="var(--ink)" strokeWidth="1.4" />
      <path d="M4 22 28 10M4 10l24 12" stroke="var(--ink)" strokeWidth=".6" opacity=".25" />
    </svg>
  )
}
