const BASE = '/api'

async function jsonFetch(url, options) {
  const res = await fetch(url, options)
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`${res.status}: ${text}`)
  }
  return res.json()
}

export const api = {
  listSessions: () => jsonFetch(`${BASE}/sessions`),

  createTrip: (body) =>
    jsonFetch(`${BASE}/sessions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),

  getSession: (sid) => jsonFetch(`${BASE}/sessions/${sid}`),

  chat: (sid, message) =>
    jsonFetch(`${BASE}/sessions/${sid}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    }),

  getMap: (sid) => jsonFetch(`${BASE}/sessions/${sid}/map`),

  deleteTrip: (sid) =>
    jsonFetch(`${BASE}/sessions/${sid}`, {
      method: 'DELETE',
    }),
}
