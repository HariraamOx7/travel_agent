import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { fmtRange } from '../format'

const TRIP_EMOJI = ['🏔️', '🏖️', '🏙️', '🌴', '🏜️', '⛩️', '🌋', '❄️']

function stageLabel(stage) {
  return String(stage || 'draft').replace(/_/g, ' ')
}

export default function Landing() {
  const [sessions, setSessions] = useState([])
  const [creating, setCreating] = useState(false)
  const [deletingId, setDeletingId] = useState(null)
  const navigate = useNavigate()

  useEffect(() => {
    api.listSessions().then(setSessions).catch(console.error)
  }, [])

  const handleCreateTrip = async () => {
    setCreating(true)
    try {
      const { session_id } = await api.createTrip({})
      navigate(`/trip/${session_id}`)
    } catch (error) {
      console.error(error)
      window.alert('Could not create a new trip. Please try again.')
    } finally {
      setCreating(false)
    }
  }

  const deleteTrip = async (event, session) => {
    event.stopPropagation()
    const tripName = session.destination || 'this untitled trip'
    if (!window.confirm(`Delete ${tripName}? This cannot be undone.`)) return

    setDeletingId(session.session_id)
    try {
      await api.deleteTrip(session.session_id)
      setSessions((current) => current.filter((item) => item.session_id !== session.session_id))
    } catch (error) {
      console.error(error)
      window.alert('Could not delete this trip. Please try again.')
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div className="min-h-screen relative overflow-hidden">
      {/* Ambient glow blobs */}
      <div className="pointer-events-none absolute inset-0" aria-hidden="true">
        <div className="blob absolute -top-32 -left-24 w-[520px] h-[520px] rounded-full bg-accent/10 blur-[120px]" />
        <div className="blob absolute top-10 -right-32 w-[460px] h-[460px] rounded-full bg-indigo-500/10 blur-[120px]" style={{ animationDelay: '-6s' }} />
      </div>

      <div className="relative flex flex-col items-center pt-20 px-4 pb-24">
        {/* Top bar */}
        <div className="w-full max-w-4xl flex items-center justify-between mb-16">
          <div className="flex items-center gap-2.5">
            <span className="inline-grid place-items-center w-9 h-9 rounded-xl bg-gradient-to-br from-accent to-amber-500 text-black text-lg shadow-glow">
              ✈️
            </span>
            <span className="font-semibold tracking-tight">Trip Planner</span>
          </div>
          <span className="pill">grounded POIs · live weather · day-by-day schedule</span>
        </div>

        {/* Hero */}
        <div className="text-center max-w-2xl fade-up">
          <div className="inline-flex items-center gap-2 pill mb-6 border-accent/30 bg-accent/10 text-accent">
            <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
            AI travel companion
          </div>
          <h1 className="text-5xl sm:text-6xl font-bold tracking-tight leading-[1.05]">
            Plan your next trip
            <span className="block bg-gradient-to-r from-accent via-amber-300 to-accent bg-clip-text text-transparent">
              in one conversation
            </span>
          </h1>
          <p className="text-muted max-w-xl mx-auto mt-5 text-base leading-relaxed">
            Tell me where, when, and who — I'll build a grounded itinerary with
            real POIs, live weather, meals, and a cost estimate.
          </p>

          <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3">
            <button
              onClick={handleCreateTrip}
              disabled={creating}
              className="bg-accent text-black font-semibold px-7 py-3.5 rounded-full hover:brightness-110 active:scale-[0.98] transition disabled:opacity-50 shadow-glow"
            >
              {creating ? 'Creating…' : '＋ Create Trip'}
            </button>
            {sessions.length > 0 && (
              <a
                href="#trips"
                className="text-sm border border-border rounded-full px-5 py-3 text-muted hover:text-white hover:border-accent/60 transition"
              >
                Continue a trip ↓
              </a>
            )}
          </div>

          <div className="mt-10 flex flex-wrap justify-center gap-2 text-xs">
            {['🗺️ Real POIs', '🌦️ Live weather', '📅 Smart scheduling', '🥘 Meals', '💰 Cost estimate'].map((f) => (
              <span key={f} className="pill">{f}</span>
            ))}
          </div>
        </div>

        {/* Trips */}
        <div id="trips" className="w-full max-w-4xl mt-20 scroll-mt-8">
          <div className="flex items-baseline justify-between mb-5">
            <h2 className="text-xl font-semibold tracking-tight">
              {sessions.length > 0 ? 'Continue a trip' : 'No trips yet'}
            </h2>
            {sessions.length > 0 && (
              <span className="text-xs text-muted">
                {sessions.length} trip{sessions.length > 1 ? 's' : ''}
              </span>
            )}
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {/* New trip card */}
            <button
              onClick={handleCreateTrip}
              disabled={creating}
              className="group border border-dashed border-border rounded-2xl p-5 min-h-[132px] flex flex-col items-center justify-center gap-2 text-muted hover:border-accent/60 hover:text-accent hover:bg-accent/5 transition disabled:opacity-50"
            >
              <span className="text-2xl group-hover:scale-110 transition-transform">＋</span>
              <span className="text-sm font-medium">
                {creating ? 'Creating…' : 'New trip'}
              </span>
            </button>

            {sessions.map((s, idx) => {
              const range = fmtRange(s.start_date, s.end_date)
              return (
                <div
                  key={s.session_id}
                  onClick={() => navigate(`/trip/${s.session_id}`)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') navigate(`/trip/${s.session_id}`)
                  }}
                  className="group text-left bg-surface/90 border border-border rounded-2xl p-5 cursor-pointer hover:border-accent/60 hover:-translate-y-1 hover:shadow-lift transition-all duration-200 fade-up"
                >
                  <div className="flex items-start justify-between gap-3">
                    <span className="text-xl">{TRIP_EMOJI[idx % TRIP_EMOJI.length]}</span>
                    <button
                      type="button"
                      onClick={(event) => deleteTrip(event, s)}
                      disabled={deletingId === s.session_id}
                      aria-label={`Delete ${s.destination || 'untitled trip'}`}
                      className="text-[11px] px-2 py-0.5 rounded-full border border-transparent text-red-300/70 hover:text-red-100 hover:border-red-400/40 hover:bg-red-500/10 opacity-0 group-hover:opacity-100 focus:opacity-100 transition disabled:opacity-50"
                    >
                      {deletingId === s.session_id ? '…' : 'Delete'}
                    </button>
                  </div>

                  <div className="font-semibold mt-3 leading-snug">
                    {s.destination || 'Untitled trip'}
                  </div>

                  <div className="text-muted text-[13px] mt-1.5 space-y-0.5">
                    {s.origin && <div>From {s.origin}</div>}
                    <div>{range || 'Dates not set'}</div>
                  </div>

                  <div className="mt-3.5 flex items-center justify-between text-[11px]">
                    <span className="pill capitalize py-0.5">{stageLabel(s.stage)}</span>
                    {s.message_count > 0 && (
                      <span className="text-muted">
                        {s.message_count} msg{s.message_count > 1 ? 's' : ''}
                      </span>
                    )}
                  </div>
                </div>
              )
            })}
          </div>

          {sessions.length === 0 && (
            <p className="text-muted text-sm text-center mt-6">
              Your trips will appear here — start one above.
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
