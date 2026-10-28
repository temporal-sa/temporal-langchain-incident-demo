import { useMemo } from 'react'
import { Handle, MarkerType, Position, ReactFlow } from '@xyflow/react'
import { IN_FLIGHT, SERVICES, chatModelOf, formatTokens } from '../lanes.js'

const RECENT_MS = 3000
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`

// ------------------------------------------------------------- node views --

function Handles() {
  return (
    <>
      <Handle id="l" type="target" position={Position.Left} className="handle" />
      <Handle id="t" type="target" position={Position.Top} className="handle" />
      <Handle id="r" type="source" position={Position.Right} className="handle" />
      <Handle id="b" type="source" position={Position.Bottom} className="handle" />
    </>
  )
}

const KIND_LABEL = { workflow: 'Workflow', activity: 'Activity', service: 'Service', person: 'Person' }

function SystemNode({ data }) {
  return (
    <div className={`sys-node kind-${data.kind} tone-${data.tone}`} style={{ '--lane': data.color }}>
      <Handles />
      <div className="sys-kind">
        <span>{KIND_LABEL[data.kind]}</span>
        {data.worker ? <span className="sys-worker">{data.worker}</span> : null}
        {data.badge ? <span className="tag tag-langchain sys-badge">{data.badge}</span> : null}
      </div>
      <div className="sys-title">{data.title}</div>
      <div className="sys-sub">{data.sub}</div>
      {data.metric ? <div className="sys-metric">{data.metric}</div> : null}
    </div>
  )
}

function LoopNode({ data }) {
  return (
    <div className={`loop-node tone-${data.tone}`}>
      <Handles />
      <div className="loop-head">
        <div className="sys-kind"><span>Workflow</span><span className="sys-worker">Python worker</span></div>
        <span className={`loop-chip tone-${data.tone}`}>{data.chip}</span>
      </div>
      <div className="loop-title">IncidentWorkflow: the outer loop</div>
    </div>
  )
}

function RegionNode({ data }) {
  return (
    <div className={`region region-${data.variant}`}>
      <div className="region-title">{data.title}</div>
      {data.sub ? <div className="region-sub">{data.sub}</div> : null}
    </div>
  )
}

function LabelNode({ data }) {
  return <div className="col-label">{data.text}</div>
}

const nodeTypes = { system: SystemNode, loop: LoopNode, region: RegionNode, label: LabelNode }

// ---------------------------------------------------------- derive status --

function derive(snap, state) {
  const steps = snap?.steps ?? []
  const now = Date.now()
  const running = snap?.status === 'RUNNING'
  const workerDown = !!(running && snap?.worker && !snap.worker.responding)
  const wftFailing = !!snap?.workflow_task?.failing

  const inflight = (s) => IN_FLIGHT.has(s.status)
  const recent = (s) => s.ended_at && now - s.ended_at < RECENT_MS
  const any = (pred, test) => steps.some((s) => pred(s) && test(s))
  const find = (pred, test) => steps.find((s) => pred(s) && test(s))
  const count = (pred) => steps.filter(pred).length

  const is = {
    llm: (s) => s.name === 'call_llm',
    mcp: (s) => s.service === 'mcp',
    ops: (s) => s.service === 'ticketing' || s.service === 'paging',
    opsAgent: (s) => (s.service === 'ticketing' || s.service === 'paging') && s.segment !== 'wftool',
    opsTool: (s) => (s.service === 'ticketing' || s.service === 'paging') && s.segment === 'wftool',
    health: (s) => s.service === 'monitoring',
    child: (s) => s.primitive === 'child_workflow',
    timer: (s) => s.primitive === 'timer',
    wftool: (s) => s.segment === 'wftool',
    inner: (s) => s.segment === 'inner',
    update: (s) => s.primitive === 'update',
    signal: (s) => s.primitive === 'signal',
    start: (s) => s.primitive === 'workflow' && s.name === 'start',
  }

  const tone = (pred) => {
    if (workerDown) return 'stalled'
    if (find(pred, (s) => s.status === 'retrying')) return 'retrying'
    if (any(pred, inflight)) return 'active'
    if (any(pred, recent)) return 'recent'
    return 'idle'
  }
  // A service behind a retrying activity is the thing that is failing.
  const serviceTone = (pred) => {
    if (find(pred, (s) => s.status === 'retrying' || s.status === 'failed')) return 'error'
    const t = tone(pred)
    return t === 'retrying' ? 'error' : t
  }

  const child = [...steps].reverse().find(is.child)
  const childSubs = child?.child_steps ?? []
  const javaLive = childSubs.some((s) => IN_FLIGHT.has(s.status))
  const javaFailed = childSubs.some((s) => s.status === 'failed')
  const lastWft = [...steps].reverse().find((s) => s.primitive === 'workflow_task')
  const awaiting = running && !!state?.awaiting_approval
  const lastLlm = [...steps].reverse().find(is.llm)
  const retryOf = (pred) => find(pred, (s) => s.status === 'retrying')

  let loopTone = 'idle'
  let chip = 'Not started'
  if (snap) {
    if (snap.status === 'FAILED' || snap.status === 'TERMINATED') {
      loopTone = 'error'
      chip = snap.status === 'FAILED' ? 'Failed' : 'Terminated'
    } else if (wftFailing) {
      loopTone = 'error'
      chip = `Workflow task failing, attempt ${snap.workflow_task.attempt}`
    } else if (workerDown) {
      loopTone = 'stalled'
      chip = 'Waiting for a worker'
    } else if (running) {
      loopTone = 'active'
      chip = awaiting ? 'Waiting for approval' : state?.agent_busy ? 'Agent turn running' : 'Session open'
    } else {
      loopTone = 'done'
      chip = 'Completed'
    }
  }

  const tokens = (state?.input_tokens ?? 0) + (state?.output_tokens ?? 0)
  const llmRetry = retryOf(is.llm)
  const mcpRetry = retryOf(is.mcp)
  const proposal = state?.proposal

  const tones = {
    console: workerDown ? 'stalled' : any(is.start, recent) ? 'recent' : 'idle',
    engineer: awaiting ? 'waiting' : tone((s) => is.update(s) || is.signal(s)),
    agent: workerDown ? 'stalled' : any(is.inner, inflight) || state?.agent_busy ? 'active' : 'idle',
    timers: workerDown ? 'stalled' : any(is.timer, inflight) ? 'active' : 'idle',
    remediation: workerDown
      ? 'stalled'
      : wftFailing && lastWft?.segment === 'wftool'
        ? 'error'
        : awaiting
          ? 'waiting'
          : any(is.wftool, inflight)
            ? 'active'
            : 'idle',
    changewf: child?.status === 'failed' ? 'error' : tone(is.child),
    actLlm: tone(is.llm),
    actMcp: tone(is.mcp),
    actOps: tone(is.ops),
    actHealth: tone(is.health),
    actJava: javaFailed ? 'error' : javaLive ? 'active' : child && recent(child) ? 'recent' : 'idle',
    svcLlm: serviceTone(is.llm),
    svcMcp: serviceTone(is.mcp),
    svcTicketing: serviceTone(is.ops),
    svcMonitoring: serviceTone(is.health),
    svcCmdb: javaFailed ? 'error' : javaLive ? 'active' : 'idle',
  }

  const runningTimer = find(is.timer, inflight)
  const lastSub = [...childSubs].reverse().find((s) => s.status !== 'scheduled') ?? childSubs[childSubs.length - 1]
  const metrics = {
    agent: lastLlm
      ? `Turn ${lastLlm.turn}, step ${Number(lastLlm.iteration) + 1}; ${state?.tool_calls ?? 0} tool calls`
      : 'Think, act, observe',
    timers: runningTimer ? runningTimer.label : count(is.timer) ? plural(count(is.timer), 'timer') : 'None yet',
    remediation: awaiting
      ? 'Waiting for a person'
      : proposal
        ? `${proposal.action}, ${proposal.risk} risk, ${state?.approval_status ?? ''}`
        : 'Not called yet',
    changewf: child
      ? child.status === 'failed'
        ? 'Rejected the change'
        : child.status === 'completed'
          ? `${child.output?.change_id ?? 'Change'} completed`
          : 'Running'
      : 'Idle',
    actLlm: llmRetry ? `retrying, attempt ${llmRetry.attempt}` : `${plural(state?.llm_calls ?? count(is.llm), 'call')}, ${formatTokens(tokens)} tokens`,
    actMcp: mcpRetry ? `retrying, attempt ${mcpRetry.attempt} of ${mcpRetry.max_attempts || '?'}` : plural(count(is.mcp), 'call'),
    actOps: plural(count(is.ops), 'call'),
    actHealth: plural(count(is.health), 'check'),
    llmSub: lastLlm?.langchain?.startsWith('Scripted') ? 'Scripted mode: model bypassed' : `${chatModelOf(lastLlm)} with bind_tools`,
    actJava: lastSub
      ? `${lastSub.label}: ${lastSub.status === 'failed' ? 'rejected' : IN_FLIGHT.has(lastSub.status) ? 'running' : 'done'}`
      : 'Idle',
    svcMcp: mcpRetry ? 'Returning 503s' : 'Tools found at run time',
    svcLlm: lastLlm?.langchain?.startsWith('Scripted') ? 'Bypassed in scripted mode' : 'Model decisions',
  }

  const edge = (t) => ({ on: ['active', 'recent', 'waiting'].includes(t), retry: t === 'retrying', error: t === 'error' })
  const edges = {
    start: edge(tones.console),
    ask: edge(tone(is.signal)),
    approve: { on: awaiting || any(is.update, recent) },
    agentLlm: edge(tones.actLlm),
    llmSvc: edge(tones.svcLlm === 'error' ? 'error' : tones.actLlm),
    agentMcp: edge(tones.actMcp),
    mcpServer: edge(tones.svcMcp === 'error' ? 'error' : tones.actMcp),
    agentOps: edge(tone(is.opsAgent)),
    remOps: edge(tone(is.opsTool)),
    opsSvc: edge(tones.actOps),
    remHealth: edge(tones.actHealth),
    healthSvc: edge(tones.actHealth),
    remChild: edge(tones.changewf),
    childJava: edge(tones.actJava),
    javaCmdb: edge(tones.svcCmdb),
  }

  return { tones, metrics, edges, loopTone, chip, workerDown }
}

// ------------------------------------------------------------------ graph --

export default function FlowGraph({ snap, state }) {
  const d = useMemo(() => derive(snap, state), [snap, state])

  const nodes = useMemo(() => {
    const T = d.tones
    const M = d.metrics
    const sys = (id, parentId, x, y, kind, title, sub, color, tone, extra = {}) => ({
      id,
      parentId,
      extent: 'parent',
      type: 'system',
      position: { x, y },
      style: { width: extra.width ?? 210 },
      data: { kind, title, sub, color, tone, metric: extra.metric, badge: extra.badge, worker: extra.worker },
    })
    const region = (id, x, w, variant, title, sub) => ({
      id,
      type: 'region',
      position: { x, y: 0 },
      style: { width: w, height: 600 },
      data: { variant, title, sub },
      selectable: false,
    })
    const label = (id, x, text, width) => ({
      id,
      parentId: 'temporal',
      type: 'label',
      position: { x, y: 58 },
      style: { width },
      data: { text },
      selectable: false,
    })
    return [
      region('people', 0, 200, 'people', 'People'),
      region('temporal', 222, 700, 'temporal', 'Temporal', 'Runs in your workers. Every step is recorded in event history.'),
      region('services', 944, 246, 'services', 'Standard services', 'Outside Temporal'),

      sys('console', 'people', 12, 84, 'person', 'Alert or console', 'Starts IncidentWorkflow', 'var(--lane-human)', T.console, { width: 176 }),
      sys('engineer', 'people', 12, 334, 'person', 'On-call engineer', 'Approves with an Update, asks with a Signal', 'var(--lane-human)', T.engineer, { width: 176 }),

      label('lbl-wf', 20, 'Workflows: orchestration, replayed from history', 340),
      label('lbl-act', 400, 'Activities: the work, with retries', 280),
      {
        id: 'incident',
        parentId: 'temporal',
        extent: 'parent',
        type: 'loop',
        position: { x: 20, y: 84 },
        style: { width: 340, height: 356 },
        data: { tone: d.loopTone, chip: d.chip },
      },
      sys('agent', 'incident', 16, 50, 'workflow', 'Agent loop: the inner loop', 'Each decision and tool call is an activity', 'var(--lane-temporal)', T.agent, { width: 308, metric: M.agent }),
      sys('timers', 'incident', 16, 150, 'workflow', 'Durable timers', 'Approval deadline, verification waits', 'var(--lane-temporal)', T.timers, { width: 308, metric: M.timers }),
      sys('remediation', 'incident', 16, 250, 'workflow', 'Remediation tool', 'propose_remediation, approval gate', 'var(--lane-temporal)', T.remediation, { width: 308, metric: M.remediation }),
      sys('changewf', 'temporal', 20, 496, 'workflow', 'ChangeRequestWorkflow', 'Child workflow, started by name', 'var(--lane-java)', T.changewf, { width: 340, worker: 'Java worker', metric: M.changewf }),

      sys('act-llm', 'temporal', 400, 84, 'activity', 'call_llm', M.llmSub, 'var(--lane-llm)', T.actLlm, { width: 280, badge: 'LangChain', worker: 'Python', metric: M.actLlm }),
      sys('act-mcp', 'temporal', 400, 186, 'activity', 'call_mcp_tool', 'MultiServerMCPClient, discovery too', 'var(--lane-mcp)', T.actMcp, { width: 280, badge: 'LangChain MCP', worker: 'Python', metric: M.actMcp }),
      sys('act-ops', 'temporal', 400, 288, 'activity', 'Ticket and paging tools', 'open_ticket, add_ticket_note, notify_oncall', 'var(--lane-tools)', T.actOps, { width: 280, worker: 'Python', metric: M.actOps }),
      sys('act-health', 'temporal', 400, 390, 'activity', 'check_service_health', 'Post-change verification', 'var(--lane-tools)', T.actHealth, { width: 280, worker: 'Python', metric: M.actHealth }),
      sys('act-java', 'temporal', 400, 496, 'activity', 'Change activities', 'Validate, execute, close', 'var(--lane-java)', T.actJava, { width: 280, worker: 'Java', metric: M.actJava }),

      sys('svc-llm', 'services', 16, 84, 'service', SERVICES.llm.label, SERVICES.llm.sub, 'var(--lane-llm)', T.svcLlm, { width: 214, metric: M.svcLlm }),
      sys('svc-mcp', 'services', 16, 186, 'service', 'ops-tools MCP server', 'Separate process, HTTP', 'var(--lane-mcp)', T.svcMcp, { width: 214, metric: M.svcMcp }),
      sys('svc-ticketing', 'services', 16, 288, 'service', 'Ticketing and paging', 'Simulated', 'var(--lane-tools)', T.svcTicketing, { width: 214 }),
      sys('svc-monitoring', 'services', 16, 390, 'service', 'Monitoring', 'Simulated', 'var(--lane-tools)', T.svcMonitoring, { width: 214 }),
      sys('svc-cmdb', 'services', 16, 496, 'service', 'CMDB and deploys', 'Simulated', 'var(--lane-java)', T.svcCmdb, { width: 214 }),
    ]
  }, [d])

  const edges = useMemo(() => {
    const E = d.edges
    const edge = (id, source, target, color, st = {}, opts = {}) => {
      const stroke = st.error ? 'var(--error)' : st.retry ? 'var(--retry)' : st.on ? color : 'var(--rule-strong)'
      const hot = st.on || st.retry || st.error
      return {
        id,
        source,
        target,
        sourceHandle: opts.from ?? 'r',
        targetHandle: opts.to ?? 'l',
        type: 'smoothstep',
        animated: !!hot && !d.workerDown,
        className: st.retry || st.error ? 'edge-retry' : undefined,
        label: hot ? opts.label : undefined,
        labelStyle: { fill: stroke, fontWeight: 600, fontSize: 12 },
        labelBgStyle: { fill: 'var(--surface)' },
        style: { stroke, strokeWidth: hot ? 2.4 : 1.2 },
        markerEnd: { type: MarkerType.ArrowClosed, color: stroke, width: 16, height: 16 },
      }
    }
    return [
      edge('start', 'console', 'incident', 'var(--lane-human)', E.start, { label: 'start workflow' }),
      edge('ask', 'engineer', 'agent', 'var(--lane-human)', E.ask, { label: 'Signal' }),
      edge('approve', 'engineer', 'remediation', 'var(--lane-human)', E.approve, { label: 'Update' }),
      edge('agent-llm', 'agent', 'act-llm', 'var(--lane-llm)', E.agentLlm),
      edge('llm-svc', 'act-llm', 'svc-llm', 'var(--lane-llm)', E.llmSvc),
      edge('agent-mcp', 'agent', 'act-mcp', 'var(--lane-mcp)', E.agentMcp),
      edge('mcp-server', 'act-mcp', 'svc-mcp', 'var(--lane-mcp)', E.mcpServer),
      edge('agent-ops', 'agent', 'act-ops', 'var(--lane-tools)', E.agentOps),
      edge('rem-ops', 'remediation', 'act-ops', 'var(--lane-tools)', E.remOps),
      edge('ops-svc', 'act-ops', 'svc-ticketing', 'var(--lane-tools)', E.opsSvc),
      edge('rem-health', 'remediation', 'act-health', 'var(--lane-tools)', E.remHealth),
      edge('health-svc', 'act-health', 'svc-monitoring', 'var(--lane-tools)', E.healthSvc),
      edge('rem-child', 'remediation', 'changewf', 'var(--lane-java)', E.remChild, { from: 'b', to: 't', label: 'child workflow' }),
      edge('child-java', 'changewf', 'act-java', 'var(--lane-java)', E.childJava),
      edge('java-cmdb', 'act-java', 'svc-cmdb', 'var(--lane-java)', E.javaCmdb),
    ]
  }, [d])

  return (
    <div className="graph" aria-label="Live architecture graph">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.04 }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        zoomOnScroll={false}
        preventScrolling={false}
        minZoom={0.3}
        maxZoom={1.6}
      />
    </div>
  )
}
