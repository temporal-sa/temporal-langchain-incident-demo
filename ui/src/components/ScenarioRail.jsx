import { useState } from 'react'
import { LLM } from '../lanes.js'

export default function ScenarioRail({ config, llmMode, setLlmMode, onStart, onTriage, busy }) {
  const [triageService, setTriageService] = useState('payments-api')
  if (!config) return <p className="empty">Connecting to the demo API</p>

  const workflowsUrl = config.temporal_workflows_url
  const llm = config.llm || {}
  const noKey = !llm.ready

  return (
    <div className="rail-inner">
      <section className="rail-section">
        <h2>Model</h2>
        <div className="segmented" role="radiogroup" aria-label="Language model">
          <button
            type="button"
            role="radio"
            aria-checked={llmMode === 'live'}
            className={llmMode === 'live' ? 'on' : ''}
            disabled={noKey}
            onClick={() => setLlmMode('live')}
          >
            {LLM.label}
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={llmMode === 'mock'}
            className={llmMode === 'mock' ? 'on' : ''}
            onClick={() => setLlmMode('mock')}
          >
            Scripted
          </button>
        </div>
        <p className="hint">
          {noKey
            ? `${llm.detail || 'No LLM configured'}, so runs use the scripted model. Everything else is real.`
            : llmMode === 'live'
              ? <>Using <code>{llm.model}</code> through LangChain <code>{llm.chat_model}</code>.</>
              : 'Scripted model for rehearsal or as a fallback. Temporal, MCP, and Java stay real.'}
        </p>
      </section>

      <section className="rail-section">
        <h2>Scenarios</h2>
        <ol className="scenarios">
          {config.scenarios.map((s) => (
            <li key={s.key} className="scenario">
              <div className="scenario-text">
                <span className="scenario-title">{s.title}</span>
                <span className="scenario-blurb">{s.blurb}</span>
                <span className="scenario-point">{s.talking_point}</span>
              </div>
              <button type="button" className="btn btn-small" disabled={busy} onClick={() => onStart(s.key)}>
                Run
              </button>
            </li>
          ))}
        </ol>
      </section>

      <section className="rail-section">
        <h2>Short workflow</h2>
        <p className="hint">TriageWorkflow uses the same inner loop with read-only tools and finishes in seconds.</p>
        <div className="inline-form">
          <label htmlFor="triage-service" className="sr-only">Service to triage</label>
          <select id="triage-service" value={triageService} onChange={(e) => setTriageService(e.target.value)}>
            {config.triage_services.map((svc) => <option key={svc} value={svc}>{svc}</option>)}
          </select>
          <button type="button" className="btn btn-small" disabled={busy} onClick={() => onTriage(triageService)}>
            Run triage
          </button>
        </div>
      </section>

      <section className="rail-section">
        <h2>Advanced visibility</h2>
        <p className="hint">Each run upserts search attributes as it moves. These open filtered lists in the Temporal UI.</p>
        <ul className="queries">
          {config.visibility_queries.map((q) => (
            <li key={q.label}>
              <a className="link" href={`${workflowsUrl}?query=${encodeURIComponent(q.query)}`} target="_blank" rel="noreferrer">
                {q.label}
              </a>
              <code className="query-text">{q.query}</code>
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}
