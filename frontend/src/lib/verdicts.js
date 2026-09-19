// Verdict vocabulary. Colour is never the only signal: every verdict also has
// a glyph and a text label, per the plan's accessibility rule for the legend.
//
// `stroke` is duplicated from tokens.css because SVG markers inside React Flow
// take a literal colour attribute, which cannot resolve a CSS variable.

export const VERDICTS = {
  UNSUPPORTED: {
    key: 'UNSUPPORTED',
    label: 'Unsupported',
    tone: 'blush',
    glyph: 'cross',
    stroke: '#D9786D',
    dash: '2 5',
    rank: 0,
    meaning: 'AWS does not support this as a direct integration. Backed by a documentation link.',
  },
  UNPRECEDENTED_IN_CORPUS: {
    key: 'UNPRECEDENTED_IN_CORPUS',
    label: 'No precedent',
    tone: 'lavender',
    glyph: 'ring',
    stroke: '#9186CF',
    dash: '7 5',
    rank: 1,
    meaning: 'Not found in any corpus pattern. Absence from the corpus is not proof it is impossible.',
  },
  RARE: {
    key: 'RARE',
    label: 'Rare',
    tone: 'butter',
    glyph: 'half',
    stroke: '#D4AE45',
    dash: null,
    rank: 2,
    meaning: 'Seen in only one or two real patterns. It works, but few people build it this way.',
  },
  UNKNOWN_SERVICE: {
    key: 'UNKNOWN_SERVICE',
    label: 'Unknown service',
    tone: 'fog',
    glyph: 'question',
    stroke: '#A3A9B3',
    dash: '3 4',
    rank: 3,
    meaning: 'One end is outside the service vocabulary, so no verdict is given.',
  },
  GROUNDED: {
    key: 'GROUNDED',
    label: 'Grounded',
    tone: 'sage',
    glyph: 'check',
    stroke: '#6FAE84',
    dash: null,
    rank: 4,
    meaning: 'Seen in three or more real, deployable AWS patterns.',
  },
}

export const VERDICT_ORDER = Object.values(VERDICTS).sort((a, b) => a.rank - b.rank)

// Edges the audit did not grade, such as weak configuration references.
export const UNGRADED = {
  key: 'UNGRADED',
  label: 'Not graded',
  tone: 'fog',
  glyph: 'dot',
  stroke: '#C6CAD1',
  dash: '1 4',
  rank: 9,
}

export function verdictMeta(label) {
  return VERDICTS[label] || UNGRADED
}

export const pairKey = (src, dst) => `${src}|${dst}`

export function tally(edges = []) {
  const counts = Object.fromEntries(VERDICT_ORDER.map((v) => [v.key, 0]))
  for (const e of edges) if (e.label in counts) counts[e.label] += 1
  return counts
}

export function sortBySeverity(edges = []) {
  return [...edges].sort(
    (a, b) =>
      verdictMeta(a.label).rank - verdictMeta(b.label).rank ||
      a.src_service.localeCompare(b.src_service) ||
      a.dst_service.localeCompare(b.dst_service),
  )
}
