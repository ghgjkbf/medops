import axios from 'axios'

// Shared API client: JSON envelope {ok, data} unwrapping + inbound API key.
// Key is optional (open mode when the backend has no registered endpoints).
const KEY_STORAGE = 'medops_api_key'

export function getApiKey(): string {
  return localStorage.getItem(KEY_STORAGE) ?? ''
}

export function setApiKey(key: string): void {
  localStorage.setItem(KEY_STORAGE, key)
}

const http = axios.create({ baseURL: '/api/v1', timeout: 15000 })

http.interceptors.request.use((cfg) => {
  const key = getApiKey()
  if (key) cfg.headers['X-API-Key'] = key
  return cfg
})

export async function apiGet<T = any>(path: string, params?: object): Promise<T> {
  const r = await http.get(path, { params })
  return r.data.data as T
}

/** Raw GET returning the full response body (for non-{ok,data} endpoints). */
export async function apiGetRaw<T = any>(path: string, params?: object): Promise<T> {
  const r = await http.get(path, { params })
  return r.data as T
}

export async function apiPost<T = any>(path: string, body?: object): Promise<T> {
  const r = await http.post(path, body)
  return r.data.data as T
}

export async function apiPut<T = any>(path: string, body?: object): Promise<T> {
  const r = await http.put(path, body)
  return r.data.data as T
}

export async function apiPatch<T = any>(path: string, body?: object): Promise<T> {
  const r = await http.patch(path, body)
  return r.data.data as T
}

export async function apiDelete<T = any>(path: string, params?: object): Promise<T> {
  const r = await http.delete(path, { params })
  return r.data.data as T
}

/** Dashboard WebSocket with auto-reconnect (design §9 layer 4). */
export function connectDashboard(onMessage: (msg: any) => void): () => void {
  let ws: WebSocket | null = null
  let closed = false
  let retryMs = 2000

  const open = () => {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const key = getApiKey()
    // Browser WS cannot set headers; key travels as query param (backend also
    // accepts header — parity for external clients).
    const suffix = key ? `?api_key=${encodeURIComponent(key)}` : ''
    ws = new WebSocket(`${proto}://${location.host}/ws/dashboard${suffix}`)
    ws.onmessage = (ev) => {
      try { onMessage(JSON.parse(ev.data)) } catch { /* ignore malformed */ }
    }
    ws.onclose = () => {
      if (!closed) {
        setTimeout(open, retryMs)
        retryMs = Math.min(retryMs * 2, 30000)
      }
    }
    ws.onopen = () => { retryMs = 2000 }
  }
  open()
  return () => { closed = true; ws?.close() }
}

/** Chat WebSocket: single request -> trace events + final answer. */
export function chatOnce(
  sessionId: string,
  message: string,
  onEvent: (msg: any) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${proto}://${location.host}/ws/chat/${sessionId}`)
    ws.onmessage = (ev) => {
      try { onEvent(JSON.parse(ev.data)) } catch { /* ignore */ }
    }
    ws.onclose = () => resolve()
    ws.onerror = () => reject(new Error('chat websocket error'))
    ws.onopen = () => ws.send(JSON.stringify({ message }))
  })
}
