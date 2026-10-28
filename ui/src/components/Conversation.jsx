import { useMemo, useState } from 'react'
import { useFollow } from '../useFollow.js'

function Markdownish({ text }) {
  const lines = (text || '').split('\n')
  return (
    <div className="md">
      {lines.map((line, i) => {
        if (line.startsWith('## ')) return <h4 key={i}>{line.slice(3)}</h4>
        if (line.startsWith('# ')) return <h4 key={i}>{line.slice(2)}</h4>
        if (/^\s*[-*] /.test(line)) return <p key={i} className="md-li">{line.replace(/^\s*[-*] /, '')}</p>
        if (!line.trim()) return null
        return <p key={i}>{line.replace(/\*\*(.+?)\*\*/g, '$1')}</p>
      })}
    </div>
  )
}

function ToolCallChip({ call, result }) {
  const [open, setOpen] = useState(false)
  const status = !result ? 'pending' : result.is_error ? 'error' : 'ok'
  return (
    <div className={`tool-chip tool-${status}`}>
      <button type="button" className="tool-chip-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        <code>{call.name}</code>
        <span className="tool-args">{Object.entries(call.args || {}).map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`).join(', ')}</span>
        <span className="tool-state">{status === 'pending' ? 'running' : status === 'error' ? 'error' : 'result'}</span>
      </button>
      {open && result ? <pre className="tool-result">{prettify(result.content)}</pre> : null}
    </div>
  )
}

function prettify(content) {
  try {
    return JSON.stringify(JSON.parse(content), null, 2)
  } catch {
    return content
  }
}

export default function Conversation({ state, stale, canChat, onSend, onClose }) {
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState(null)

  const messages = state?.messages ?? []
  const results = useMemo(() => {
    const map = {}
    for (const m of messages) if (m.role === 'tool' && m.tool_call_id) map[m.tool_call_id] = m
    return map
  }, [messages])

  // Keep the newest message in view by scrolling this panel only. The old
  // scrollIntoView() would also scroll the page, pulling you away from what you're reading.
  const visible = messages.filter((m) => m.role !== 'tool').length
  const { ref: scroller, unseen, jump } = useFollow(visible)

  const send = async () => {
    const text = draft.trim()
    if (!text) {
      setError('Type a question first.')
      return
    }
    setSending(true)
    setError(null)
    try {
      await onSend(text)
      setDraft('')
    } catch (e) {
      setError(e.message)
    } finally {
      setSending(false)
    }
  }

  return (
    <section className="panel conversation" aria-label="Agent conversation">
      <header className="panel-head">
        <h2>Session</h2>
        <span className="panel-note">
          {stale ? 'Last known state. The worker is not answering queries right now.' : 'Held in workflow state, rebuilt from history after any restart.'}
        </span>
      </header>
      <div className="messages" ref={scroller}>
        {!messages.length ? <p className="empty">The conversation appears here once the agent starts.</p> : null}
        {messages.map((m, i) => {
          if (m.role === 'tool') return null
          if (m.role === 'user') {
            const alert = m.author === 'alert'
            return (
              <div key={i} className={`msg ${alert ? 'msg-alert' : 'msg-human'}`}>
                <div className="msg-who">{alert ? 'Alert' : 'On-call engineer'}<span className="msg-turn">turn {m.turn}</span></div>
                <div className="msg-body">{m.content}</div>
              </div>
            )
          }
          return (
            <div key={i} className="msg msg-agent">
              <div className="msg-who">Agent<span className="msg-turn">turn {m.turn}</span></div>
              {m.content ? <Markdownish text={m.content} /> : null}
              {m.tool_calls?.length ? (
                <div className="tool-calls">
                  {m.tool_calls.map((c) => <ToolCallChip key={c.id} call={c} result={results[c.id]} />)}
                </div>
              ) : null}
            </div>
          )
        })}
        {state?.agent_busy ? <div className="thinking">Agent turn in progress</div> : null}
        {unseen > 0 ? (
          <button type="button" className="jump-latest" onClick={jump}>
            {unseen} new message{unseen === 1 ? '' : 's'}, jump to latest
          </button>
        ) : null}
      </div>
      <div className="composer">
        <label htmlFor="followup" className="sr-only">Ask the agent a follow-up question</label>
        <textarea
          id="followup"
          rows={2}
          value={draft}
          placeholder={canChat ? 'Ask a follow-up, e.g. “What was the root cause?”' : 'Follow-ups are available while the session is running.'}
          disabled={!canChat || sending}
          onChange={(e) => {
            setDraft(e.target.value)
            if (error) setError(null)
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) send()
          }}
        />
        <div className="composer-actions">
          <button type="button" className="btn btn-primary" onClick={send} disabled={!canChat || sending}>
            {sending ? 'Sending' : 'Send question'}
          </button>
          <button type="button" className="btn" onClick={onClose} disabled={!canChat}>Close session</button>
        </div>
        {error ? <p className="field-error" role="alert">{error}</p> : null}
      </div>
    </section>
  )
}
