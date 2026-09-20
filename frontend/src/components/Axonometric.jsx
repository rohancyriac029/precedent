// An axonometric drawing of a small serverless "campus": each building is a
// service, the ground lines are the connections between them. Cloud
// architecture, drawn as architecture.
//
// Projection is standard isometric: x runs down-right, y runs down-left,
// z runs up. Buildings are placed so that no footprint sits in front of
// another in screen space, then painted back to front.

const C = Math.cos(Math.PI / 6)
const S = 0.5
const U = 34 // pixels per grid unit

const project = (x, y, z = 0) => [(x - y) * C * U, (x + y) * S * U - z * U]
const pts = (...ps) => ps.map((p) => project(...p).map((n) => n.toFixed(1)).join(',')).join(' ')

const WASH = {
  powder: ['#eef5fb', '#d9e8f4', '#bdd5ea'],
  sage: ['#eef6ef', '#d9ebdc', '#bcdcc3'],
  butter: ['#fdf8e6', '#f8edc4', '#ecd894'],
  lavender: ['#f3f0fb', '#e3def5', '#cac1eb'],
  apricot: ['#fdf2e9', '#fbe4d2', '#f1caa9'],
  blush: ['#fcf0ee', '#f8dad5', '#eebdb4'],
}

// Footprint (x, y, w, d) in grid units, height h, optional stacked plates.
// lx / ly place the callout label relative to the roof centre, in pixels,
// always pointing away from the middle of the campus.
const BLOCKS = [
  { id: 's3', x: 0.2, y: 1.8, w: 1.4, d: 1.4, h: 1.6, wash: 'apricot', label: 'S3', lx: -48, ly: -46 },
  { id: 'api', x: 0, y: 4.6, w: 2, d: 1.3, h: 1.0, wash: 'powder', label: 'API Gateway', lx: -54, ly: -30 },
  { id: 'lambda', x: 3.2, y: 2.2, w: 1.2, d: 1.2, h: 3.4, wash: 'sage', label: 'Lambda', lx: 46, ly: -34 },
  { id: 'dynamo', x: 6.4, y: 0.4, w: 1.8, d: 1.8, h: 0.42, plates: 3, gap: 0.2, wash: 'lavender', label: 'DynamoDB', lx: 52, ly: -34 },
  { id: 'events', x: 4.6, y: 4.4, w: 2.4, d: 0.9, h: 0.7, wash: 'butter', label: 'EventBridge', lx: -40, ly: 58 },
  { id: 'sqs', x: 8, y: 4.2, w: 1.8, d: 0.9, h: 0.3, plates: 3, gap: 0.14, wash: 'blush', label: 'SQS', lx: 50, ly: 34 },
]

const FLOWS = [
  ['s3', 'lambda'],
  ['api', 'lambda'],
  ['lambda', 'dynamo'],
  ['lambda', 'events'],
  ['events', 'sqs'],
]

const GRID_X = 10
const GRID_Y = 7

const topZ = (b) => (b.plates || 1) * b.h + ((b.plates || 1) - 1) * (b.gap || 0)
const centre = (b) => [b.x + b.w / 2, b.y + b.d / 2]

function Block({ b, index }) {
  const [top, left, right] = WASH[b.wash]
  const plates = b.plates || 1
  const { x, y, w, d, h } = b
  return (
    <g className="iso-block" style={{ animationDelay: `${140 + index * 90}ms` }}>
      {Array.from({ length: plates }, (_, i) => {
        const z0 = i * (h + (b.gap || 0))
        const z1 = z0 + h
        return (
          <g key={i}>
            <polygon className="iso-face" fill={left} points={pts([x, y + d, z0], [x + w, y + d, z0], [x + w, y + d, z1], [x, y + d, z1])} />
            <polygon className="iso-face" fill={right} points={pts([x + w, y, z0], [x + w, y + d, z0], [x + w, y + d, z1], [x + w, y, z1])} />
            <polygon className="iso-face" fill={top} points={pts([x, y, z1], [x + w, y, z1], [x + w, y + d, z1], [x, y + d, z1])} />
          </g>
        )
      })}
    </g>
  )
}

