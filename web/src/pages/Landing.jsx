import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'

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
    <div className="min-h-screen flex flex-col items-center pt-24 px-4">
      <div className="text-5xl mb-4">✈️</div>
      <h1 className="text-4xl font-bold mb-3">Plan your next trip</h1>
      <p className="text-muted max-w-lg text-center mb-8">
        Tell me where, when, and who — I'll build a grounded itinerary
        with real POIs, live weather, and a cost estimate.
      </p>

      <button
        onClick={handleCreateTrip}
        disabled={creating}
        className="bg-accent text-black font-semibold px-6 py-3 rounded-full hover:opacity-90 disabled:opacity-50"
      >
        {creating ? 'Creating…' : '+ Create Trip'}
      </button>

      {sessions.length > 0 && (
        <div className="w-full max-w-4xl mt-16">
          <h2 className="text-xl font-semibold mb-4">Continue a trip</h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {sessions.map((s) => (
              <div
                key={s.session_id}
                onClick={() => navigate(`/trip/${s.session_id}`)}
                role="button"
                tabIndex={0}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') navigate(`/trip/${s.session_id}`)
                }}
                className="text-left bg-surface border border-border rounded-xl p-4 hover:border-accent cursor-pointer"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="font-semibold">
                    {s.destination || 'Untitled trip'}
                  </div>
                  <button
                    type="button"
                    onClick={(event) => deleteTrip(event, s)}
                    disabled={deletingId === s.session_id}
                    aria-label={`Delete ${s.destination || 'untitled trip'}`}
                    className="text-xs text-red-300 hover:text-red-100 disabled:opacity-50"
                  >
                    {deletingId === s.session_id ? 'Deleting…' : 'Delete'}
                  </button>
                </div>
                <div className="text-muted text-sm mt-1">
                  {s.origin && `from ${s.origin} · `}
                  {s.start_date && s.end_date ? `${s.start_date} → ${s.end_date}` : 'Dates not set'}
                </div>
                <div className="text-muted text-xs mt-2 flex items-center justify-between">
                  <span>{s.stage}</span>
                  {s.message_count > 0 && (
                    <span>{s.message_count} msg{s.message_count > 1 ? 's' : ''}</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
