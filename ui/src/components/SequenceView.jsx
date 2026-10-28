import { useMemo, useState } from 'react'
import { IN_FLIGHT, SERVICES, activityColor, langchainBadge } from '../lanes.js'
import { useFollow, usePreference } from '../useFollow.js'

// Column geometry, in percent of row width. CSS uses the same numbers.
const X = {
  people: 5.5,
  peopleR: 9.9,
  wf: 19.5,
  note: 22.4,
  actL: 29.6,
  actR: 66.4,
  childL: 29.4,
  svcL: 69.9,
}
const Y_CALL = 26
const Y_RETURN = 54

// ---------------------------------------------------------------- grouping --

function buildBlocks(steps, showVisibility) {
  const blocks = []
  let frame = null
  for (const s of steps) {
    if (!showVisibility && s.primitive === 'visibility') continue
    const inLoop = s.segment !== 'outer' && s.turn != null
    if (!inLoop) {
      frame = null
      blocks.push({ type: 'row', step: s })
      continue
    }
    if (!frame || frame.turn !== s.turn) {
      const continued = blocks.some((b) => b.type === 'turn' && b.turn === s.turn)
      frame = { type: 'turn', turn: s.turn, continued, iterations: [] }
      blocks.push(frame)
    }
    let it = frame.iterations[frame.iterations.length - 1]
    if (!it || it.iteration !== s.iteration) {
      it = { iteration: s.iteration, items: [] }
      frame.iterations.push(it)
    }
    const last = it.items[it.items.length - 1]
    if (s.segment === 'wftool') {
      if (last?.type === 'wftool') last.steps.push(s)
      else it.items.push({ type: 'wftool', tool: s.tool_name, steps: [s] })
    } else if (s.role === 'tool') {
      if (last?.type === 'par') last.steps.push(s)
      else it.items.push({ type: 'par', steps: [s] })
    } else {
      it.items.push({ type: 'row', step: s })
    }
  }
  return blocks
}

// ------------------------------------------------------------------ arrows --

function Arrow({ from, to, y, color, dashed, flowing, label }) {
  const right = to > from
  return (
    <g className={flowing ? 'flowing' : undefined}>
      <line x1={`${from}%`} x2={`${to}%`} y1={y} y2={y} stroke={color} strokeWidth="1.6" strokeDasharray={dashed ? '5 4' : undefined} />
      <svg x={`${to}%`} y={y} overflow="visible">
        <path d={right ? 'M-8,-4.5 L0,0 L-8,4.5 Z' : 'M8,-4.5 L0,0 L8,4.5 Z'} fill={color} />
      </svg>
      {label ? (
        <text x={`${(from + to) / 2}%`} y={y - 6} textAnchor="middle" className="arrow-label" fill={color}>
          {label}
        </text>
      ) : null}
    </g>
  )
}

function SelfLoop({ color }) {
  return (
    <svg x={`${X.wf}%`} y={14} overflow="visible">
      <path d="M2,0 H18 V18 H8" fill="none" stroke={color} strokeWidth="1.6" />
      <path d="M2,18 l8,-4.5 v9 z" fill={color} />
    </svg>
  )
}

const live = (s) => IN_FLIGHT.has(s.status)
const statusColor = (s, base) =>
  s.status === 'failed' ? 'var(--error)' : s.status === 'retrying' ? 'var(--retry)' : base

// --------------------------------------------------------------------- rows --

function Attempts({ step }) {
  const n = step.attempt || 1
  if (n <= 1 && step.status !== 'retrying') return null
  const done = step.status === 'completed' || step.status === 'completed_with_error'
  const pips = []
  for (let i = 1; i <= n; i += 1) {
    const last = i === n
    const cls = !last ? 'pip-fail' : done ? 'pip-ok' : step.status === 'failed' ? 'pip-fail' : 'pip-live'
    pips.push(<span key={i} className={`pip ${cls}`} />)
  }
  const text = done
    ? `succeeded on attempt ${n}`
    : step.status === 'failed'
      ? `failed after ${n} attempts`
      : `attempt ${n}${step.max_attempts ? ` of ${step.max_attempts}` : ''}`
  return (
    <span className="attempts" title="Temporal retry policy at work">
      <span className="pips" aria-hidden="true">{pips}</span>
      {text}
    </span>
  )
}

function RowShell({ step, selected, onSelect, gutter, arrows, className = '', children }) {
  return (
    <div
      role="button"
      tabIndex={0}
      className={`seq-row lifelines st-${step.status} ${className}${selected ? ' is-selected' : ''}`}
      onClick={() => onSelect(selected ? null : step.id)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onSelect(selected ? null : step.id)
        }
      }}
    >
      <svg className="seq-svg" aria-hidden="true">{arrows}</svg>
      <div className="c c-people">{gutter}</div>
      {children}
    </div>
  )
}