function calloutGeometry(b) {
  const [cx, cy] = project(...centre(b), topZ(b))
  const tx = cx + b.lx
  const ty = cy + b.ly
  const right = b.lx >= 0
  const tail = right ? 14 : -14
  const textX = tx + (right ? 18 : -18)
  return { cx, cy, tx, ty, tail, textX, right }
}

function Callout({ b }) {
  const g = calloutGeometry(b)
  return (
    <g className="iso-callout">
      <circle cx={g.cx} cy={g.cy} r="2.4" />
      <polyline points={`${g.cx},${g.cy} ${g.tx},${g.ty} ${g.tx + g.tail},${g.ty}`} />
      <text x={g.textX} y={g.ty + 3.5} textAnchor={g.right ? 'start' : 'end'}>
        {b.label.toUpperCase()}
      </text>
    </g>
  )
}

function bounds() {
  const xs = []
  const ys = []
  const add = ([px, py]) => {
    xs.push(px)
    ys.push(py)
  }
  add(project(0, 0))
  add(project(GRID_X, 0))
  add(project(0, GRID_Y))
  add(project(GRID_X, GRID_Y))
  for (const b of BLOCKS) {
    add(project(b.x, b.y, topZ(b)))
    add(project(b.x + b.w, b.y, topZ(b)))
    const g = calloutGeometry(b)
    const textW = b.label.length * 7.4 + 4 // mono, 10px, tracked
    add([g.textX + (g.right ? textW : -textW), g.ty - 8])
    add([g.textX, g.ty + 8])
  }
  const pad = 14
  const minX = Math.min(...xs) - pad
  const minY = Math.min(...ys) - pad
  return [minX, minY, Math.max(...xs) - minX + pad, Math.max(...ys) - minY + pad]
}

export default function Axonometric() {
  const byId = Object.fromEntries(BLOCKS.map((b) => [b.id, b]))
  // painter's order: furthest (smallest x + y) first
  const order = [...BLOCKS].sort((a, b) => a.x + a.y + (a.w + a.d) / 2 - (b.x + b.y + (b.w + b.d) / 2))

  const grid = []
  for (let i = 0; i <= GRID_X; i++) grid.push([[i, 0], [i, GRID_Y]])
  for (let j = 0; j <= GRID_Y; j++) grid.push([[0, j], [GRID_X, j]])

  const vb = bounds()

  return (
    <svg
      className="axo"
      viewBox={vb.map((n) => n.toFixed(0)).join(' ')}
      role="img"
      aria-label="Axonometric drawing of a serverless architecture: S3, API Gateway, Lambda, DynamoDB, EventBridge and SQS drawn as buildings connected across a ground plane."
    >
      <defs>
        <marker id="axo-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0 0 8 4 0 8Z" fill="#7b808a" />
        </marker>
      </defs>

      <g className="iso-grid">
        {grid.map(([a, b], i) => {
          const [x1, y1] = project(...a)
          const [x2, y2] = project(...b)
          return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} />
        })}
      </g>

      <g className="iso-flows">
        {FLOWS.map(([from, to]) => {
          const a = byId[from]
          const b = byId[to]
          const [ax, ay] = project(...centre(a))
          const [bx, by] = project(...centre(b))
          // leave the source and stop at the target's footprint edge, so the
          // line reads as running between buildings rather than under them
          const start = 0.28
          const end = 0.7
          return (
            <line
              key={`${from}-${to}`}
              x1={ax + (bx - ax) * start}
              y1={ay + (by - ay) * start}
              x2={ax + (bx - ax) * end}
              y2={ay + (by - ay) * end}
              markerEnd="url(#axo-arrow)"
            />
          )
        })}
      </g>

      {order.map((b, i) => (
        <Block key={b.id} b={b} index={i} />
      ))}

      <g>{BLOCKS.map((b) => <Callout key={b.id} b={b} />)}</g>
    </svg>
  )
}
