import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api } from '../api'
import ChatPane from '../components/ChatPane'
import TripPane from '../components/TripPane'

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
    setTrace(res.trace)
  }, [sessionId])

  useEffect(() => {
    refresh().catch(console.error)
  }, [refresh])

  const send = async (text) => {
    setMessages((m) => [...m, { role: 'user', content: text }])
    setBusy(true)
    try {
      const res = await api.chat(sessionId, text)
      setMessages((m) => [...m, { role: 'assistant', content: res.reply }])
      setState(res.state)
      setTrace(res.trace)
    } catch (e) {
      setMessages((m) => [
        ...m,
        { role: 'assistant', content: `⚠️ ${String(e)}` },
      ])
    } finally {
      setBusy(false)
    }
  }

  if (!state) return <div className="p-8 text-muted">Loading…</div>

  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-border px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="text-2xl">✈️</div>
          <div>
            <div className="font-semibold">{state.origin || 'Trip'} Trip</div>
            <div className="text-xs text-muted">
              {state.start_date} → {state.end_date} ·{' '}
              {state.travellers} traveller{state.travellers > 1 ? 's' : ''}
            </div>
          </div>
        </div>
        <button
          onClick={() => navigate('/')}
          className="text-sm border border-border rounded px-3 py-1 hover:border-accent"
        >
          ← All trips
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
        />
      </main>
    </div>
  )
}