function callLine(step) {
  const inp = step.input || {}
  if (step.name === 'call_llm') return step.langchain
  if (step.name === 'discover_mcp_tools') return 'MultiServerMCPClient.get_tools()'
  if (step.name === 'call_mcp_tool') {
    const args = Object.entries(inp.args || {}).map(([k, v]) => `${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`)
    return `${inp.tool}(${args.join(', ')})`
  }
  if (step.name === 'check_service_health') return `check_service_health(check=${inp.check_number}/${inp.total_checks})`
  const SKIP = new Set(['note', 'message', 'title', 'incident_id'])
  const args = Object.entries(inp)
    .filter(([k, v]) => !SKIP.has(k) && v !== null && v !== '' && typeof v !== 'object')
    .slice(0, 3)
    .map(([k, v]) => `${k}=${v}`)
  return `${step.name}(${args.join(', ')})`
}

function resultLine(step) {
  if (step.status === 'retrying') return step.error
  if (step.status === 'failed') return step.error
  if (step.name === 'call_llm') return live(step) ? 'The model is deciding' : step.label
  if (live(step)) return 'Running'
  return step.detail || step.label
}

function ActivityRow({ step, selected, onSelect, gutter }) {
  const color = activityColor(step)
  const scripted = step.name === 'call_llm' && step.langchain?.startsWith('Scripted')
  const svc = scripted
    ? { label: 'Scripted model', sub: 'Inside the activity; no API call', color: 'var(--ink-faint)' }
    : SERVICES[step.service] ?? { label: 'Service', sub: '' }
  const badge = langchainBadge(step)
  const svcState = step.status === 'retrying' || step.status === 'failed' ? 'svc-failing' : live(step) ? 'svc-live' : ''
  const arrows = (
    <>
      <Arrow from={X.wf} to={X.actL} y={Y_CALL} color={statusColor(step, color)} flowing={live(step)} />
      <Arrow
        from={X.actR}
        to={X.svcL}
        y={Y_CALL}
        color={step.status === 'retrying' || step.status === 'failed' ? 'var(--error)' : 'var(--ink-soft)'}
        dashed={step.status === 'retrying'}
        flowing={live(step)}
      />
      {!live(step) ? <Arrow from={X.actL} to={X.wf} y={Y_RETURN} color={statusColor(step, color)} dashed /> : null}
    </>
  )
  return (
    <RowShell step={step} selected={selected} onSelect={onSelect} gutter={gutter} arrows={arrows} className="row-activity">
      <div className="c c-wf" />
      <div className="c c-act">
        <div className="act-box" style={{ '--lane': color }}>
          <div className="act-top">
            <span className="tag tag-activity">Activity</span>
            <code className="act-name">{step.name}</code>
            {badge ? <span className="tag tag-langchain">{badge}</span> : null}
            <Attempts step={step} />
          </div>
          <code className="act-call">{callLine(step)}</code>
          <div className="act-result">{resultLine(step)}</div>
        </div>
      </div>
      <div className="c c-svc">
        <div className={`svc-chip ${svcState}`} style={{ '--lane': svc.color }}>
          <span className="svc-name">{svc.label}</span>
          <span className="svc-sub">{step.status === 'retrying' ? 'failing; Temporal retries' : svc.sub}</span>
        </div>
      </div>
    </RowShell>
  )
}

function ChildRow({ step, selected, onSelect, gutter }) {
  const subs = step.child_steps || []
  const color = 'var(--lane-java)'
  const out = step.output || {}
  const arrows = (
    <>
      <Arrow from={X.wf} to={X.childL} y={Y_CALL} color={statusColor(step, color)} flowing={live(step)} label="child workflow" />
      {!live(step) ? <Arrow from={X.childL} to={X.wf} y={Y_RETURN} color={statusColor(step, color)} dashed /> : null}
    </>
  )
  return (
    <RowShell step={step} selected={selected} onSelect={onSelect} gutter={gutter} arrows={arrows} className="row-child">
      <div className="c c-wf" />
      <div className="c c-child">
        <div className="child-box">
          <div className="act-top">
            <span className="tag tag-workflow">Child workflow</span>
            <code className="act-name">{step.name}</code>
            <span className="tag tag-java">Java, Spring Boot worker</span>
          </div>
          <p className="child-sub">
            Started by name on task queue <code>change-management</code>. Inside, the same pattern: a workflow calling activities that call services.
          </p>
          <ol className="child-steps">
            {subs.length === 0 ? <li className="child-empty">{live(step) ? 'Starting on the Java worker' : 'No activity detail available'}</li> : null}
            {subs.map((sub, i) => (
              <li key={i} className={`child-step st-${sub.status}`}>
                <span className="tag tag-activity">Activity</span>
                <span className="child-act">{sub.label}</span>
                <span className="child-arrow" aria-hidden="true" />
                <span className="child-svc">{SERVICES.cmdb.label}</span>
                <span className="child-state">{sub.status === 'failed' ? 'rejected' : live(sub) ? 'running' : 'done'}</span>
                {sub.error ? <span className="child-error">{sub.error}</span> : null}
                {sub.detail && !sub.error ? <span className="child-detail">{sub.detail}</span> : null}
              </li>
            ))}
          </ol>
          <div className="act-result">
            {step.status === 'failed' ? step.error : step.status === 'completed' ? `${out.change_id ?? 'Change'} ${out.status ?? 'completed'}; result returned to Python` : 'Running on the Java worker'}
          </div>
        </div>
      </div>
    </RowShell>
  )
}

