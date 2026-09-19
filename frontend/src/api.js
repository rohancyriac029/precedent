// API client.
//
// Development: /api is proxied to the local FastAPI server (see vite.config.js).
// Deployed:    set VITE_API_BASE to the API Gateway URL at build time.
//
// Errors arrive in two shapes: the Lambda returns {error, message} at the top
// level, FastAPI wraps it as {detail: {error, message}}. Both are normalised
// into ApiError so the UI never shows "[object Object]".

const BASE = (import.meta.env.VITE_API_BASE || '/api').replace(/\/$/, '')
const TIMEOUT_MS = 60_000

export class ApiError extends Error {
  constructor(message, { status = 0, code = 'error', line, column } = {}) {
    super(message)
    this.status = status
    this.code = code
    this.line = line
    this.column = column
  }
}

async function request(path, { method = 'GET', body } = {}) {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS)
  let res
  try {
    res = await fetch(`${BASE}${path}`, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
    })
  } catch (err) {
    clearTimeout(timer)
    if (err.name === 'AbortError') {
      throw new ApiError('The request took too long and was cancelled.', { code: 'timeout' })
    }
    throw new ApiError('Cannot reach the API.', { code: 'network' })
  }
  clearTimeout(timer)

  let data = null
  try {
    data = await res.json()
  } catch {
    data = null
  }

  if (!res.ok) {
    const inner = data?.detail && typeof data.detail === 'object' ? data.detail : data || {}
    const message =
      inner.message ||
      (typeof data?.detail === 'string' ? data.detail : null) ||
      `Request failed with status ${res.status}.`
    throw new ApiError(message, {
      status: res.status,
      code: inner.error || 'error',
      line: inner.line,
      column: inner.column,
    })
  }
  return data
}

// The deployed audit Lambda never calls a model, so its /health always says
// llm: none. Prose extraction is a separate Lambda with its own health route;
// ask it, and fall back to the audit answer if it is unreachable.
export async function getHealth() {
  const [core, extract] = await Promise.all([
    request('/health'),
    request('/extract/health').catch(() => null),
  ])
  return extract?.llm ? { ...core, llm: extract.llm, model: extract.model } : core
}
export const getCorpusStats = () => request('/corpus/stats')
export const getAudit = (id) => request(`/audits/${encodeURIComponent(id)}`)

export function runAudit({ inputType, content, graph }) {
  const body = graph ? { graph } : { input_type: inputType, content }
  return request('/audits', { method: 'POST', body })
}

export function extractProse(content) {
  return request('/extract', { method: 'POST', body: { input_type: 'prose', content } })
}

export const API_BASE = BASE
