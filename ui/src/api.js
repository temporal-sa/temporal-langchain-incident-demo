// Normally empty: the console calls /api on its own origin, through Vite's proxy.
// Set VITE_API_BASE only when serving the console separately from the API
// (and list the console's origin in the API's CORS_ORIGINS).
const BASE = (import.meta.env?.VITE_API_BASE || '').replace(/\/$/, '')

async function request(path, options = {}) {
  const response = await fetch(BASE + path, {
    headers: { 'content-type': 'application/json' },
    ...options,
  })
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      if (body?.detail) message = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* keep the status text */
    }
    throw new Error(message)
  }
  return response.json()
}

const post = (path, body) => request(path, { method: 'POST', body: JSON.stringify(body ?? {}) })
const runPath = (id) => `/api/runs/${encodeURIComponent(id)}`

export const api = {
  config: () => request('/api/config'),
  runs: () => request('/api/runs'),
  snapshot: (id) => request(`${runPath(id)}/snapshot`),
  startIncident: (scenario, llmMode) => post('/api/incidents', { scenario, llm_mode: llmMode }),
  startTriage: (service, llmMode) => post('/api/triage', { service, llm_mode: llmMode }),
  approve: (id, decision) => post(`${runPath(id)}/approval`, decision),
  message: (id, text) => post(`${runPath(id)}/messages`, { text }),
  close: (id) => post(`${runPath(id)}/close`),
  closeAll: () => post('/api/sessions/close-all'),
}