const PEOPLE_TAGS = { update: 'Update', signal: 'Signal', workflow: 'Start workflow' }

function PeopleRow({ step, selected, onSelect }) {
  const color = 'var(--lane-human)'
  const who = step.primitive === 'workflow' ? 'Console' : 'On-call'
  const arrows = <Arrow from={X.peopleR} to={X.wf - 0.6} y={Y_CALL} color={statusColor(step, color)} label={PEOPLE_TAGS[step.primitive]} />
  return (
    <RowShell step={step} selected={selected} onSelect={onSelect} arrows={arrows} className="row-people" gutter={<span className="person">{who}</span>}>
      <div className="c c-note">
        <div className="note note-people">
          <span className="tag tag-people">{PEOPLE_TAGS[step.primitive] ?? step.primitive}</span>
          <span className="note-label">{step.label}</span>
          {step.detail ? <span className="note-detail">{step.detail}</span> : null}
          {step.error ? <span className="note-error">{step.error}</span> : null}
        </div>
      </div>
    </RowShell>
  )
}

const NOTE_TAGS = {
  timer: 'Durable timer',
  visibility: 'Search attributes',
  workflow_task: 'Workflow task',
  workflow: 'Workflow',
}

function WorkflowNote({ step, selected, onSelect, gutter, wft }) {
  const color = step.status === 'failed' ? 'var(--error)' : 'var(--lane-temporal)'
  const compact = step.primitive === 'visibility'
  let detail = step.detail
  if (step.primitive === 'workflow_task' && wft?.failing) detail = `Still failing, attempt ${wft.attempt}. ${detail}`
  return (
    <RowShell
      step={step}
      selected={selected}
      onSelect={onSelect}
      gutter={gutter}
      arrows={compact ? null : <SelfLoop color={color} />}
      className={`row-note${compact ? ' row-compact' : ''}`}
    >
      <div className="c c-note">
        <div className={`note note-${step.primitive}`}>
          <span className="tag tag-workflow">{NOTE_TAGS[step.primitive] ?? 'Workflow code'}</span>
          {compact ? <span className="note-values">{detail}</span> : <span className="note-label">{step.label}</span>}
          {detail && !compact ? <span className="note-detail">{detail}</span> : null}
          {step.error ? <span className="note-error">{step.error}</span> : null}
        </div>
      </div>
    </RowShell>
  )
}

function StepRow(props) {
  const { step } = props
  if (step.primitive === 'activity') return <ActivityRow {...props} />
  if (step.primitive === 'child_workflow') return <ChildRow {...props} />
  if (step.primitive === 'update' || step.primitive === 'signal') return <PeopleRow {...props} />
  if (step.primitive === 'workflow' && step.name === 'start') return <PeopleRow {...props} />
  return <WorkflowNote {...props} />
}

// ------------------------------------------------------------------- frames --

function TurnFrame({ block, rowProps }) {
  const followUp = block.turn > 1
  return (
    <div className="frame frame-turn lifelines">
      <div className="frame-head lifelines">
        <span className="frame-tab">loop</span>
        <span className="frame-title">
          Inner loop, agent turn {block.turn}
          {followUp ? ' (follow-up question)' : ''}
          {block.continued ? ', continued' : ''}
        </span>
        <span className="frame-note">The model decides through LangChain. Temporal runs each decision and each tool call as its own activity.</span>
      </div>
      {block.iterations.map((it) => (
        <div key={it.iteration} className="iteration">
          {it.items.map((item, i) => {
            const gutter = i === 0 && item.type === 'row' && item.step.role === 'decide'
              ? <span className="step-no">step {Number(it.iteration) + 1}</span>
              : null
            if (item.type === 'row') return <StepRow key={item.step.id} step={item.step} gutter={gutter} {...rowProps(item.step)} />
            if (item.type === 'par') {
              const rows = item.steps.map((s) => <StepRow key={s.id} step={s} {...rowProps(s)} />)
              return item.steps.length > 1 ? (
                <div key={`par-${item.steps[0].id}`} className="par">
                  <span className="par-tab">par</span>
                  <span className="par-note">{item.steps.length} tool calls run in parallel</span>
                  {rows}
                </div>
              ) : rows
            }
            return (
              <div key={`wf-${item.steps[0].id}`} className="frame frame-wftool lifelines">
                <div className="frame-head lifelines">
                  <span className="frame-tab">tool</span>
                  <span className="frame-title">Workflow tool <code>{item.tool}</code>, inside turn {block.turn}</span>
                  <span className="frame-note">The model asked for a change. Workflow code takes over: policy, approval, the Java change, verification. Then the result goes back to the model.</span>
                </div>
                {item.steps.map((s) => <StepRow key={s.id} step={s} {...rowProps(s)} />)}
              </div>
            )
          })}
        </div>
      ))}
    </div>
  )
}

