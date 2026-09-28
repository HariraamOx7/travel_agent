import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api } from '../api'
import ChatPane from '../components/ChatPane'
import TripPane from '../components/TripPane'
import { fmtRange } from '../format'

export default function Builder() {
  const { sessionId } = useParams()
  const navigate = useNavigate()

  const [state, setState] = useState(null)
  const [trace, setTrace] = useState([])
  const [messages, setMessages] = useState([])
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(async () => {
    const res = await api.getSession(sessionId)
    setState(res.state)
    setTrace(res.trace || [])
    if (res.messages && res.messages.length > 0) {
      setMessages(res.messages)
    }
  }, [sessionId])

  useEffect(() => {
    refresh().catch(console.error)
  }, [refresh])

  const send = async (text) => {
    setMessages((m) => [...m, { role: 'user', content: text }])
    setBusy(true)
    try {
      const res = await api.chat(sessionId, text)
      if (res.messages && res.messages.length > 0) {
        setMessages(res.messages)
      } else {
        setMessages((m) => [
          ...m,
          { role: 'assistant', content: res.reply, routing: res.routing },
        ])
      }
      setState(res.state)
      setTrace(res.trace || [])
    } catch (e) {
      setMessages((m) => [
        ...m,
        { role: 'assistant', content: `⚠️ ${String(e)}` },
      ])
    } finally {
      setBusy(false)
    }
  }

  if (!state) {
    return (
      <div className="h-screen flex items-center justify-center">
        <div className="flex flex-col items-center gap-4 text-muted fade-up">
          <span className="inline-grid place-items-center w-11 h-11 rounded-2xl bg-gradient-to-br from-accent to-amber-500 text-black text-xl shadow-glow">
            ✈️
          </span>
          <span className="text-sm">Loading your trip…</span>
        </div>
      </div>
    )
  }

  const title =
    state.destinations?.[0]?.name ||
    state.destination_candidates?.[0]?.name ||
    state.pending_destination_query ||
    'New Trip'
  const dateRange = fmtRange(state.start_date, state.end_date)
  const travellers = state.travellers || 1

  return (
    <div className="h-screen flex flex-col">
      <header className="shrink-0 border-b border-border/70 bg-surface/60 backdrop-blur-md px-4 sm:px-6 py-3 flex items-center justify-between gap-4">
        <div className="flex items-center gap-3 min-w-0">
          <button
            onClick={() => navigate('/')}
            title="All trips"
            aria-label="Back to all trips"
            className="shrink-0 w-9 h-9 grid place-items-center rounded-full border border-border text-muted hover:text-white hover:border-accent/60 transition"
          >
            ←
          </button>
          <span className="hidden sm:grid place-items-center w-9 h-9 rounded-xl bg-gradient-to-br from-accent to-amber-500 text-black text-lg shadow-glow shrink-0">
            ✈️
          </span>
          <div className="min-w-0">
            <div className="font-semibold tracking-tight truncate">{title}</div>
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted mt-1">
              {state.origin && (
                <span className="pill py-0.5">🏠 {state.origin}</span>
              )}
              {dateRange && (
                <span className="pill py-0.5">📅 {dateRange}</span>
              )}
              <span className="pill py-0.5">
                👤 {travellers} traveller{travellers > 1 ? 's' : ''}
              </span>
            </div>
          </div>
        </div>
        <button
          onClick={() => navigate('/')}
          className="hidden sm:block text-sm border border-border rounded-full px-3.5 py-1.5 text-muted hover:text-white hover:border-accent/60 transition shrink-0"
        >
          All trips
        </button>
      </header>

      <main className="flex-1 grid grid-cols-1 lg:grid-cols-[1fr_1.3fr] gap-4 p-4 overflow-hidden">
        <ChatPane
          messages={messages}
          onSend={send}
          busy={busy}
        />
        <TripPane
          sessionId={sessionId}
          state={state}
          trace={trace}
          onStateRefresh={setState}
        />
      </main>
    </div>
  )
}