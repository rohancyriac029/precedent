import { useEffect, useMemo, useState } from 'react'
import { askAudit, reviewAudit } from '../api.js'
import { serviceTitle } from '../lib/services.js'
import { Arrow, External } from './Glyphs.jsx'

// Everything here is drafted by a model after the audit is finished. It can
// explain verdicts, never change them, and it may only cite patterns the audit
// already cites (the server enforces both). The UI says so in plain words.

// pattern id -> { title, url }, from the report itself and any returned sources
function usePatternMeta(report, extra) {
  return useMemo(() => {
    const meta = {}
    const add = (p) => {
      if (p?.pattern_id && !meta[p.pattern_id]) meta[p.pattern_id] = p
    }
    for (const v of report.edges || []) {
      ;(v.evidence || []).forEach(add)
      for (const r of v.repairs || []) (r.hop_evidence || []).flat().forEach(add)
    }
    ;(report.closest_patterns || []).forEach(add)
    ;(extra || []).forEach(add)
    return meta
  }, [report, extra])
}

function Cites({ ids, meta }) {
  if (!ids?.length) return null
  return (
    <span className="cites">
      {ids.map((id) => (
        <a
          key={id}
          className="cite mono"
          href={meta[id]?.url || undefined}
          target="_blank"
          rel="noopener noreferrer"
          title={meta[id]?.title || id}
        >
          {id}
        </a>
      ))}
    </span>
  )
}

function Points({ points, meta }) {
  return (
    <ol className="notes review__points">
      {points.map((p, i) => (
        <li key={i}>
          {p.text.replaceAll(' -> ', ' → ')} <Cites ids={p.cites} meta={meta} />
        </li>
      ))}
    </ol>
  )
}

// What the model was shown, so a reader can check the answer against it.
function Sources({ sources, meta }) {
  if (!sources?.length) return null
  const patterns = new Set(sources.map((s) => s.pattern_id)).size
  return (
    <details className="sources">
      <summary>
        Read {sources.length} passage{sources.length === 1 ? '' : 's'} from {patterns} pattern
        {patterns === 1 ? '' : 's'}
      </summary>
      <ol>
        {sources.map((s, i) => (
          <li key={`${s.pattern_id}-${i}`}>
            <a href={s.url || meta[s.pattern_id]?.url} target="_blank" rel="noopener noreferrer">
              <span className="mono">{s.pattern_id}</span>
              <External size={11} />
            </a>
            <p>{s.excerpt}</p>
          </li>
        ))}
      </ol>
    </details>
  )
}

function Skeleton({ lines = 3 }) {
  return (
    <div className="skeleton" aria-hidden="true">
      {Array.from({ length: lines }, (_, i) => (
        <span key={i} style={{ width: `${92 - i * 14}%` }} />
      ))}
    </div>
  )
}

function Notes({ notes }) {
  if (!notes?.length) return null
  return <p className="review__fine">{notes.join(' ')}</p>
}

export function ReviewCard({ report }) {
  const [state, setState] = useState({ status: 'loading' })
  const [attempt, setAttempt] = useState(0)
  const meta = usePatternMeta(report, state.data?.sources)

  useEffect(() => {
    let live = true
    setState({ status: 'loading' })
    reviewAudit(report.audit_id)
      .then((data) => live && setState({ status: 'done', data }))
      .catch((err) => live && setState({ status: 'error', error: err.message }))
    return () => {
      live = false
    }
  }, [report.audit_id, attempt])

  return (
    <div className="results__block review-block">
      <div className="block-head">
        <h3>Review</h3>
        <p className="muted">
          Drafted by a model from the finished audit. It explains the verdicts; it cannot change
          them.
        </p>
      </div>

      <div className="review" aria-live="polite" aria-busy={state.status === 'loading'}>
        {state.status === 'loading' ? (
          <>
            <p className="annot review__status">Reading the evidence</p>
            <Skeleton />
          </>
        ) : null}

        {state.status === 'error' ? (
          <p className="muted">
            The review is unavailable right now. The audit above is complete without it.{' '}
            <button className="link" onClick={() => setAttempt((n) => n + 1)}>
              Try again
            </button>
          </p>
        ) : null}

        {state.status === 'done' ? (
          <>
            <p className="review__headline">{state.data.headline}</p>
            {state.data.points?.length ? <Points points={state.data.points} meta={meta} /> : null}
            <div className="review__foot">
              <Sources sources={state.data.sources} meta={meta} />
              <Notes notes={state.data.notes} />
            </div>
          </>
        ) : null}
      </div>
    </div>
  )
}

