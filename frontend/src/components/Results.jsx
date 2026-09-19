import { forwardRef } from 'react'
import ArchitectureGraph from './ArchitectureGraph.jsx'
import ConnectionSchedule from './ConnectionSchedule.jsx'
import { SectionMark } from './Primitives.jsx'
import { External, VerdictGlyph } from './Glyphs.jsx'
import { VERDICT_ORDER, tally } from '../lib/verdicts.js'
import { serviceTitle } from '../lib/services.js'

const INPUT_NAMES = { mermaid: 'Mermaid', template: 'SAM / CFN', prose: 'Prose', graph: 'Graph' }

// The title block: the boxed panel in the corner of every architectural sheet
// that says what the drawing is, when it was made, and against what.
function TitleBlock({ report }) {
  const t = report.timings_ms || {}
  const cells = [
    ['Drawing', report.audit_id || '—', true],
    ['Input', INPUT_NAMES[report.graph?.input_type] || '—'],
    ['Corpus commit', report.corpus?.commit ? report.corpus.commit.slice(0, 10) : '—', true],
    ['Fingerprint', report.fingerprint ? `${report.fingerprint.slice(0, 12)}…` : '—', true],
    ['Components', report.graph?.nodes?.length ?? 0],
    ['Audit time', typeof t.total === 'number' ? `${t.total} ms` : '—'],
  ]
  return (
    <dl className="titleblock">
      {cells.map(([k, v, mono]) => (
        <div key={k} className="titleblock__cell">
          <dt className="annot">{k}</dt>
          <dd className={mono ? 'mono' : undefined}>{v}</dd>
        </div>
      ))}
    </dl>
  )
}

function Tally({ edges }) {
  const counts = tally(edges)
  return (
    <ul className="tally" aria-label="Verdict totals">
      {VERDICT_ORDER.map((v) => (
        <li key={v.key} className={`tally__item tone-${v.tone} ${counts[v.key] ? '' : 'is-zero'}`}>
          <span className="tally__n">{counts[v.key]}</span>
          <span className="tally__label">
            <VerdictGlyph name={v.glyph} />
            {v.label}
          </span>
        </li>
      ))}
    </ul>
  )
}

function Findings({ report }) {
  const byId = Object.fromEntries((report.graph?.nodes || []).map((n) => [n.id, n]))
  const items = (report.nodes || []).filter((n) => n.flags?.length)
  if (!items.length) return null
  return (
    <div className="findings">
      {items.map((n) => {
        const overlap = n.flags.includes('OVERLAPPING_CAPABILITY')
        const node = byId[n.node_id]
        return (
          <div key={n.node_id} className={`finding ${overlap ? 'tone-butter' : 'tone-fog'}`}>
            <span className="finding__mark mono">{overlap ? '?' : '∅'}</span>
            <div>
              <p className="finding__title">
                {node?.label || n.node_id}
                <span className="muted"> · {serviceTitle(node?.service)}</span>
              </p>
              <p className="muted">{n.note || (overlap ? 'May overlap in capability.' : 'Not connected.')}</p>
            </div>
          </div>
        )
      })}
    </div>
  )
}

function Precedents({ patterns }) {
  if (!patterns?.length) return <p className="muted">No close patterns were found in the corpus.</p>
  return (
    <div className="precedents">
      {patterns.map((p) => {
        const pct = Math.round((p.containment || 0) * 100)
        return (
          <a key={p.pattern_id} className="precedent" href={p.url} target="_blank" rel="noopener noreferrer">
            <div className="precedent__head">
              <span className="precedent__title">{p.title}</span>
              <External />
            </div>
            <span className="mono muted precedent__id">{p.pattern_id}</span>
            <div className="scalebar" aria-label={`${pct}% of this pattern is in your design`}>
              <div className="scalebar__track">
                <div className="scalebar__fill" style={{ width: `${pct}%` }} />
              </div>
              <span className="mono">{pct}%</span>
            </div>
            <div className="precedent__edges">
              {p.shared_edges.map(([a, b]) => (
                <span key={`${a}-${b}`} className="mini-edge mono">
                  {a} → {b}
                </span>
              ))}
            </div>
          </a>
        )
      })}
    </div>
  )
}

// "General notes": the numbered list every drawing sheet carries.
function Notes({ report }) {
  const notes = [...(report.graph?.warnings || []), ...(report.limitations || [])]
  if (!notes.length) return null
  return (
    <ol className="notes">
      {notes.map((n, i) => (
        <li key={i}>{n}</li>
      ))}
    </ol>
  )
}

const Results = forwardRef(function Results(
  { report, selected, onSelect, onShare, onDownload, onPrint, onReset },
  ref,
) {
  return (
    <section className="section results" ref={ref} aria-labelledby="results-title">
      <div className="page">
        <SectionMark n={2} label="Drawing" id="results" />

        <div className="results__head rise">
          <div>
            <p className="annot">Audit result</p>
            <h2 id="results-title" className="results__summary">
              {report.summary}
            </h2>
          </div>
          <div className="results__actions no-print">
            <button className="btn btn-sm" onClick={onShare}>Copy link</button>
            <button className="btn btn-sm" onClick={onDownload}>JSON</button>
            <button className="btn btn-sm" onClick={onPrint}>Print sheet</button>
            <button className="btn btn-sm btn-quiet" onClick={onReset}>New audit</button>
          </div>
        </div>

        <TitleBlock report={report} />
        <Tally edges={report.edges} />

        <ArchitectureGraph report={report} selected={selected} onSelect={onSelect} />

        <div className="results__block">
          <div className="block-head">
            <h3>Connection schedule</h3>
            <p className="muted">
              Worst first. Open a row for the evidence, the rule behind it, and any grounded route
              around it.
            </p>
          </div>
          <ConnectionSchedule edges={report.edges} selected={selected} onSelect={onSelect} />
        </div>

        {(report.nodes || []).some((n) => n.flags?.length) ? (
          <div className="results__block">
            <div className="block-head">
              <h3>Review notes</h3>
              <p className="muted">Questions, not verdicts. Two services can overlap on purpose.</p>
            </div>
            <Findings report={report} />
          </div>
        ) : null}

        <div className="results__block">
          <div className="block-head">
            <h3>Reference precedents</h3>
            <p className="muted">
              The real patterns your design most resembles, weighted so rare shared connections count
              for more than common ones.
            </p>
          </div>
          <Precedents patterns={report.closest_patterns} />
        </div>

        <div className="results__block">
          <div className="block-head">
            <h3>General notes</h3>
          </div>
          <Notes report={report} />
        </div>
      </div>
    </section>
  )
})

export default Results
