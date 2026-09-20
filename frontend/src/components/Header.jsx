import { External, Mark } from './Glyphs.jsx'
import { API_BASE } from '../api.js'

function StatusPill({ health }) {
  const online = health.status === 'online'
  const checking = health.status === 'checking'
  const llm = health.data?.llm
  const prose = online && llm && llm !== 'none'
  const text = checking ? 'Connecting' : online ? 'API online' : 'API offline'
  return (
    <div
      className={`status ${online ? 'is-online' : checking ? 'is-checking' : 'is-offline'}`}
      role="status"
      aria-live="polite"
      title={
        online
          ? `Corpus ${health.data?.corpus_commit?.slice(0, 7) || ''}. Prose extraction ${prose ? 'on' : 'off'}.`
          : 'The audit API is not reachable.'
      }
    >
      <span className="status__dot" aria-hidden="true" />
      <span>{text}</span>
      {online ? <span className="status__sub">prose {prose ? 'on' : 'off'}</span> : null}
    </div>
  )
}

export default function Header({ health }) {
  const liveHealth = API_BASE.startsWith('http') ? `${API_BASE}/health` : null
  return (
    <header className="site-header">
      <div className="page site-header__inner">
        <a className="brand" href="#top" aria-label="Precedent, back to top">
          <Mark />
          <span className="brand__name">Precedent</span>
          <span className="brand__sheet annot">Sheet A-001</span>
        </a>

        <nav className="site-nav" aria-label="Sections">
          <a href="#workbench">Workbench</a>
          <a href="#method">Method</a>
          <a href="#legend">Legend</a>
        </nav>

        <div className="site-header__end">
          <StatusPill health={health} />
          {liveHealth ? (
            <a className="btn btn-sm btn-quiet" href={liveHealth} target="_blank" rel="noopener noreferrer">
              API <External />
            </a>
          ) : null}
        </div>
      </div>
    </header>
  )
}
