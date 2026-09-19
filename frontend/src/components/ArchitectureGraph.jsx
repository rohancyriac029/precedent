import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  Position,
  BaseEdge,
  EdgeLabelRenderer,
  getSmoothStepPath,
  MarkerType,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import { layoutGraph, NODE_W, NODE_H } from '../lib/layout.js'
import { pairKey, verdictMeta } from '../lib/verdicts.js'
import { CATEGORIES, serviceCategory, serviceTitle } from '../lib/services.js'
import { CategoryGlyph, VerdictGlyph } from './Glyphs.jsx'

// ---------------------------------------------------------------- node

function ServiceNode({ data }) {
  const lr = data.direction === 'LR'
  const cat = serviceCategory(data.service)
  const flags = data.flags || []
  const cls = [
    'svc-node',
    data.dim ? 'is-dim' : '',
    data.active ? 'is-active' : '',
    flags.includes('OVERLAPPING_CAPABILITY') ? 'is-overlap' : '',
    flags.includes('ORPHAN') ? 'is-orphan' : '',
    data.service === 'unknown' ? 'is-unknown' : '',
  ].join(' ')

  return (
    <div className={cls} style={{ width: NODE_W, height: NODE_H }} title={data.note || undefined}>
      <Handle type="target" position={lr ? Position.Left : Position.Top} />
      <div className="svc-node__top">
        <span className="svc-node__glyph">
          <CategoryGlyph category={cat} size={14} />
        </span>
        <span className="annot">{CATEGORIES[cat]}</span>
        {flags.length ? (
          <span className="svc-node__flag" aria-label={flags.join(', ')}>
            {flags.includes('OVERLAPPING_CAPABILITY') ? '?' : '∅'}
          </span>
        ) : null}
      </div>
      <div className="svc-node__title">{serviceTitle(data.service)}</div>
      <div className="svc-node__label">{data.label}</div>
      <Handle type="source" position={lr ? Position.Right : Position.Bottom} />
    </div>
  )
}

// ---------------------------------------------------------------- edge

function VerdictEdge({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, markerEnd }) {
  const meta = verdictMeta(data.verdict?.label)
  const [path, lx, ly] = getSmoothStepPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    borderRadius: 14,
    offset: 22,
  })
  const width = data.active ? 3 : 1.8
  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        markerEnd={markerEnd}
        style={{
          stroke: meta.stroke,
          strokeWidth: width,
          strokeDasharray: meta.dash || undefined,
          opacity: data.dim ? 0.16 : 1,
          transition: 'opacity .2s, stroke-width .2s',
        }}
        interactionWidth={18}
      />
      {data.showLabel ? (
        <EdgeLabelRenderer>
          <button
            className={`edge-pill tone-${meta.tone} ${data.dim ? 'is-dim' : ''} ${data.active ? 'is-active' : ''}`}
            style={{ transform: `translate(-50%, -50%) translate(${lx}px, ${ly}px)` }}
            onClick={() => data.onSelect?.(data.pair)}
            title={`${meta.label}${data.verdict ? ` · ${data.verdict.count} pattern${data.verdict.count === 1 ? '' : 's'}` : ''}`}
          >
            <VerdictGlyph name={meta.glyph} />
            {data.verdict ? <span>{data.verdict.count}</span> : null}
          </button>
        </EdgeLabelRenderer>
      ) : null}
    </>
  )
}

const nodeTypes = { service: ServiceNode }
const edgeTypes = { verdict: VerdictEdge }

// ---------------------------------------------------------------- graph

function useWidth(ref) {
  const [w, setW] = useState(0)
  useEffect(() => {
    if (!ref.current) return undefined
    const ro = new ResizeObserver(([entry]) => setW(entry.contentRect.width))
    ro.observe(ref.current)
    return () => ro.disconnect()
  }, [ref])
  return w
}

export default function ArchitectureGraph({ report, selected, onSelect }) {
  const box = useRef(null)
  const width = useWidth(box)
  const direction = width && width < 700 ? 'TB' : 'LR'

  const { nodes, edges } = useMemo(() => {
    const g = report.graph || { nodes: [], edges: [] }
    const svc = Object.fromEntries(g.nodes.map((n) => [n.id, n.service]))
    const verdicts = Object.fromEntries((report.edges || []).map((v) => [pairKey(v.src_service, v.dst_service), v]))
    const flags = Object.fromEntries((report.nodes || []).map((n) => [n.node_id, n]))

    const selEdgeNodes = new Set()
    if (selected) {
      for (const e of g.edges) {
        if (pairKey(svc[e.src], svc[e.dst]) === selected) {
          selEdgeNodes.add(e.src)
          selEdgeNodes.add(e.dst)
        }
      }
    }

    const pos = layoutGraph(
      g.nodes.map((n) => n.id),
      g.edges,
      { direction },
    )

    const rfNodes = g.nodes.map((n) => ({
      id: n.id,
      type: 'service',
      position: pos[n.id] || { x: 0, y: 0 },
      data: {
        label: n.label,
        service: n.service,
        direction,
        flags: flags[n.id]?.flags,
        note: flags[n.id]?.note,
        dim: Boolean(selected) && !selEdgeNodes.has(n.id),
        active: selEdgeNodes.has(n.id),
      },
    }))

    const seenPair = new Set()
    const rfEdges = g.edges
      .filter((e) => e.src !== e.dst && svc[e.src] && svc[e.dst])
      .map((e, i) => {
        const pair = pairKey(svc[e.src], svc[e.dst])
        const verdict = svc[e.src] === svc[e.dst] ? null : verdicts[pair] || null
        const meta = verdictMeta(verdict?.label)
        const showLabel = !seenPair.has(pair) && Boolean(verdict)
        seenPair.add(pair)
        return {
          id: `e${i}-${e.src}-${e.dst}`,
          source: e.src,
          target: e.dst,
          type: 'verdict',
          markerEnd: { type: MarkerType.ArrowClosed, color: meta.stroke, width: 16, height: 16 },
          data: {
            verdict,
            pair,
            showLabel,
            onSelect,
            dim: Boolean(selected) && pair !== selected,
            active: pair === selected,
          },
        }
      })

    return { nodes: rfNodes, edges: rfEdges }
  }, [report, selected, direction, onSelect])

  const empty = !nodes.length

  return (
    <div className="board frame" ref={box}>
      <div className="board__head">
        <span className="annot">Plan · connections by verdict</span>
        <span className="annot board__hint">Click a connection to inspect it</span>
      </div>
      <div className="board__canvas">
        {empty ? (
          <div className="board__empty muted">No components were found in this input.</div>
        ) : width ? (
          <ReactFlow
            key={`${report.audit_id}-${direction}`}
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            fitView
            fitViewOptions={{ padding: 0.18, maxZoom: 1.1 }}
            minZoom={0.3}
            maxZoom={1.8}
            nodesConnectable={false}
            nodesDraggable={false}
            elementsSelectable={false}
            zoomOnScroll={false}
            preventScrolling={false}
            panOnDrag
            onEdgeClick={(_, edge) => onSelect(edge.data.pair)}
            onPaneClick={() => onSelect(null)}
          >
            <Background id="minor" variant={BackgroundVariant.Lines} gap={24} color="rgba(96,124,152,.07)" />
            <Background id="major" variant={BackgroundVariant.Lines} gap={120} color="rgba(96,124,152,.14)" />
            <Controls showInteractive={false} position="bottom-left" />
          </ReactFlow>
        ) : null}
      </div>
    </div>
  )
}
