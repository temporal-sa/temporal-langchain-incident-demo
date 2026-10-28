import { SERVICES, statusWord } from '../lanes.js'

// One notice at a time, in a line of fixed height, so nothing above the graph ever
// changes size mid-demo. The most important condition wins.
function notice(snap, state, stale) {
  if (!snap) return { tone: 'quiet', text: 'Loading the run from Temporal…' }
  const steps = snap.steps || []
  const running = snap.status === 'RUNNING'
  const wft = [...steps].reverse().find((s) => s.kind === 'workflow_task')
  const failed = [...steps].reverse().find((s) => s.status === 'failed' && s.kind === 'workflow')
  if (snap.workflow_task?.failing) {
    return { tone: 'error', text: `Workflow task failing on attempt ${snap.workflow_task.attempt}: ${wft?.error ?? 'see worker logs'}. Fix the code and restart the worker; completed steps won’t run again.` }
  }
  if (snap.status === 'FAILED') return { tone: 'error', text: `Workflow failed: ${failed?.error ?? 'see the last step'}` }
  if (running && snap.worker && !snap.worker.responding) {
    return { tone: 'warn', text: 'No worker is answering. Nothing is lost: the session lives in Temporal’s event history and resumes when a worker reconnects.' }
  }
  if (stale) return { tone: 'quiet', text: 'Showing the last state the worker reported.' }
  if (running && state?.awaiting_approval) return { tone: 'human', text: 'Waiting for a person to approve. The workflow holds no thread while it waits.' }
  if (running) return { tone: 'live', text: 'Live. Every step below is drawn from Temporal’s event history.' }
  return { tone: 'quiet', text: `${statusWord(snap.status)}. Select any step to see exactly what Temporal recorded.` }
}

export default function StatusStrip({ snap, state, stale, awaiting, onReviewApproval }) {
  const sa = snap?.search_attributes || {}
  const n = notice(snap, state, stale)
  return (
    <div className="status-strip">
      <div className="status-row">
        <code className="wf-id" title={snap?.workflow_id}>{snap?.workflow_id ?? '…'}</code>
        {snap ? <span className={`pill pill-${snap.status?.toLowerCase()}`}>{statusWord(snap.status)}</span> : null}
        {sa.AgentPhase ? <span className="fact"><span>Phase</span>{sa.AgentPhase}</span> : null}
        {sa.ApprovalStatus && sa.ApprovalStatus !== 'NotRequired' ? <span className="fact"><span>Approval</span>{sa.ApprovalStatus}</span> : null}
        {sa.RemediationAction ? <span className="fact"><span>Action</span>{sa.RemediationAction}</span> : null}
        {state?.llm_mode ? <span className="fact"><span>LLM</span>{state.llm_mode === 'mock' ? 'Scripted' : SERVICES.llm.label}</span> : null}
        {snap ? (
          <span className={`fact worker ${snap.worker?.responding ? 'worker-up' : 'worker-down'}`}>
            <span>Worker</span>{snap.worker?.responding ? 'answering' : 'not answering'}
          </span>
        ) : null}
        <span className="status-actions">
          {awaiting ? (
            <button type="button" className="btn btn-small btn-approval" onClick={onReviewApproval}>Review approval</button>
          ) : null}
          {snap?.links?.temporal_ui ? <a className="link" href={snap.links.temporal_ui} target="_blank" rel="noreferrer">Open in Temporal UI</a> : null}
        </span>
      </div>
      <p className={`notice-line tone-${n.tone}`} role="status" title={n.text}>{n.text}</p>
    </div>
  )
}
