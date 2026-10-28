import { laneColor, LANES } from '../lanes.js'

function Json({ value }) {
  if (value === null || value === undefined) return <p className="empty">None</p>
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2)
  return <pre className="json">{text}</pre>
}

export default function StepInspector({ step, onClose, historyUrl }) {
  if (!step) {
    return (
      <section className="panel inspector" aria-label="Step details">
        <header className="panel-head"><h2>Step details</h2></header>
        <p className="empty">Select a step in the timeline to see the exact input and result Temporal recorded.</p>
      </section>
    )
  }
  const lane = LANES.find((l) => l.key === step.lane)
  const duration = step.ended_at && step.started_at ? `${((step.ended_at - step.started_at) / 1000).toFixed(2)}s` : 'in progress'
  return (
    <section className="panel inspector" aria-label="Step details" style={{ '--lane': laneColor(step.lane) }}>
      <header className="panel-head">
        <h2>{step.label}</h2>
        <button type="button" className="btn btn-quiet" onClick={onClose}>Close</button>
      </header>
      <dl className="facts">
        <dt>System</dt><dd>{lane?.label ?? step.lane}</dd>
        <dt>Kind</dt><dd><code>{step.kind}</code>{step.name && step.name !== step.kind ? <> <code>{step.name}</code></> : null}</dd>
        <dt>Status</dt><dd>{step.status}{step.attempt > 1 ? `, attempt ${step.attempt}` : ''}</dd>
        <dt>Duration</dt><dd>{duration}</dd>
        <dt>Event</dt><dd>#{step.event_id}</dd>
      </dl>
      {step.detail ? <p className="detail">{step.detail}</p> : null}
      {step.error ? <p className="error-text">{step.error}</p> : null}
      <h3>Input</h3>
      <Json value={step.input} />
      <h3>Result</h3>
      <Json value={step.output} />
      {historyUrl ? <a className="link" href={historyUrl} target="_blank" rel="noreferrer">Open full history in the Temporal UI</a> : null}
    </section>
  )
}
