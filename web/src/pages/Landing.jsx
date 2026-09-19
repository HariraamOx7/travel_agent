import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'

export default function Landing() {
  const [sessions, setSessions] = useState([])
  const [showCreate, setShowCreate] = useState(false)
  const [deletingId, setDeletingId] = useState(null)
  const navigate = useNavigate()

  useEffect(() => {
    api.listSessions().then(setSessions).catch(console.error)
  }, [])

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
        onClick={() => setShowCreate(true)}
        className="bg-accent text-black font-semibold px-6 py-3 rounded-full hover:opacity-90"
      >
        + Create Trip
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
                  {s.start_date} → {s.end_date}
                </div>
                <div className="text-muted text-xs mt-2">{s.stage}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {showCreate && <CreateTripModal onClose={() => setShowCreate(false)} />}
    </div>
  )
}

function CreateTripModal({ onClose }) {
  const navigate = useNavigate()
  const [form, setForm] = useState({
    origin: 'Chennai',
    destination: '',
    start_date: '',
    end_date: '',
    travellers: 1,
    budget_total: 40000,
  })
  const [error, setError] = useState(null)

  const submit = async () => {
    if (!form.destination.trim()) return setError('Please enter a destination.')
    if (!form.start_date || !form.end_date) return setError('Please pick both dates.')
    try {
      const { session_id } = await api.createTrip(form)
      navigate(`/trip/${session_id}`)
    } catch (e) {
      setError(String(e))
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50"
      onClick={onClose}
    >
      <div
        className="bg-surface border border-border rounded-2xl p-6 w-full max-w-md"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-xl font-semibold mb-4">What's the plan?</h2>

        {error && (
          <div className="bg-red-900/40 border border-red-700 text-red-200 text-sm p-2 rounded mb-3">
            {error}
          </div>
        )}

        <div className="space-y-3">
          <input
            className="w-full bg-bg border border-border rounded px-3 py-2"
            placeholder="From"
            value={form.origin}
            onChange={(e) => setForm({ ...form, origin: e.target.value })}
          />
          <input
            className="w-full bg-bg border border-border rounded px-3 py-2"
            placeholder="Where (e.g. hill stations near Chennai)"
            value={form.destination}
            onChange={(e) => setForm({ ...form, destination: e.target.value })}
          />
          <div className="grid grid-cols-2 gap-3">
            <input
              type="date"
              className="bg-bg border border-border rounded px-3 py-2"
              value={form.start_date}
              onChange={(e) => setForm({ ...form, start_date: e.target.value })}
            />
            <input
              type="date"
              className="bg-bg border border-border rounded px-3 py-2"
              value={form.end_date}
              onChange={(e) => setForm({ ...form, end_date: e.target.value })}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <input
              type="number"
              min="1"
              className="bg-bg border border-border rounded px-3 py-2"
              value={form.travellers}
              onChange={(e) => setForm({ ...form, travellers: Number(e.target.value) })}
            />
            <input
              type="number"
              className="bg-bg border border-border rounded px-3 py-2"
              value={form.budget_total}
              onChange={(e) => setForm({ ...form, budget_total: Number(e.target.value) })}
            />
          </div>
        </div>

        <div className="flex gap-2 mt-5">
          <button
            onClick={onClose}
            className="flex-1 border border-border rounded py-2"
          >
            Cancel
          </button>
          <button
            onClick={submit}
            className="flex-1 bg-accent text-black font-semibold rounded py-2"
          >
            Create trip
          </button>
        </div>
      </div>
    </div>
  )
}
