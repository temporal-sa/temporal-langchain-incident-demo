import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from './api.js'
import { configureLlm } from './lanes.js'
import ApprovalCard from './components/ApprovalCard.jsx'
import Conversation from './components/Conversation.jsx'
import FlowGraph from './components/FlowGraph.jsx'
import RunList from './components/RunList.jsx'
import ScenarioRail from './components/ScenarioRail.jsx'
import StatusStrip from './components/StatusStrip.jsx'
import StepInspector from './components/StepInspector.jsx'
import SequenceView from './components/SequenceView.jsx'

const SNAPSHOT_MS = 900
const RUNS_MS = 3000

export default function App() {
  const [config, setConfig] = useState(null)
  const [runs, setRuns] = useState([])
  const [selected, setSelected] = useState(null)
  const [snap, setSnap] = useState(null)
  const [knownStates, setKnownStates] = useState({})
  const [llmMode, setLlmMode] = useState('live')
  const [inspectId, setInspectId] = useState(null)
  const [notice, setNotice] = useState(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.config()
      .then((c) => {
        configureLlm(c.llm)
        setConfig(c)
        setLlmMode(c.llm?.ready ? c.default_llm_mode : 'mock')
      })
      .catch((e) => setNotice({ tone: 'error', text: `Cannot reach the demo API: ${e.message}. Is it running on port 8000?` }))
  }, [])

  const refreshRuns = useCallback(() => api.runs().then((r) => setRuns(r.runs)).catch(() => {}), [])

  useEffect(() => {
    refreshRuns()
    const t = setInterval(refreshRuns, RUNS_MS)
    return () => clearInterval(t)
  }, [refreshRuns])

  useEffect(() => {
    if (!selected) return undefined
    let cancelled = false
    let timer
    const tick = async () => {
      try {
        const s = await api.snapshot(selected)
        if (cancelled) return
        setSnap(s)
        if (s.state) setKnownStates((prev) => ({ ...prev, [selected]: s.state }))
      } catch (e) {
        if (!cancelled) setNotice({ tone: 'error', text: e.message })
      } finally {
        if (!cancelled) timer = setTimeout(tick, SNAPSHOT_MS)
      }
    }
    tick()
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [selected])

  const select = (id) => {
    if (id === selected) return
    setSnap(null)
    setInspectId(null)
    setSelected(id)
  }

  const start = async (scenario) => {
    setBusy(true)
    setNotice(null)
    try {
      const r = await api.startIncident(scenario, llmMode)
      select(r.workflow_id)
      if (r.fault_armed === false) {
        setNotice({ tone: 'warn', text: 'Could not arm the MCP fault. Is the MCP server running? The run will not show retries.' })
      }
      refreshRuns()
    } catch (e) {
      setNotice({ tone: 'error', text: e.message })
    } finally {
      setBusy(false)
    }
  }

  const triage = async (service) => {
    setBusy(true)
    setNotice(null)
    try {
      const r = await api.startTriage(service, llmMode)
      select(r.workflow_id)
      refreshRuns()
    } catch (e) {
      setNotice({ tone: 'error', text: e.message })
    } finally {
      setBusy(false)
    }
  }

  const closeAll = async () => {
    setNotice(null)
    try {
      const r = await api.closeAll()
      const n = r.signaled.length
      setNotice({
        tone: 'info',
        text: n
          ? `Asked ${n} open session${n === 1 ? '' : 's'} to close. Any waiting for approval close once you decide; terminate a stuck one from the CLI.`
          : 'No open sessions.',
      })
      refreshRuns()
    } catch (e) {
      setNotice({ tone: 'error', text: e.message })
    }
  }

  const state = snap?.state ?? (selected ? knownStates[selected] : null) ?? null
  const stale = !!selected && !snap?.state && !!knownStates[selected]
  const running = snap?.status === 'RUNNING'
  const isIncident = snap?.workflow_type === 'IncidentWorkflow'
  const awaiting = running && !!state?.awaiting_approval
  const steps = snap?.steps ?? []

  // The only place the page scrolls programmatically, and only because you clicked.
  const reviewApproval = () => {
    const card = document.getElementById('approval-card')
    if (!card) return
    card.scrollIntoView({ behavior: 'smooth', block: 'center' })
    card.classList.remove('flash')
    void card.offsetWidth
    card.classList.add('flash')
  }
  const inspected = useMemo(() => steps.find((s) => s.id === inspectId) ?? null, [steps, inspectId])

  return (
    <div className="app">
      <header className="masthead">
        <div className="brand">
          <h1>Incident copilot</h1>
          <p className="thesis">Temporal runs the outer loop. LangChain runs each agent turn.</p>
        </div>
        <div className="masthead-links">
          {config ? (
            <a className="link" href={config.temporal_workflows_url} target="_blank" rel="noreferrer">
              Temporal UI, namespace {config.namespace}
            </a>
          ) : null}
        </div>
      </header>

      <aside className="rail">
        {notice ? <p className={`notice notice-${notice.tone}`} role="status">{notice.text}</p> : null}
        <ScenarioRail config={config} llmMode={llmMode} setLlmMode={setLlmMode} onStart={start} onTriage={triage} busy={busy} />
        <section className="rail-section">
          <div className="rail-head">
            <h2>Recent runs</h2>
            <button type="button" className="btn btn-quiet btn-small" onClick={closeAll}>Close open sessions</button>
          </div>
          <p className="hint">Runs are independent workflows; several can be open at once. Sessions close on their own after an idle timeout.</p>
          <RunList runs={runs} selected={selected} onSelect={select} />
        </section>
      </aside>

      <main className="stage">
        {selected ? (
          <StatusStrip snap={snap} state={state} stale={stale} awaiting={awaiting && isIncident} onReviewApproval={reviewApproval} />
        ) : (
          <div className="intro">
            <p>
              Pick a scenario. The graph below is drawn from Temporal’s own event history, so what lights up is what
              actually ran: each model decision, each MCP call, each Java step, each human approval.
            </p>
          </div>
        )}

        <FlowGraph snap={snap} state={state} />

        <SequenceView key={selected ?? 'none'} snap={snap} selectedId={inspectId} onSelect={setInspectId} />

        <div className="lower">
          <Conversation
            key={selected ?? 'none'}
            state={state}
            stale={stale}
            canChat={running && isIncident}
            onSend={(text) => api.message(selected, text)}
            onClose={() => api.close(selected)}
          />
          <div className="side-stack">
            {awaiting && isIncident ? (
              <ApprovalCard proposal={state.proposal} onDecide={(d) => api.approve(selected, d)} />
            ) : null}
            <StepInspector step={inspected} onClose={() => setInspectId(null)} historyUrl={snap?.links?.temporal_ui} />
          </div>
        </div>
      </main>
    </div>
  )
}
