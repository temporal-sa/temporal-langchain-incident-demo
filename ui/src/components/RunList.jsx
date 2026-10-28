import { statusWord, timeAgo } from '../lanes.js'

export default function RunList({ runs, selected, onSelect }) {
  if (!runs.length) return <p className="empty">No runs yet.</p>
  return (
    <ul className="run-list">
      {runs.map((r) => {
        const sa = r.search_attributes || {}
        return (
          <li key={r.workflow_id}>
            <button
              type="button"
              className={`run ${selected === r.workflow_id ? 'is-selected' : ''}`}
              onClick={() => onSelect(r.workflow_id)}
            >
              <span className={`dot dot-${r.status?.toLowerCase()}`} aria-hidden="true" />
              <span className="run-main">
                <span className="run-title">{sa.DemoScenario === 'triage' ? 'Triage' : sa.DemoScenario ?? r.workflow_type}: {sa.IncidentService}</span>
                <span className="run-sub">{statusWord(r.status)}{sa.AgentPhase ? `, ${sa.AgentPhase}` : ''}, {timeAgo(r.start_time)}</span>
              </span>
            </button>
          </li>
        )
      })}
    </ul>
  )
}
