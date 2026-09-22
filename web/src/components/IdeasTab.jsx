import { useState } from 'react'
import { api } from '../api'

const KIND_ICONS = {
  viewpoint: '🌄', peak: '⛰️', trek: '🥾', waterfall: '💧',
  museum: '🏛️', temple: '🛕', church: '⛪', mosque: '🕌',
  garden: '🌳', park: '🌲', lake: '🏞️', beach: '🏖️',
  fort: '🏰', monument: '🗿', zoo: '🦌', cave: '🕳️',
  tea: '🍵', plantation: '🌿', market: '🛍️', dam: '🌊',
}

function iconFor(kind) {
  if (!kind) return '📍'
  const k = String(kind).toLowerCase()
  for (const [needle, icon] of Object.entries(KIND_ICONS)) {
    if (k.includes(needle)) return icon
  }
  return '📍'
}

export default function IdeasTab({ sessionId, state, onStateRefresh }) {
  const rec = state?.recommendations
  const [busyName, setBusyName] = useState(null)
  const [error, setError] = useState(null)

  if (!rec) {
    return <div className="text-muted text-sm">Recommendations not loaded yet.</div>
  }

  const excluded = new Set((state.excluded_names || []).map((n) => n.toLowerCase()))
  const attractions = rec.attractions || []

  const toggle = async (name, currentlyExcluded) => {
    setBusyName(name)
    setError(null)
    try {
      const res = currentlyExcluded
        ? await api.editIdeas(sessionId, [], [name])
        : await api.editIdeas(sessionId, [name], [])
      if (res?.state && onStateRefresh) onStateRefresh(res.state)
    } catch (e) {
      setError(String(e))
    } finally {
      setBusyName(null)
    }
  }

  return (
    <div className="space-y-6">
      {error && (
        <div className="p-2 rounded bg-red-500/10 border border-red-500/30 text-red-300 text-xs">
          {error}
        </div>
      )}

      <div className="font-semibold text-sm">
        All ideas ({attractions.length}) · tap to include/exclude, itinerary rebuilds
      </div>

      <ul className="space-y-1.5">
        {attractions.map((it, idx) => {
          const isExcluded = excluded.has(String(it.name).toLowerCase())
          return (
            <li key={`${it.name}::${idx}`}>
              <button
                onClick={() => toggle(it.name, isExcluded)}
                disabled={busyName !== null}
                className={`w-full text-left px-3 py-2 rounded-lg border text-sm transition flex items-center justify-between gap-2 ${
                  isExcluded
                    ? 'border-border/40 bg-transparent opacity-55 hover:opacity-80'
                    : 'border-border bg-bg hover:border-accent/60'
                } ${busyName === it.name ? 'animate-pulse' : ''}`}
              >
                <span className="flex items-center gap-2 min-w-0">
                  <span>{iconFor(it.kind)}</span>
                  <span className={`truncate ${isExcluded ? 'line-through text-muted' : ''}`}>
                    {it.name}
                  </span>
                  {it.kind && <span className="text-muted text-xs shrink-0">· {it.kind}</span>}
                </span>
                <span className={`text-xs shrink-0 ${isExcluded ? 'text-emerald-400' : 'text-muted'}`}>
                  {busyName === it.name ? '…' : isExcluded ? 'restore' : 'remove'}
                </span>
              </button>
            </li>
          )
        })}
        {attractions.length === 0 && (
          <li className="text-muted text-sm">No attractions loaded.</li>
        )}
      </ul>

      {(rec.food?.length > 0 || rec.stay?.length > 0) && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <div className="font-semibold mb-2 text-sm">🍽️ Food ideas</div>
            <ul className="space-y-1">
              {(rec.food || []).slice(0, 8).map((f, i) => (
                <li key={`${f.name}::${i}`} className="text-sm text-muted truncate">
                  • {f.name}
                  {f.cuisine && <span className="text-xs"> · {f.cuisine}</span>}
                </li>
              ))}
            </ul>
          </div>
          <div>
            <div className="font-semibold mb-2 text-sm">🏨 Stay ideas</div>
            <ul className="space-y-1">
              {(rec.stay || []).slice(0, 6).map((s, i) => (
                <li key={`${s.name}::${i}`} className="text-sm text-muted truncate">
                  • {s.name}
                  {s.type && <span className="text-xs"> · {s.type}</span>}
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </div>
  )
}
