import { SectionMark } from './Primitives.jsx'
import { CategoryGlyph, External, VerdictGlyph } from './Glyphs.jsx'
import { VERDICT_ORDER } from '../lib/verdicts.js'
import { CATEGORIES } from '../lib/services.js'

const STEPS = [
  {
    n: '01',
    title: 'Parse',
    body: 'Templates and Mermaid become a graph of service-to-service connections, without a model. Prose is drafted by one, then confirmed by you.',
  },
  {
    n: '02',
    title: 'Ground',
    body: 'Each connection is counted across real, deployable patterns from the AWS Serverless Patterns Collection, pinned to one commit.',
  },
  {
    n: '03',
    title: 'Check',
    body: 'Connections AWS does not support directly are flagged, but only by rules carrying a documentation link and a reviewer’s sign-off.',
  },
  {
    n: '04',
    title: 'Repair',
    body: 'Where a connection fails, a route through well-trodden connections is drafted, each hop cited to the patterns that prove it.',
  },
]

const PRINCIPLES = [
  [
    'Deterministic.',
    'No model decides a verdict. The same design against the same corpus commit gives the same drawing, fingerprinted to prove it.',
  ],
  [
    'Absence is not impossibility.',
    'A connection nobody has published is labelled “no precedent”, never “novel” and never “broken”.',
  ],
  [
    'Rules need a signature.',
    'An unsupported verdict is a strong claim, so it needs a documentation link and a person who checked it.',
  ],
  [
    'Model output is a draft.',
    'Prose extraction can be wrong, so its graph is always shown to you, and corrected by you, before an audit runs.',
  ],
]

export function Method({ n = 3 }) {
  return (
    <section className="section" aria-labelledby="method-title">
      <div className="page">
        <SectionMark n={n} label="Method" id="method" />
        <div className="section__head">
          <h2 id="method-title">How a design is checked.</h2>
          <p className="muted">Four steps, read left to right, like a section through the building.</p>
        </div>

        <ol className="steps">
          {STEPS.map((s) => (
            <li key={s.n} className="step">
              <span className="step__n mono">{s.n}</span>
              <h3>{s.title}</h3>
              <p className="muted">{s.body}</p>
            </li>
          ))}
        </ol>

        <div className="principles frame">
          <p className="annot">Principles</p>
          <ol className="notes notes--principles">
            {PRINCIPLES.map(([h, b]) => (
              <li key={h}>
                <strong>{h}</strong> {b}
              </li>
            ))}
          </ol>
        </div>
      </div>
    </section>
  )
}

export function Legend({ n = 4 }) {
  return (
    <section className="section" aria-labelledby="legend-title">
      <div className="page">
        <SectionMark n={n} label="Legend" id="legend" />
        <div className="section__head">
          <h2 id="legend-title">Reading the drawing.</h2>
          <p className="muted">Every verdict has a colour, a mark and a name. Colour is never the only signal.</p>
        </div>

        <div className="legend">
          <div className="legend__col">
            <p className="annot">Connections</p>
            <ul className="legend__list">
              {VERDICT_ORDER.map((v) => (
                <li key={v.key} className={`legend__item tone-${v.tone}`}>
                  <svg className="legend__line" viewBox="0 0 44 10" aria-hidden="true">
                    <line
                      x1="2"
                      y1="5"
                      x2="42"
                      y2="5"
                      stroke={v.stroke}
                      strokeWidth="2"
                      strokeDasharray={v.dash || undefined}
                    />
                  </svg>
                  <span className="badge">
                    <VerdictGlyph name={v.glyph} />
                    {v.label}
                  </span>
                  <span className="muted legend__text">{v.meaning}</span>
                </li>
              ))}
            </ul>
          </div>

          <div className="legend__col">
            <p className="annot">Components</p>
            <ul className="legend__symbols">
              {Object.entries(CATEGORIES)
                .filter(([k]) => k !== 'unknown')
                .map(([k, name]) => (
                  <li key={k}>
                    <CategoryGlyph category={k} size={18} />
                    <span>{name}</span>
                  </li>
                ))}
              <li>
                <CategoryGlyph category="unknown" size={18} />
                <span>Unmapped</span>
              </li>
            </ul>
            <div className="legend__flags">
              <p>
                <span className="flag-key tone-butter mono">?</span> Overlapping capability: two services
                share a producer and a consumer.
              </p>
              <p>
                <span className="flag-key tone-fog mono">∅</span> Orphan: nothing connects to it.
              </p>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}

export function Footer({ health }) {
  const commit = health.data?.corpus_commit
  return (
    <footer className="site-footer">
      <div className="page site-footer__inner">
        <div>
          <p className="site-footer__brand">Precedent</p>
          <p className="muted">
            Drawn deterministically. No model decides a verdict.
          </p>
        </div>
        <dl className="site-footer__meta">
          <div>
            <dt className="annot">Corpus</dt>
            <dd>
              <a className="link" href="https://github.com/aws-samples/serverless-patterns" target="_blank" rel="noopener noreferrer">
                AWS Serverless Patterns <External />
              </a>{' '}
              <span className="muted">MIT-0</span>
            </dd>
          </div>
          <div>
            <dt className="annot">Commit</dt>
            <dd className="mono">{commit ? commit.slice(0, 12) : '—'}</dd>
          </div>
          <div>
            <dt className="annot">Source</dt>
            <dd>
              <a className="link" href="https://github.com/rohancyriac029/precedent" target="_blank" rel="noopener noreferrer">
                GitHub <External />
              </a>
            </dd>
          </div>
        </dl>
      </div>
    </footer>
  )
}
