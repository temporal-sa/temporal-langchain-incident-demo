// One color per system, used identically in the graph and the timeline.
export const LANES = [
  { key: 'workflow', label: 'Temporal workflow', sub: 'Outer loop, Python worker', color: 'var(--lane-temporal)' },
  { key: 'llm', label: 'LangChain + Claude', sub: 'call_llm activity', color: 'var(--lane-llm)' },
  { key: 'mcp', label: 'MCP server', sub: 'ops-tools over HTTP', color: 'var(--lane-mcp)' },
  { key: 'tools', label: 'Tool activities', sub: 'Tickets, paging, checks', color: 'var(--lane-tools)' },
  { key: 'java', label: 'Java worker', sub: 'Spring Boot', color: 'var(--lane-java)' },
  { key: 'human', label: 'People', sub: 'Approvals, questions', color: 'var(--lane-human)' },
]

export const laneColor = (key) => LANES.find((l) => l.key === key)?.color ?? 'var(--ink-faint)'

export const IN_FLIGHT = new Set(['scheduled', 'running', 'retrying'])

export function statusWord(status) {
  switch (status) {
    case 'RUNNING':
      return 'Running'
    case 'COMPLETED':
      return 'Completed'
    case 'FAILED':
      return 'Failed'
    case 'TERMINATED':
      return 'Terminated'
    case 'CANCELED':
      return 'Canceled'
    case 'TIMED_OUT':
      return 'Timed out'
    case 'CONTINUED_AS_NEW':
      return 'Continued as new'
    default:
      return status ?? 'Unknown'
  }
}

export function formatTokens(n) {
  if (!n) return '0'
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)
}

export function timeAgo(ms) {
  if (!ms) return ''
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.round(s / 60)}m ago`
  return `${Math.round(s / 3600)}h ago`
}

// Standard services (outside Temporal) that activities call.
export const SERVICES = {
  llm: { label: 'Claude', sub: 'Anthropic API', color: 'var(--lane-llm)' },
  mcp: { label: 'ops-tools MCP server', sub: 'Separate process, streamable HTTP', color: 'var(--lane-mcp)' },
  ticketing: { label: 'Ticketing', sub: 'Simulated', color: 'var(--lane-tools)' },
  paging: { label: 'Paging', sub: 'Simulated', color: 'var(--lane-tools)' },
  monitoring: { label: 'Monitoring', sub: 'Simulated', color: 'var(--lane-tools)' },
  cmdb: { label: 'CMDB and deploy system', sub: 'Simulated, called from Java', color: 'var(--lane-java)' },
}

// Where LangChain runs: inside these activities.
export function langchainBadge(step) {
  if (step.name === 'call_llm') return step.langchain?.startsWith('Scripted') ? null : 'LangChain'
  if (step.name === 'call_mcp_tool' || step.name === 'discover_mcp_tools') return 'LangChain MCP adapters'
  return null
}

export function activityColor(step) {
  if (step.service === 'llm') return 'var(--lane-llm)'
  if (step.service === 'mcp') return 'var(--lane-mcp)'
  return 'var(--lane-tools)'
}

// The configured LLM provider (from /api/config). Labels across the console read from here,
// so they say Claude, OpenAI, Ollama, and so on, whichever LLM_PROVIDER is set.
export const LLM = { label: 'Claude', chatModel: 'ChatAnthropic' }

export function configureLlm(info) {
  if (!info) return
  LLM.label = info.label || LLM.label
  LLM.chatModel = info.chat_model || LLM.chatModel
  SERVICES.llm.label = LLM.label
  SERVICES.llm.sub = info.service_sub || SERVICES.llm.sub
  const lane = LANES.find((l) => l.key === 'llm')
  if (lane) lane.label = `LangChain + ${LLM.label}`
}

// "ChatOpenAI.bind_tools(...)" -> "ChatOpenAI"
export function chatModelOf(step) {
  const line = step?.langchain || ''
  return line.includes('.bind_tools') ? line.split('.bind_tools')[0] : LLM.chatModel
}
