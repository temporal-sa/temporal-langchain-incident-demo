import { useState } from 'react'

export default function ApprovalCard({ proposal, onDecide }) {
  const [reviewer, setReviewer] = useState('alice')
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const decide = async (approved) => {
    if (!reviewer.trim()) {
      setError('Enter a reviewer name first.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await onDecide({ approved, reviewer: reviewer.trim(), comment })
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const label = proposal?.action === 'rollback' ? `Approve rollback to ${proposal.target_version}` : `Approve ${proposal?.action ?? 'change'}`

  return (
    <section id="approval-card" className="panel approval" aria-label="Approval needed">
      <header className="panel-head">
        <h2>Approval needed</h2>
        <span className="panel-note">The workflow is paused on a durable wait. No thread is held.</span>
      </header>
      <dl className="proposal">
        <dt>Action</dt>
        <dd><code>{proposal?.action}</code>{proposal?.target_version ? <> to <code>{proposal.target_version}</code></> : null}</dd>
        <dt>Risk</dt>
        <dd>{proposal?.risk}</dd>
        <dt>Rationale</dt>
        <dd>{proposal?.rationale}</dd>
      </dl>
      <div className="field">
        <label htmlFor="reviewer">Reviewer</label>
        <input id="reviewer" value={reviewer} onChange={(e) => { setReviewer(e.target.value); if (error) setError(null) }} />
      </div>
      <div className="field">
        <label htmlFor="comment">Comment (optional)</label>
        <textarea id="comment" rows={2} value={comment} onChange={(e) => setComment(e.target.value)} />
      </div>
      <div className="approval-actions">
        <button type="button" className="btn btn-primary" disabled={busy} onClick={() => decide(true)}>{label}</button>
        <button type="button" className="btn btn-danger" disabled={busy} onClick={() => decide(false)}>Reject</button>
      </div>
      {error ? <p className="field-error" role="alert">{error}</p> : null}
      <p className="hint">Sent as a Temporal Update. Its validator rejects decisions when nothing is waiting.</p>
    </section>
  )
}
