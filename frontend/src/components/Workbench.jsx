import { useRef } from 'react'
import { SAMPLES } from '../samples.js'
import { SectionMark } from './Primitives.jsx'
import { Arrow } from './Glyphs.jsx'

export const MODES = {
  mermaid: {
    label: 'Mermaid',
    short: 'Mermaid',
    hint: 'A flowchart, the way an LLM usually answers.',
    placeholder: 'flowchart LR\n  A[Upload Bucket] --> B[Processor Lambda]\n  B --> C[(Results Table)]',
    code: true,
  },
  template: {
    label: 'SAM / CloudFormation',
    short: 'SAM / CFN',
    hint: 'A template you are about to deploy.',
    placeholder: "AWSTemplateFormatVersion: '2010-09-09'\nTransform: AWS::Serverless-2016-10-31\nResources:\n  ...",
    code: true,
  },
  prose: {
    label: 'Prose',
    short: 'Prose',
    hint: 'A plain-English design, drafted into a graph for you to confirm.',
    placeholder:
      'Users upload receipts to an S3 bucket. Each upload triggers a Lambda that calls Textract and writes the result to DynamoDB...',
    code: false,
  },
}

function CodeEditor({ value, onChange, onSubmit, placeholder, code, label }) {
  const gutter = useRef(null)
  const lines = Math.max(1, value.split('\n').length)

  const onKeyDown = (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault()
      onSubmit()
    }
  }

  return (
    <div className={`editor ${code ? 'is-code' : 'is-prose'}`}>
      {code ? (
        <div className="editor__gutter" ref={gutter} aria-hidden="true">
          {Array.from({ length: lines }, (_, i) => (
            <span key={i}>{i + 1}</span>
          ))}
        </div>
      ) : null}
      <textarea
        className="editor__input"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        onScroll={(e) => {
          if (gutter.current) gutter.current.scrollTop = e.currentTarget.scrollTop
        }}
        placeholder={placeholder}
        spellCheck={!code}
        autoCapitalize="off"
        autoCorrect="off"
        aria-label={label}
        wrap={code ? 'off' : 'soft'}
      />
    </div>
  )
}

export default function Workbench({
  mode,
  setMode,
  content,
  setContent,
  onRun,
  busy,
  phase,
  health,
  children,
}) {
  const m = MODES[mode]
  const lines = content ? content.split('\n').length : 0
  const proseOff = mode === 'prose' && health.status === 'online' && health.data?.llm === 'none'
  const runLabel =
    phase === 'extracting' ? 'Drafting graph' : phase === 'auditing' ? 'Auditing' : mode === 'prose' ? 'Draft graph' : 'Run audit'

  return (
    <section className="section" aria-labelledby="workbench-title">
      <div className="page">
        <SectionMark n={1} label="Workbench" id="workbench" />
        <div className="section__head">
          <h2 id="workbench-title">Put a design on the table.</h2>
          <p className="muted">
            Three ways in. Templates and Mermaid are parsed deterministically; prose is drafted by a
            model and always shown to you before it is audited.
          </p>
        </div>

        <div className="panel frame">
          <div className="panel__bar">
            <div className="tabs" role="tablist" aria-label="Input type">
              {Object.entries(MODES).map(([key, v]) => (
                <button
                  key={key}
                  role="tab"
                  aria-selected={mode === key}
                  className={`tab ${mode === key ? 'is-active' : ''}`}
                  onClick={() => setMode(key)}
                  disabled={busy}
                >
                  <span className="tab__long">{v.label}</span>
                  <span className="tab__short" aria-hidden="true">{v.short}</span>
                </button>
              ))}
            </div>
            <p className="panel__hint muted">{m.hint}</p>
          </div>

          <div className="samples" aria-label="Examples">
            <span className="annot">Load an example</span>
            {(SAMPLES[mode] || []).map((s) => (
              <button key={s.label} className="chip" onClick={() => setContent(s.content)} disabled={busy}>
                {s.label}
              </button>
            ))}
          </div>

          <CodeEditor
            value={content}
            onChange={setContent}
            onSubmit={onRun}
            placeholder={m.placeholder}
            code={m.code}
            label={`${m.label} input`}
          />

          {mode === 'prose' ? (
            <div className={`note ${proseOff ? 'tone-butter' : 'tone-powder'}`}>
              {proseOff ? (
                <>
                  <strong>Prose extraction is off on this server.</strong> It needs a Bedrock key.
                  Mermaid and templates work fully without one.
                </>
              ) : (
                <>
                  Prose is sent to Amazon Bedrock to draft a graph. <strong>Nothing is audited</strong>{' '}
                  until you have reviewed and confirmed that graph.
                </>
              )}
            </div>
          ) : null}

          <div className="panel__foot">
            <span className="annot">
              {lines} {lines === 1 ? 'line' : 'lines'} · {content.length.toLocaleString()} chars
            </span>
            <div className="panel__actions">
              <span className="muted shortcut">
                <span className="kbd">Ctrl</span> <span className="kbd">Enter</span>
              </span>
              <button className="btn btn-quiet" onClick={() => setContent('')} disabled={busy || !content}>
                Clear
              </button>
              <button
                className="btn btn-primary"
                onClick={onRun}
                disabled={busy || !content.trim() || proseOff}
                title={proseOff ? 'This server has no model configured for prose extraction.' : undefined}
              >
                {busy ? <span className="spinner" aria-hidden="true" /> : null}
                {runLabel}
                {busy ? null : <Arrow />}
              </button>
            </div>
          </div>
        </div>

        {children}
      </div>
    </section>
  )
}