// Starter questions, built from the report so they are always about this design.
function suggestions(report) {
  const out = []
  const weak = (report.edges || []).find((e) => e.label !== 'GROUNDED')
  if (weak) {
    out.push(
      `Why is ${serviceTitle(weak.src_service)} → ${serviceTitle(weak.dst_service)} flagged, and what should I use instead?`,
    )
  } else if (report.edges?.length) {
    const top = [...report.edges].sort((a, b) => b.count - a.count)[0]
    out.push(
      `What do real patterns add around ${serviceTitle(top.src_service)} → ${serviceTitle(top.dst_service)}?`,
    )
  }
  if ((report.nodes || []).some((n) => n.flags?.includes('OVERLAPPING_CAPABILITY'))) {
    out.push('Which components might overlap, and does it matter?')
  }
  if (report.closest_patterns?.length) out.push('Which real pattern is closest to my design?')
  return out.slice(0, 3)
}

function Answer({ item, report }) {
  const meta = usePatternMeta(report, item.data?.sources)
  if (item.status === 'pending') {
    return (
      <div className="qa__a">
        <Skeleton lines={2} />
      </div>
    )
  }
  if (item.status === 'error') {
    return <p className="qa__a muted">No answer: {item.error}</p>
  }
  const { data } = item
  return (
    <div className={`qa__a ${data.answerable ? '' : 'is-unanswerable'}`}>
      {!data.answerable ? <p className="annot">Not covered by this audit</p> : null}
      <Points points={data.points} meta={meta} />
      <div className="review__foot">
        <Sources sources={data.sources} meta={meta} />
        <Notes notes={data.notes} />
      </div>
    </div>
  )
}

export function AskPanel({ report }) {
  const [question, setQuestion] = useState('')
  const [thread, setThread] = useState([])
  const busy = thread.some((t) => t.status === 'pending')
  const starters = useMemo(() => suggestions(report), [report])

  // a new audit starts a new conversation
  useEffect(() => {
    setThread([])
    setQuestion('')
  }, [report.audit_id])

  const ask = (text) => {
    const q = text.trim()
    if (!q || busy) return
    const id = Date.now()
    setThread((t) => [...t, { id, question: q, status: 'pending' }])
    setQuestion('')
    askAudit(report.audit_id, q)
      .then((data) =>
        setThread((t) => t.map((x) => (x.id === id ? { ...x, status: 'done', data } : x))),
      )
      .catch((err) =>
        setThread((t) =>
          t.map((x) => (x.id === id ? { ...x, status: 'error', error: err.message } : x)),
        ),
      )
  }

  return (
    <div className="results__block">
      <div className="block-head">
        <h3>Ask about this audit</h3>
        <p className="muted">
          Answers draw only on this audit and the READMEs of the patterns it cites. Your question
          is sent to Amazon Bedrock; your design input is not.
        </p>
      </div>

      {thread.length ? (
        <div className="thread" aria-live="polite">
          {thread.map((item) => (
            <article key={item.id} className="qa">
              <p className="qa__q">
                <span className="annot">Q</span>
                <span>{item.question}</span>
              </p>
              <Answer item={item} report={report} />
            </article>
          ))}
        </div>
      ) : null}

      <form
        className="ask no-print"
        onSubmit={(e) => {
          e.preventDefault()
          ask(question)
        }}
      >
        <input
          className="ask__input"
          value={question}
          maxLength={400}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Why was a connection flagged? What should I use instead?"
          aria-label="Question about this audit"
          disabled={busy}
        />
        <button className="btn btn-primary btn-sm" type="submit" disabled={busy || !question.trim()}>
          {busy ? <span className="spinner" aria-hidden="true" /> : null}
          Ask
          {busy ? null : <Arrow />}
        </button>
      </form>

      {!thread.length && starters.length ? (
        <div className="ask__starters no-print">
          {starters.map((s) => (
            <button key={s} type="button" className="chip" onClick={() => ask(s)}>
              {s}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  )
}
