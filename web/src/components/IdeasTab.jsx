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
  const includedCount = attractions.filter(
    (a) => !excluded.has(String(a.name).toLowerCase())
  ).length

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
    <div className="space-y-5 fade-up">
      {error && (
        <div className="p-2.5 rounded-xl bg-red-500/10 border border-red-500/30 text-red-300 text-xs">
          {error}
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <span className="section-title">All ideas</span>
          <div className="text-xs text-muted mt-1">
            Tap to include or exclude — the itinerary rebuilds.
          </div>
        </div>
        <span className="pill py-0.5">
          {includedCount}/{attractions.length} included
        </span>
      </div>

      <ul className="space-y-1.5">
        {attractions.map((it, idx) => {
          const isExcluded = excluded.has(String(it.name).toLowerCase())
          return (
            <li key={`${it.name}::${idx}`}>
              <button
                onClick={() => toggle(it.name, isExcluded)}
                disabled={busyName !== null}
                className={`w-full text-left px-3 py-2.5 rounded-xl border text-sm transition flex items-center justify-between gap-3 ${
                  isExcluded
                    ? 'border-border/40 bg-transparent opacity-55 hover:opacity-80'
                    : 'bg-bg/70 border-border hover:border-accent/60 hover:-translate-y-px'
                } ${busyName === it.name ? 'animate-pulse' : ''}`}
              >
                <span className="flex items-center gap-2.5 min-w-0">
                  <span className="shrink-0 w-7 h-7 grid place-items-center rounded-lg bg-surface border border-border text-sm">
                    {iconFor(it.kind)}
                  </span>
                  <span className={`truncate ${isExcluded ? 'line-through text-muted' : ''}`}>
                    {it.name}
                  </span>
                  {it.kind && (
                    <span className="text-muted text-[11px] shrink-0 capitalize hidden sm:inline">
                      · {String(it.kind).replace(/_/g, ' ')}
                    </span>
                  )}
                  {it.indoor_outdoor && (
                    <span className="text-muted text-[11px] shrink-0 capitalize hidden md:inline">
                      · {it.indoor_outdoor}
                    </span>
                  )}
                </span>
                <span
                  className={`text-[10px] shrink-0 px-2 py-0.5 rounded-full border ${
                    isExcluded
                      ? 'bg-surface text-muted border-border'
                      : 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30'
                  }`}
                >
                  {busyName === it.name ? '…' : isExcluded ? 'excluded' : 'included'}
                </span>
              </button>
            </li>
          )
        })}
        {attractions.length === 0 && (
          <li className="text-muted text-sm p-6 text-center border border-dashed border-border rounded-2xl">
            No attractions loaded.
          </li>
        )}
      </ul>

      {(rec.food?.length > 0 || rec.stay?.length > 0) && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {rec.food?.length > 0 && (
            <div className="panel p-4">
              <div className="section-title mb-3">🍽️ Food ideas</div>
              <ul className="space-y-2">
                {(rec.food || []).slice(0, 8).map((f, i) => (
                  <li key={`${f.name}::${i}`} className="text-sm text-muted truncate flex items-baseline gap-2">
                    <span className="w-1 h-1 rounded-full bg-accent shrink-0" />
                    <span className="truncate">{f.name}</span>
                    {f.cuisine && <span className="text-xs shrink-0">· {f.cuisine}</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {rec.stay?.length > 0 && (
            <div className="panel p-4">
              <div className="section-title mb-3">🏨 Stay ideas</div>
              <ul className="space-y-2">
                {(rec.stay || []).slice(0, 6).map((s, i) => (
                  <li key={`${s.name}::${i}`} className="text-sm text-muted truncate flex items-baseline gap-2">
                    <span className="w-1 h-1 rounded-full bg-accent shrink-0" />
                    <span className="truncate">{s.name}</span>
                    {s.type && <span className="text-xs shrink-0">· {s.type}</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
