import { useEffect, useRef } from 'react'
import { pairKey, sortBySeverity, verdictMeta } from '../lib/verdicts.js'
import { serviceCategory, serviceTitle } from '../lib/services.js'
import { VerdictBadge } from './Primitives.jsx'
import { Arrow, CategoryGlyph, External } from './Glyphs.jsx'

function ServiceTag({ id }) {
  return (
    <span className="svc-tag">
      <CategoryGlyph category={serviceCategory(id)} size={14} />
      {serviceTitle(id)}
    </span>
  )
}

// A repair drawn as a chain of services with dimensioned hops, where each hop
// carries the number of real patterns that prove it.
export function RepairPath({ path }) {
  return (
    <div className="repair">
      <div className="repair__chain">
        {path.services.map((s, i) => (
          <span key={`${s}-${i}`} className="repair__step">
            <ServiceTag id={s} />
            {i < path.services.length - 1 ? (
              <span className="repair__hop" title={`${path.hop_counts[i]} corpus patterns`}>
                <span className="repair__hop-line" aria-hidden="true" />
                <span className="repair__hop-n mono">{path.hop_counts[i]}</span>
              </span>
            ) : null}
          </span>
        ))}
      </div>
      {path.hop_evidence?.some((h) => h.length) ? (
        <div className="repair__evidence">
          {path.hop_evidence.map((hop, i) =>
            hop.length ? (
              <div key={i} className="repair__evidence-hop">
                <span className="annot">
                  {serviceTitle(path.services[i])} → {serviceTitle(path.services[i + 1])}
                </span>
                {hop.slice(0, 2).map((ev) => (
                  <a key={ev.pattern_id} className="link" href={ev.url} target="_blank" rel="noopener noreferrer">
                    {ev.pattern_id}
                  </a>
                ))}
              </div>
            ) : null,
          )}
        </div>
      ) : null}
    </div>
  )
}

function Detail({ v }) {
  const meta = verdictMeta(v.label)
  return (
    <div className="sched__detail">
      <p className="sched__meaning">{meta.meaning}</p>

      {v.rule ? (
        <div className={`note tone-blush`}>
          <strong>Integration rule {v.rule.rule_id}.</strong> {v.rule.note}{' '}
          <a className="link" href={v.rule.doc_url} target="_blank" rel="noopener noreferrer">
            AWS documentation <External />
          </a>
        </div>
      ) : null}

      {v.repairs?.length ? (
        <div className="sched__block">
          <p className="annot">Suggested route{v.repairs.length > 1 ? 's' : ''}</p>
          {v.repairs.map((p, i) => (
            <RepairPath key={i} path={p} />
          ))}
        </div>
      ) : v.label !== 'GROUNDED' ? (
        <p className="muted sched__none">No grounded alternative route exists in this corpus.</p>
      ) : null}

      {v.evidence?.length ? (
        <div className="sched__block">
          <p className="annot">Evidence · {v.count} pattern{v.count === 1 ? '' : 's'}</p>
          <ul className="evidence">
            {v.evidence.map((ev) => (
              <li key={ev.pattern_id}>
                <a className="link" href={ev.url} target="_blank" rel="noopener noreferrer">
                  {ev.title || ev.pattern_id}
                </a>
                <span className="mono muted"> {ev.pattern_id}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  )
}

export default function ConnectionSchedule({ edges, selected, onSelect }) {
  const rows = sortBySeverity(edges)
  const refs = useRef({})

  useEffect(() => {
    if (selected && refs.current[selected]) {
      refs.current[selected].scrollIntoView({
        behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
        block: 'nearest',
      })
    }
  }, [selected])

  if (!rows.length) {
    return <p className="muted">No service-to-service connections were found in this input.</p>
  }

  return (
    <div className="sched" role="table" aria-label="Connection schedule">
      <div className="sched__row sched__row--head" role="row">
        <span role="columnheader" className="annot">Mark</span>
        <span role="columnheader" className="annot">Connection</span>
        <span role="columnheader" className="annot">Relation</span>
        <span role="columnheader" className="annot">Verdict</span>
        <span role="columnheader" className="annot sched__num">Precedent</span>
      </div>
      {rows.map((v, i) => {
        const key = pairKey(v.src_service, v.dst_service)
        const open = selected === key
        const meta = verdictMeta(v.label)
        return (
          <div
            key={key}
            ref={(el) => {
              refs.current[key] = el
            }}
            className={`sched__item tone-${meta.tone} ${open ? 'is-open' : ''}`}
          >
            <button
              className="sched__row"
              role="row"
              aria-expanded={open}
              onClick={() => onSelect(open ? null : key)}
            >
              <span role="cell" className="mono sched__mark">
                C-{String(i + 1).padStart(2, '0')}
              </span>
              <span role="cell" className="sched__conn">
                <ServiceTag id={v.src_service} />
                <Arrow size={12} />
                <ServiceTag id={v.dst_service} />
              </span>
              <span role="cell" className="annot sched__rel">
                {v.edge?.relation?.replaceAll('_', ' ') || '—'}
              </span>
              <span role="cell">
                <VerdictBadge label={v.label} />
              </span>
              <span role="cell" className="mono sched__num">
                {v.count}
                {v.repairs?.length ? <span className="sched__fix">route</span> : null}
              </span>
            </button>
            {open ? <Detail v={v} /> : null}
          </div>
        )
      })}
    </div>
  )
}