// --------------------------------------------------------------------- view --

export default function SequenceView({ snap, selectedId, onSelect }) {
  const [showVisibility, setShowVisibility] = usePreference('incident-console.show-visibility', true)
  const [follow, setFollow] = usePreference('incident-console.follow-steps', true)
  const steps = snap?.steps ?? []
  const blocks = useMemo(() => buildBlocks(steps, showVisibility), [steps, showVisibility])
  // Scrolls this panel only, never the page; pauses when you scroll up inside it.
  const { ref: scroller, unseen, jump } = useFollow(steps.length, { enabled: follow })

  const rowProps = (s) => ({ selected: s.id === selectedId, onSelect, wft: snap?.workflow_task })
  const running = snap?.status === 'RUNNING'
  const workerDown = running && snap?.worker && !snap.worker.responding

  return (
    <section className="panel seq-panel" aria-label="Execution, step by step">
      <header className="panel-head">
        <h2>Execution, step by step</h2>
        <div className="panel-controls">
          <label className="toggle" title="Keeps this panel on the newest step. Never scrolls the page.">
            <input type="checkbox" checked={follow} onChange={(e) => { setFollow(e.target.checked); if (e.target.checked) jump() }} />
            Follow new steps
          </label>
          <label className="toggle">
            <input type="checkbox" checked={showVisibility} onChange={(e) => setShowVisibility(e.target.checked)} />
            Show search attribute updates
          </label>
        </div>
      </header>
      <ul className="seq-legend">
        <li><span className="lg lg-workflow" />Workflow: orchestration, replayed from history</li>
        <li><span className="lg lg-activity" />Activity: does the work; retried and recorded</li>
        <li><span className="lg lg-service" />Standard service: called by an activity</li>
        <li><span className="tag tag-langchain">LangChain</span>runs inside these activities</li>
        <li><span className="lg lg-frame" />Frames: <em>loop</em> is an agent turn, <em>tool</em> is workflow code serving a tool call</li>
      </ul>

      {!steps.length ? (
        <p className="empty">Start a scenario to see each step drawn from Temporal’s event history.</p>
      ) : (
        <div className="seq-scroll" ref={scroller}>
          <div className="seq-head">
            <div className="seq-bands">
              <span className="band band-people" />
              <span className="band band-temporal">Temporal: everything in these two columns is recorded in event history</span>
              <span className="band band-services">Outside Temporal</span>
            </div>
            <div className="seq-heads">
              <div className="head"><strong>People</strong><small>Console, on-call</small></div>
              <div className="head head-wf"><strong>{snap?.workflow_type ?? 'Workflow'}</strong><small>Workflow, Python: the outer loop</small></div>
              <div className="head"><strong>Activities</strong><small>Python worker</small></div>
              <div className="head"><strong>Standard services</strong><small>{SERVICES.llm.label}, MCP server, ticketing, …</small></div>
            </div>
          </div>
          <div className="seq-body">
            {blocks.map((b, i) =>
              b.type === 'turn'
                ? <TurnFrame key={`turn-${b.turn}-${i}`} block={b} rowProps={rowProps} />
                : <StepRow key={b.step.id} step={b.step} {...rowProps(b.step)} />,
            )}
            {unseen > 0 ? (
              <button type="button" className="jump-latest" onClick={jump}>
                {unseen} new step{unseen === 1 ? '' : 's'}, jump to latest
              </button>
            ) : null}
            {workerDown ? (
              <div className="seq-row lifelines row-note row-stalled">
                <div className="c c-people" />
                <div className="c c-note">
                  <div className="note note-stalled">
                    <span className="tag tag-workflow">Waiting for a worker</span>
                    <span className="note-label">Paused, not lost</span>
                    <span className="note-detail">No worker is answering. The workflow’s state is in event history, and it resumes from here when a worker reconnects.</span>
                  </div>
                </div>
              </div>
            ) : null}
          </div>
        </div>
      )}
    </section>
  )
}
