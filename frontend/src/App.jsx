import { useCallback, useEffect, useRef, useState } from 'react'
import Header from './components/Header.jsx'
import Hero from './components/Hero.jsx'
import Workbench from './components/Workbench.jsx'
import ConfirmPanel from './components/ConfirmPanel.jsx'
import Results from './components/Results.jsx'
import { Footer, Legend, Method } from './components/Sections.jsx'
import { extractProse, getAudit, getHealth, runAudit } from './api.js'
import { SAMPLES } from './samples.js'

// What to tell someone when a request fails, keyed by the API's error code.
function explain(err) {
  switch (err.code) {
    case 'network':
      return {
        title: 'The audit API is not reachable.',
        hint: 'Locally, start it from backend/ with `python tasks.py api`, then try again.',
      }
    case 'extraction_disabled':
      return {
        title: 'Prose extraction is off on this server.',
        hint: 'It needs a Bedrock key. Mermaid and SAM/CloudFormation input work without one.',
      }
    case 'parse_error':
      return {
        title: err.message,
        hint: err.line ? `Check line ${err.line}${err.column ? `, column ${err.column}` : ''}.` : null,
      }
    case 'too_large':
      return { title: 'That input is too large.', hint: err.message }
    case 'timeout':
      return { title: err.message, hint: 'Prose extraction can be slow on a cold start. Try once more.' }
    default:
      return { title: err.message || 'Something went wrong.', hint: null }
  }
}

// Respect the OS "reduce motion" setting. The CSS scroll-behavior rule does not
// cover this: an explicit `behavior: scrollBehavior()` in JS overrides it.
const scrollBehavior = () =>
  window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'

function Toast({ message }) {
  return (
    <div className={`toast ${message ? 'is-on' : ''}`} role="status" aria-live="polite">
      {message}
    </div>
  )
}

export default function App() {
  const [health, setHealth] = useState({ status: 'checking' })
  const [mode, setMode] = useState('mermaid')
  const [content, setContent] = useState(SAMPLES.mermaid[0].content)
  const [phase, setPhase] = useState('idle') // idle | extracting | confirm | auditing | done
  const [draft, setDraft] = useState(null)
  const [report, setReport] = useState(null)
  const [error, setError] = useState(null)
  const [selected, setSelected] = useState(null)
  const [toast, setToast] = useState('')
  const resultsRef = useRef(null)
  const toastTimer = useRef(0)

  const busy = phase === 'extracting' || phase === 'auditing'
  // Review and Q&A need a model; the deterministic audit never does.
  const aiOn = health.status === 'online' && !!health.data?.llm && health.data.llm !== 'none'

  const notify = (msg) => {
    setToast(msg)
    window.clearTimeout(toastTimer.current)
    toastTimer.current = window.setTimeout(() => setToast(''), 2400)
  }

  const refreshHealth = useCallback(() => {
    getHealth()
      .then((data) => setHealth({ status: 'online', data }))
      .catch(() => setHealth({ status: 'offline' }))
  }, [])

  useEffect(() => {
    refreshHealth()
  }, [refreshHealth])

  const showReport = useCallback((r) => {
    setReport(r)
    setSelected(null)
    setPhase('done')
    if (r?.audit_id) {
      const url = new URL(window.location.href)
      url.searchParams.set('audit', r.audit_id)
      window.history.replaceState(null, '', url)
    }
    requestAnimationFrame(() => resultsRef.current?.scrollIntoView({ behavior: scrollBehavior(), block: 'start' }))
  }, [])

  // A shared link: /?audit=<id> reopens a saved drawing.
  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get('audit')
    if (!id) return
    setPhase('auditing')
    getAudit(id)
      .then(showReport)
      .catch(() => {
        setPhase('idle')
        setError({
          title: 'That shared audit could not be found.',
          hint: 'Saved audits on a local server last only as long as the server runs.',
        })
      })
  }, [showReport])

  const fail = (err, fallbackPhase = 'idle') => {
    setError(explain(err))
    setPhase(fallbackPhase)
    if (err.code === 'network') setHealth({ status: 'offline' })
  }

  const run = async (override) => {
    const text = (override?.content ?? content).trim()
    const kind = override?.mode ?? mode
    if (!text || busy) return
    setError(null)
    setDraft(null)

    if (kind === 'prose') {
      setPhase('extracting')
      try {
        setDraft(await extractProse(text))
        setPhase('confirm')
      } catch (err) {
        fail(err)
      }
      return
    }

    setPhase('auditing')
    try {
      showReport(await runAudit({ inputType: kind, content: text }))
      if (health.status !== 'online') refreshHealth()
    } catch (err) {
      fail(err)
    }
  }

  const confirmDraft = async (graph) => {
    setError(null)
    setPhase('auditing')
    try {
      showReport(await runAudit({ graph }))
      setDraft(null)
    } catch (err) {
      fail(err, 'confirm')
    }
  }

  const runDemo = () => {
    const demo = SAMPLES.mermaid[0].content
    setMode('mermaid')
    setContent(demo)
    run({ mode: 'mermaid', content: demo })
  }

  const changeMode = (m) => {
    setMode(m)
    setContent(SAMPLES[m]?.[0]?.content || '')
    setDraft(null)
    setError(null)
    if (phase === 'confirm') setPhase('idle')
  }

  const share = async () => {
    const url = window.location.href
    try {
      await navigator.clipboard.writeText(url)
      notify('Link copied')
    } catch {
      window.prompt('Copy this link', url)
    }
  }

  const download = () => {
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `precedent-${report.audit_id || 'audit'}.json`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  const reset = () => {
    setReport(null)
    setSelected(null)
    setPhase('idle')
    const url = new URL(window.location.href)
    url.searchParams.delete('audit')
    window.history.replaceState(null, '', url)
    document.getElementById('workbench')?.scrollIntoView({ behavior: scrollBehavior() })
  }

  return (
    <>
      <Header health={health} />
      <main>
        <Hero health={health} onDemo={runDemo} busy={busy} />

        <Workbench
          mode={mode}
          setMode={changeMode}
          content={content}
          setContent={setContent}
          onRun={() => run()}
          busy={busy}
          phase={phase}
          health={health}
        >
          {error ? (
            <div className="alert rise" role="alert">
              <strong>{error.title}</strong>
              {error.hint ? <span className="muted">{error.hint}</span> : null}
              <button className="icon-btn" onClick={() => setError(null)} aria-label="Dismiss">
                ×
              </button>
            </div>
          ) : null}

          {draft && (phase === 'confirm' || phase === 'auditing') ? (
            <ConfirmPanel
              draft={draft}
              busy={busy}
              onConfirm={confirmDraft}
              onDiscard={() => {
                setDraft(null)
                setPhase('idle')
              }}
            />
          ) : null}
        </Workbench>

        {report ? (
          <Results
            ref={resultsRef}
            report={report}
            aiOn={aiOn}
            selected={selected}
            onSelect={setSelected}
            onShare={share}
            onDownload={download}
            onPrint={() => window.print()}
            onReset={reset}
          />
        ) : null}

        {/* number sections without a gap, whether or not a drawing is on screen */}
        <Method n={report ? 3 : 2} />
        <Legend n={report ? 4 : 3} />
      </main>
      <Footer health={health} />
      <Toast message={toast} />
    </>
  )
}
