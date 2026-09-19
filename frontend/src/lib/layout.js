// Layered layout for small architecture graphs (typically 3 to 20 nodes).
//
// A trimmed Sugiyama approach, written here rather than pulling in dagre:
//   1. break cycles by reversing DFS back-edges (for layering only)
//   2. assign each node to the longest-path layer from the sources, then
//      pull each node right to sit just before its nearest successor
//   3. order nodes within a layer by the barycentre of their neighbours,
//      sweeping down and up a few times to reduce crossings
//   4. place layers as columns (LR) or rows (TB), centred on each other
//
// Deterministic: the same graph always produces the same drawing, which keeps
// the picture consistent with the audit's own reproducibility guarantee.

export const NODE_W = 196
export const NODE_H = 88

export function layoutGraph(nodeIds, edges, { direction = 'LR' } = {}) {
  const ids = [...nodeIds]
  const index = new Map(ids.map((id, i) => [id, i]))
  const links = edges.filter((e) => e.src !== e.dst && index.has(e.src) && index.has(e.dst))

  // 1. cycle breaking
  const out = new Map(ids.map((id) => [id, []]))
  for (const e of links) out.get(e.src).push(e.dst)
  const state = new Map()
  const reversed = new Set()
  const visit = (u) => {
    state.set(u, 1)
    for (const v of out.get(u)) {
      if (state.get(v) === 1) reversed.add(`${u}>${v}`)
      else if (!state.get(v)) visit(v)
    }
    state.set(u, 2)
  }
  for (const id of ids) if (!state.get(id)) visit(id)

  const dag = links.map((e) =>
    reversed.has(`${e.src}>${e.dst}`) ? { src: e.dst, dst: e.src } : { src: e.src, dst: e.dst },
  )

  // 2. longest-path layering (Kahn order, so it terminates on the DAG)
  const indeg = new Map(ids.map((id) => [id, 0]))
  const succ = new Map(ids.map((id) => [id, []]))
  const pred = new Map(ids.map((id) => [id, []]))
  for (const e of dag) {
    succ.get(e.src).push(e.dst)
    pred.get(e.dst).push(e.src)
    indeg.set(e.dst, indeg.get(e.dst) + 1)
  }
  const layer = new Map(ids.map((id) => [id, 0]))
  const queue = ids.filter((id) => indeg.get(id) === 0)
  const topo = []
  while (queue.length) {
    const u = queue.shift()
    topo.push(u)
    for (const v of succ.get(u)) {
      layer.set(v, Math.max(layer.get(v), layer.get(u) + 1))
      indeg.set(v, indeg.get(v) - 1)
      if (indeg.get(v) === 0) queue.push(v)
    }
  }

  // Longest-path layering puts every source in the first column. A source
  // whose only target sits two columns later then draws its edge straight
  // through the node in between (a DynamoDB stream feeding a Lambda that an
  // API also invokes). Moving each node as far right as its successors allow,
  // latest first, keeps edges short without breaking any predecessor.
  for (let i = topo.length - 1; i >= 0; i--) {
    const u = topo[i]
    const next = succ.get(u)
    if (!next.length) continue
    const limit = Math.min(...next.map((v) => layer.get(v))) - 1
    if (limit > layer.get(u)) layer.set(u, limit)
  }

  // Orphans sit after the last layer, so they read as "not connected".
  const connected = new Set(dag.flatMap((e) => [e.src, e.dst]))
  const maxLayer = Math.max(0, ...ids.filter((id) => connected.has(id)).map((id) => layer.get(id)))
  const orphanLayer = connected.size ? maxLayer + 1 : 0
  for (const id of ids) if (!connected.has(id)) layer.set(id, orphanLayer)

  // 3. ordering within layers
  const layers = []
  for (const id of ids) {
    const l = layer.get(id)
    ;(layers[l] ||= []).push(id)
  }
  const pos = new Map()
  const reindex = () => layers.forEach((row) => row?.forEach((id, i) => pos.set(id, i)))
  reindex()
  const bary = (id, neighbours) => {
    const ns = neighbours.get(id)
    if (!ns.length) return pos.get(id)
    return ns.reduce((s, n) => s + pos.get(n), 0) / ns.length
  }
  for (let sweep = 0; sweep < 4; sweep++) {
    const down = sweep % 2 === 0
    const range = down ? layers.map((_, i) => i) : layers.map((_, i) => layers.length - 1 - i)
    for (const l of range) {
      const row = layers[l]
      if (!row) continue
      const ref = down ? pred : succ
      row.sort((a, b) => bary(a, ref) - bary(b, ref) || index.get(a) - index.get(b))
      row.forEach((id, i) => pos.set(id, i))
    }
  }

  // 4. coordinates
  const lr = direction === 'LR'
  const gapMain = lr ? 118 : 86
  const gapCross = lr ? 34 : 44
  const main = lr ? NODE_W : NODE_H
  const cross = lr ? NODE_H : NODE_W
  const widest = Math.max(1, ...layers.map((r) => r?.length || 0))
  const span = widest * cross + (widest - 1) * gapCross

  const positions = {}
  layers.forEach((row, l) => {
    if (!row) return
    const rowSpan = row.length * cross + (row.length - 1) * gapCross
    const offset = (span - rowSpan) / 2
    row.forEach((id, i) => {
      const m = l * (main + gapMain)
      const c = offset + i * (cross + gapCross)
      positions[id] = lr ? { x: m, y: c } : { x: c, y: m }
    })
  })
  return positions
}
