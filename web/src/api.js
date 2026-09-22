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

  editIdeas: (sid, exclude, include) =>
    jsonFetch(`${BASE}/sessions/${sid}/ideas`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ exclude, include }),
    }),

  moveStop: (sid, stop_name, from_day, to_day, to_index) =>
    jsonFetch(`${BASE}/sessions/${sid}/itinerary/move`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stop_name, from_day, to_day, to_index }),
    }),

  deleteTrip: (sid) =>
    jsonFetch(`${BASE}/sessions/${sid}`, {
      method: 'DELETE',
    }),
}
