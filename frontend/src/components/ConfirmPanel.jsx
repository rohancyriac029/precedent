import { useMemo, useState } from 'react'
import { SERVICE_OPTIONS, serviceCategory } from '../lib/services.js'
import { CategoryGlyph, Arrow } from './Glyphs.jsx'

// The control that makes the prose path safe. The model's draft is shown in
// full and can be corrected before a single verdict is computed: the schema
// enum stops invented services, but no model tested kept the right set of
// edges under prompt injection, so a person has the last word.
export default function ConfirmPanel({ draft, onConfirm, onDiscard, busy }) {
  const [nodes, setNodes] = useState(() => draft.nodes || [])
  const [edges, setEdges] = useState(() => draft.edges || [])

  const byId = useMemo(() => Object.fromEntries(nodes.map((n) => [n.id, n])), [nodes])
  const liveEdges = edges.filter((e) => byId[e.src] && byId[e.dst])
  const repairs = draft.repairs_applied || {}
  const changed = (repairs.removed?.length || 0) + (repairs.added?.length || 0)

  const setNode = (id, patch) =>
    setNodes((ns) => ns.map((n) => (n.id === id ? { ...n, ...patch, canon_method: 'user' } : n)))
  const removeNode = (id) => {
    setNodes((ns) => ns.filter((n) => n.id !== id))
    setEdges((es) => es.filter((e) => e.src !== id && e.dst !== id))
  }
  const removeEdge = (i) => setEdges((es) => es.filter((_, j) => j !== i))

  const confirm = () =>
    onConfirm({
      input_type: 'prose',
      nodes,
      edges: liveEdges,
      warnings: draft.warnings || [],
    })

  return (
    <div className="confirm frame rise" role="region" aria-labelledby="confirm-title">
      <div className="confirm__head">
        <div>
          <p className="annot">Review before audit</p>
          <h3 id="confirm-title">Check the drafted graph.</h3>
          <p className="muted">
            A model read your description and drafted this. Correct anything it got wrong. Nothing is
            audited until you confirm.
          </p>
        </div>
        {changed ? (
          <div className="note tone-powder confirm__repairs">
            <strong>{changed} automatic correction{changed === 1 ? '' : 's'}</strong>
            {repairs.removed?.map((r) => (
              <span key={`r-${r}`} className="mono">− {r}</span>
            ))}
            {repairs.added?.map((r) => (
              <span key={`a-${r}`} className="mono">+ {r}</span>
            ))}
          </div>
        ) : null}
      </div>

      <div className="confirm__grid">
        <div>
          <p className="annot confirm__label">Components · {nodes.length}</p>
          <ul className="confirm__list">
            {nodes.map((n) => (
              <li key={n.id} className="confirm__node">
                <span className="confirm__glyph">
                  <CategoryGlyph category={serviceCategory(n.service)} />
                </span>
                <input
                  className="field"
                  value={n.label}
                  onChange={(e) => setNode(n.id, { label: e.target.value })}
                  aria-label={`Name of component ${n.id}`}
                />
                <select
                  className={`field ${n.service === 'unknown' ? 'is-warn' : ''}`}
                  value={n.service}
                  onChange={(e) => setNode(n.id, { service: e.target.value })}
                  aria-label={`AWS service for ${n.label}`}
                >
                  <option value="unknown">Unmapped</option>
                  {SERVICE_OPTIONS.map((g) => (
                    <optgroup key={g.group} label={g.group}>
                      {g.options.map((o) => (
                        <option key={o.id} value={o.id}>
                          {o.title}
                        </option>
                      ))}
                    </optgroup>
                  ))}
                </select>
                <button className="icon-btn" onClick={() => removeNode(n.id)} aria-label={`Remove ${n.label}`}>
                  ×
                </button>
              </li>
            ))}
          </ul>
        </div>

        <div>
          <p className="annot confirm__label">Connections · {liveEdges.length}</p>
          {liveEdges.length ? (
            <ul className="confirm__list">
              {edges.map((e, i) =>
                byId[e.src] && byId[e.dst] ? (
                  <li key={`${e.src}-${e.dst}-${i}`} className="confirm__edge">
                    <span className="confirm__edge-text">
                      <span>{byId[e.src].label}</span>
                      <Arrow size={12} />
                      <span>{byId[e.dst].label}</span>
                    </span>
                    <span className="annot">{e.relation?.replaceAll('_', ' ')}</span>
                    <button
                      className="icon-btn"
                      onClick={() => removeEdge(i)}
                      aria-label={`Remove connection ${byId[e.src].label} to ${byId[e.dst].label}`}
                    >
                      ×
                    </button>
                  </li>
                ) : null,
              )}
            </ul>
          ) : (
            <p className="muted">No connections left to audit.</p>
          )}
        </div>
      </div>

      {draft.warnings?.length ? (
        <ul className="confirm__warnings">
          {draft.warnings.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      ) : null}

      <div className="confirm__foot">
        <button className="btn btn-quiet" onClick={onDiscard} disabled={busy}>
          Discard draft
        </button>
        <button className="btn btn-primary" onClick={confirm} disabled={busy || !liveEdges.length}>
          {busy ? <span className="spinner" aria-hidden="true" /> : null}
          Audit {liveEdges.length} connection{liveEdges.length === 1 ? '' : 's'}
          {busy ? null : <Arrow />}
        </button>
      </div>
    </div>
  )
}
