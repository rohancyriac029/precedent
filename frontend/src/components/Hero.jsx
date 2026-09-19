import Axonometric from './Axonometric.jsx'
import { Arrow } from './Glyphs.jsx'
import { Stat } from './Primitives.jsx'

const fmt = (n) => (typeof n === 'number' && n >= 0 ? n.toLocaleString() : '—')

export default function Hero({ health, onDemo, busy }) {
  const d = health.data || {}
  const commit = d.corpus_commit ? d.corpus_commit.slice(0, 7) : '—'
  const rules = typeof d.rules_total === 'number' ? d.rules_total : null

  return (
    <section className="hero" id="top">
      <div className="page hero__grid">
        <div className="hero__copy rise">
          <p className="annot hero__eyebrow">AWS architecture audit · Drawing A-001</p>
          <h1 className="hero__title">
            Every connection, <span className="wash">checked against precedent.</span>
          </h1>
          <p className="lede">
            Paste an AI-generated AWS design. Precedent grounds each service-to-service connection in
            real, deployable AWS patterns, flags what AWS does not support, and drafts a proven route
            around it.
          </p>

          <div className="hero__actions">
            <button className="btn btn-primary" onClick={onDemo} disabled={busy}>
              Run the demo <Arrow />
            </button>
            <a className="btn" href="#workbench">
              Open the workbench
            </a>
          </div>

          <div className="hero__stats" aria-label="Live corpus figures">
            <Stat value={fmt(d.patterns_indexed)} label="Patterns with evidence" />
            <Stat value={fmt(d.vocabulary_services)} label="AWS services mapped" />
            <Stat
              value={rules === null ? '—' : fmt(rules)}
              label="Integration rules"
              hint={
                typeof d.rules_verified === 'number' && rules !== null
                  ? `${d.rules_verified} signed off`
                  : null
              }
            />
            <Stat value={<span className="mono">{commit}</span>} label="Corpus commit" />
          </div>
        </div>

        <figure className="hero__drawing frame rise" style={{ animationDelay: '120ms' }}>
          <Axonometric />
          <figcaption className="drawing-strip">
            <span className="annot">Axonometric</span>
            <span className="annot">Serverless campus</span>
            <span className="annot">Not to scale</span>
          </figcaption>
        </figure>
      </div>
    </section>
  )
}
