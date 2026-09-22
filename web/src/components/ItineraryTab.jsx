import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { dayColor } from '../dayColors'

function fmtMinutes(min) {
  if (!min && min !== 0) return null
  const h = Math.floor(min / 60)
  const m = Math.round(min % 60)
  return h ? (m ? `${h}h ${m}m` : `${h}h`) : `${m}m`
}

// The drag affordance: three vertical dots, revealed on card hover.
function GripDots() {
  return (
    <svg width="8" height="18" viewBox="0 0 8 18" fill="currentColor" aria-hidden="true">
      <circle cx="4" cy="3" r="1.5" />
      <circle cx="4" cy="9" r="1.5" />
      <circle cx="4" cy="15" r="1.5" />
    </svg>
  )
}

function StopCard({ stop, dayIdx, sIdx, color, isSource, onHandleDown }) {
  const times = stop.arrive && stop.depart ? `${stop.arrive}–${stop.depart}` : null
  return (
    <div
      data-stop-card=""
      data-card-key={`${dayIdx}:${sIdx}`}
      className={`group relative flex gap-2 rounded-lg p-3 text-sm border transition-all duration-150 ${
        isSource
          ? 'opacity-30 border-dashed bg-bg/40'
          : 'bg-bg border-border hover:border-accent/50 hover:-translate-y-px hover:shadow-md'
      }`}
    >
      <button
        type="button"
        title="Drag to another day"
        onPointerDown={(e) => onHandleDown(e, dayIdx, sIdx, stop)}
        className={`shrink-0 self-start mt-0.5 text-muted hover:text-white cursor-grab active:cursor-grabbing select-none transition-opacity duration-150 focus:opacity-100 ${
          isSource ? 'opacity-0' : 'opacity-0 group-hover:opacity-100'
        }`}
        style={{ touchAction: 'none' }}
      >
        <GripDots />
      </button>

      <div className="flex-1 min-w-0 flex flex-col gap-1.5">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-2 min-w-0">
            <span
              className="inline-grid place-items-center w-5 h-5 rounded-full text-[10px] font-bold text-white shrink-0"
              style={{ background: color }}
            >
              {sIdx + 1}
            </span>
            <span className="font-medium text-white truncate">{stop.name}</span>
          </div>
          <div className="flex flex-col items-end shrink-0">
            {times && <span className="text-xs font-mono text-white/90">{times}</span>}
            {stop.km_from_prev > 0 && (
              <span className="text-xs text-muted">{stop.km_from_prev} km</span>
            )}
          </div>
        </div>

        {stop.activity && <span className="text-xs text-accent pl-7">{stop.activity}</span>}

        <div className="flex flex-wrap items-center gap-1.5 pl-7">
          {stop.is_trek && (
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-300 border border-amber-500/30 font-medium">
              🥾 trek
            </span>
          )}
          {!stop.is_trek && stop.strenuous && (
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-200/80 border border-amber-500/20">
              strenuous
            </span>
          )}
          {stop.kind && (
            <span className="text-[10px] text-muted capitalize">
              {stop.kind.replace(/_/g, ' ')}
            </span>
          )}
          {fmtMinutes(stop.visit_min) && (
            <span className="text-[10px] text-muted">· {fmtMinutes(stop.visit_min)} on site</span>
          )}
          {stop.ascent_m > 0 && (
            <span className="text-[10px] text-muted">· +{stop.ascent_m} m</span>
          )}
        </div>

        {stop.note && <span className="text-[11px] text-muted italic pl-7">{stop.note}</span>}
      </div>
    </div>
  )
}

function DropBar() {
  return <div className="h-1.5 rounded-full bg-accent my-1 shadow-[0_0_8px_rgba(255,138,61,0.6)]" />
}

export default function ItineraryTab({ sessionId, state, onStateRefresh }) {
  const itinerary = state?.itinerary
  const days = itinerary?.days || []
  const unscheduled = itinerary?.unscheduled || []
  const validation = itinerary?.validation
  const allocation = itinerary?.allocation || []
  const multiDest = itinerary?.multi_destination
  const adventure = itinerary?.adventure_level || state?.adventure_level

  const [drag, setDrag] = useState(null)       // rendered ghost + target
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const dragRef = useRef(null)
  const dayRefs = useRef({})
  const dragging = !!drag

  // ── Drag lifecycle ────────────────────────────────────────────────────
  const startDrag = (e, dayIdx, sIdx, stop) => {
    if (e.button !== 0 || saving) return
    const card = e.currentTarget.closest('[data-stop-card]')
    if (!card) return
    const rect = card.getBoundingClientRect()
    e.preventDefault()
    const d = {
      name: stop.name,
      fromDay: dayIdx,
      fromIndex: sIdx,
      w: rect.width,
      grabX: e.clientX - rect.left,
      grabY: e.clientY - rect.top,
      x: e.clientX,
      y: e.clientY,
      overDay: dayIdx,
      overIndex: sIdx,
    }
    dragRef.current = d
    setDrag({ ...d })
  }

  // Which day / insertion index is under the cursor (source card skipped).
  const retarget = (d) => {
    let overDay = null
    for (const key of Object.keys(dayRefs.current)) {
      const el = dayRefs.current[key]
      if (!el) continue
      const r = el.getBoundingClientRect()
      if (d.x >= r.left && d.x <= r.right && d.y >= r.top && d.y <= r.bottom) {
        overDay = Number(key)
        break
      }
    }
    let overIndex = null
    if (overDay !== null) {
      const col = dayRefs.current[overDay]
      const cards = col ? Array.from(col.querySelectorAll('[data-stop-card]')) : []
      const srcKey = `${d.fromDay}:${d.fromIndex}`
      let count = 0
      for (const el of cards) {
        if (el.getAttribute('data-card-key') === srcKey) continue
        const r = el.getBoundingClientRect()
        if (d.y < r.top + r.height / 2) break
        count += 1
      }
      overIndex = count
    }
    d.overDay = overDay
    d.overIndex = overIndex
  }

  // Edge auto-scroll while dragging, so long trips are reachable.
  const autoScroll = (d) => {
    const first = dayRefs.current[0] || Object.values(dayRefs.current)[0]
    const scroller = first?.closest?.('.overflow-y-auto')
    if (!scroller) return
    const r = scroller.getBoundingClientRect()
    const edge = 64
    if (d.y < r.top + edge) scroller.scrollTop -= 14
    else if (d.y > r.bottom - edge) scroller.scrollTop += 14
  }

  const commitDrag = async () => {
    const d = dragRef.current
    dragRef.current = null
    setDrag(null)
    if (!d || saving) return
    if (d.overDay === null || d.overIndex === null) return
    if (d.overDay === d.fromDay && d.overIndex === d.fromIndex) return
    if (!sessionId || !onStateRefresh) return
    setSaving(true)
    setError(null)
    try {
      const res = await api.moveStop(sessionId, d.name, d.fromDay, d.overDay, d.overIndex)
      if (res?.state) onStateRefresh(res.state)
    } catch (err) {
      setError(String(err))
    } finally {
      setSaving(false)
    }
  }

  useEffect(() => {
    if (!dragging) return undefined

    const onMove = (e) => {
      const d = dragRef.current
      if (!d) return
      d.x = e.clientX
      d.y = e.clientY
      retarget(d)
      setDrag({ ...d })
    }
    const onUp = () => { commitDrag() }

    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onUp)
    const prevSelect = document.body.style.userSelect
    const prevCursor = document.body.style.cursor
    document.body.style.userSelect = 'none'
    document.body.style.cursor = 'grabbing'

    let raf = 0
    const tick = () => {
      const d = dragRef.current
      if (d) {
        const before = `${d.x},${d.y},${d.overDay},${d.overIndex}`
        autoScroll(d)
        retarget(d)
        if (`${d.x},${d.y},${d.overDay},${d.overIndex}` !== before) setDrag({ ...d })
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)

    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onUp)
      cancelAnimationFrame(raf)
      document.body.style.userSelect = prevSelect
      document.body.style.cursor = prevCursor
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dragging])

  if (!itinerary || days.length === 0) {
    return (
      <div className="text-muted text-sm p-4 text-center border border-dashed border-border rounded-xl">
        Itinerary hasn't been built yet. Ask the agent in the chat to create one!
      </div>
    )
  }

  const degraded = itinerary.activity_source === 'default'

  return (
    <div className="space-y-6 pb-8">
      {error && (
        <div className="p-2 rounded bg-red-500/10 border border-red-500/30 text-red-300 text-xs">
          {error}
        </div>
      )}

      <div className="flex flex-wrap gap-2 items-center">
        <span className="text-[11px] text-muted italic">
          {saving ? 'Saving…' : '💡 Hover a card and drag it by the ⋮ dots to another day'}
        </span>
        {adventure && (
          <div className="bg-surface border border-border px-3 py-2 rounded-lg text-xs inline-flex items-center gap-2">
            <span>🥾</span>
            <span className="text-muted">Adventure level:</span>
            <span className="font-medium text-white capitalize">{adventure}</span>
            {itinerary.trek_cap > 0 && (
              <span className="text-muted">
                · up to {itinerary.trek_cap} trek{itinerary.trek_cap > 1 ? 's' : ''} total
              </span>
            )}
          </div>
        )}
        {multiDest && allocation.length > 0 && (
          <div className="bg-surface border border-border px-3 py-2 rounded-lg text-xs inline-flex items-center gap-2">
            <span>🗺️</span>
            <span className="text-muted">Split:</span>
            <span className="font-medium text-white">
              {allocation.map((a) => `${a.destination} ${a.days}d`).join(' · ')}
            </span>
          </div>
        )}
        {state.travel_mode && (
          <div className="bg-surface border border-border px-3 py-2 rounded-lg text-xs inline-flex items-center gap-2">
            <span>🚗</span>
            <span className="text-muted">Travel Mode:</span>
            <span className="font-medium text-white capitalize">{state.travel_mode}</span>
          </div>
        )}
        {degraded && (
          <div className="bg-amber-950/40 border border-amber-800/50 px-3 py-2 rounded-lg text-xs text-amber-300">
            ⚠️ Activity effort could not be assessed — days may be ambitious.
          </div>
        )}
      </div>

      <div className="space-y-6">
        {days.map((day, dayIdx) => {
          const color = dayColor(dayIdx)
          const stops = day.stops || []
          const isTarget = dragging && drag?.overDay === dayIdx
          return (
            <div
              key={dayIdx}
              data-day-idx={dayIdx}
              ref={(el) => { dayRefs.current[dayIdx] = el }}
              className={`bg-surface border rounded-xl overflow-hidden transition-all duration-150 ${
                isTarget
                  ? 'border-accent/60 ring-2 ring-accent/30'
                  : 'border-border'
              }`}
            >
              {/* Day color hairline — same color as this day on the map */}
              <div className="h-1 w-full" style={{ background: color }} />

              <div className="p-4 space-y-3">
                <div className="flex items-start justify-between border-b border-border pb-2 gap-3">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span
                      className="inline-block w-2.5 h-2.5 rounded-full shrink-0"
                      style={{ background: color }}
                    />
                    <span className="font-semibold text-white text-sm">Day {dayIdx + 1}</span>
                    <span className="text-[10px] px-2 py-0.5 rounded-full bg-surface border border-border text-muted">
                      {stops.length} stop{stops.length === 1 ? '' : 's'}
                    </span>
                    {day.destination && (
                      <span className="text-xs bg-accent/10 border border-accent/20 px-2 py-0.5 rounded text-accent">
                        📍 {day.destination}
                      </span>
                    )}
                    {day.theme && (
                      <span className="text-xs bg-accent/10 border border-accent/20 px-2 py-0.5 rounded text-accent">
                        {day.theme}
                      </span>
                    )}
                    {Array.isArray(day.class_mix) && day.class_mix.length > 0 && (
                      <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">
                        {day.class_mix.join(' + ')}
                      </span>
                    )}
                    {day.overloaded && (
                      <span className="text-[10px] px-2 py-0.5 rounded-full bg-red-500/15 text-red-300 border border-red-500/30">
                        long day
                      </span>
                    )}
                    {day.unverified && (
                      <span className="text-[10px] px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-300 border border-amber-500/30">
                        unverified effort
                      </span>
                    )}
                  </div>
                  <div className="text-right shrink-0">
                    <div className="text-xs text-muted">{day.date || `Day ${dayIdx + 1}`}</div>
                    {day.start_time && day.end_time && (
                      <div className="text-xs font-mono text-white/90">
                        {day.start_time}–{day.end_time}
                      </div>
                    )}
                  </div>
                </div>

                {day.transfer_in && (
                  <div className="text-xs text-indigo-300 bg-indigo-950/40 border border-indigo-800/50 rounded px-2.5 py-1 flex items-center gap-2">
                    <span>🚌</span>
                    <span>
                      Transfer {day.transfer_in.from} → {day.transfer_in.to}
                      {day.transfer_in.km ? ` · ${day.transfer_in.km} km` : ''}
                      {day.transfer_in.hours ? ` · ~${day.transfer_in.hours} h` : ''}
                    </span>
                  </div>
                )}

                {day.expected_rain_mm > 5 && (
                  <div className="text-xs text-blue-300 bg-blue-950/40 border border-blue-800/50 rounded px-2.5 py-1">
                    🌧️ Expected rain: {day.expected_rain_mm} mm
                  </div>
                )}

                {!day.rest_day && stops.length > 0 && (
                  <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted">
                    {fmtMinutes(day.effort_min) && <span>On foot: {fmtMinutes(day.effort_min)}</span>}
                    {fmtMinutes(day.total_travel_min) && (
                      <span>Travel: {fmtMinutes(day.total_travel_min)}</span>
                    )}
                    {typeof day.trek_count === 'number' && (
                      <span>
                        Treks: {day.trek_count}
                        {day.effort_cap_min ? ` · budget ${fmtMinutes(day.effort_cap_min)}` : ''}
                      </span>
                    )}
                  </div>
                )}

                {day.breakfast && (
                  <div className="bg-bg/60 border border-border/80 rounded-lg p-2.5 text-xs flex items-center gap-2 flex-wrap">
                    <span>🥐</span>
                    <span className="text-muted">Breakfast:</span>
                    <span className="font-medium text-white">{day.breakfast.name}</span>
                    {day.breakfast.included && (
                      <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">
                        included with stay
                      </span>
                    )}
                    <span className="text-muted font-mono">at {day.breakfast.arrive}</span>
                    {day.breakfast.context && (
                      <span className="text-[10px] text-muted">· {day.breakfast.context}</span>
                    )}
                  </div>
                )}

                {day.rest_day || stops.length === 0 ? (
                  <div
                    className={`border border-dashed rounded-lg px-3 py-5 text-center text-xs italic transition-colors duration-150 ${
                      isTarget
                        ? 'border-accent text-accent bg-accent/5'
                        : 'border-border text-muted'
                    }`}
                  >
                    {isTarget ? 'Drop to plan a stop here' : 'Rest & leisure day'}
                  </div>
                ) : (
                  <div className="space-y-2 mt-2">
                    {stops.map((stop, sIdx) => (
                      <div key={`${stop.name}::${sIdx}`}>
                        {dragging && drag?.overDay === dayIdx && drag.overIndex === sIdx && (
                          <DropBar />
                        )}
                        <StopCard
                          stop={stop}
                          dayIdx={dayIdx}
                          sIdx={sIdx}
                          color={color}
                          isSource={!!(dragging && drag.fromDay === dayIdx && drag.fromIndex === sIdx)}
                          onHandleDown={startDrag}
                        />
                      </div>
                    ))}
                    {dragging && drag?.overDay === dayIdx && drag.overIndex === stops.length && (
                      <DropBar />
                    )}
                  </div>
                )}

                {day.lunch && (
                  <div className="bg-bg/60 border border-border/80 rounded-lg p-2.5 text-xs flex items-center gap-2">
                    <span>🍽️</span>
                    <span className="text-muted">Lunch:</span>
                    <span className="font-medium text-white">
                      {day.lunch.name || day.lunch}
                    </span>
                    {day.lunch_time && <span className="text-muted font-mono">at {day.lunch_time}</span>}
                  </div>
                )}

                {day.dinner && (
                  <div className="bg-bg/60 border border-border/80 rounded-lg p-2.5 text-xs flex items-center gap-2 flex-wrap">
                    <span>🍲</span>
                    <span className="text-muted">Dinner:</span>
                    <span className="font-medium text-white">{day.dinner.name}</span>
                    {day.dinner.included && (
                      <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">
                        included with stay
                      </span>
                    )}
                    <span className="text-muted font-mono">at {day.dinner.arrive}</span>
                    {day.dinner.context && (
                      <span className="text-[10px] text-muted">· {day.dinner.context}</span>
                    )}
                  </div>
                )}

                {day.hotel && (
                  <div className="bg-bg/60 border border-border/80 rounded-lg p-2.5 text-xs flex items-center gap-2">
                    <span>🏨</span>
                    <span className="text-muted">Stay:</span>
                    <span className="font-medium text-white">{day.hotel.name || day.hotel}</span>
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </div>

      {unscheduled.length > 0 && (
        <div className="bg-surface border border-border rounded-xl p-4 space-y-2">
          <div className="text-sm font-semibold text-white">Left out of the schedule</div>
          {unscheduled.map((entry, idx) => (
            <div key={idx} className="text-xs flex flex-col gap-0.5">
              <span className="text-white/90 font-medium">{entry.name || entry}</span>
              {entry.reason && <span className="text-muted">{entry.reason}</span>}
            </div>
          ))}
        </div>
      )}

      {validation && validation.trek_rule_ok === false && (
        <div className="text-xs text-amber-300 bg-amber-950/40 border border-amber-800/50 rounded-lg px-3 py-2">
          ⚠️ One day now holds more than one trek — that makes for a heavy day. Drag a trek
          to another day to keep the pace realistic.
        </div>
      )}

      {/* Drag ghost — follows the cursor, above everything */}
      {drag && (
        <div
          className="fixed left-0 top-0 z-50 pointer-events-none"
          style={{
            transform: `translate3d(${drag.x - drag.grabX}px, ${drag.y - drag.grabY}px, 0)`,
            width: drag.w,
          }}
        >
          <div className="bg-surface border border-accent/60 rounded-lg p-3 shadow-2xl rotate-[-1.5deg] scale-[1.02]">
            <div className="flex items-center gap-2">
              <span className="text-muted"><GripDots /></span>
              <span className="font-medium text-white text-sm truncate">{drag.name}</span>
            </div>
            <div className="text-[11px] text-accent mt-1">
              {drag.overDay !== null
                ? `Move to Day ${drag.overDay + 1}`
                : 'Drag onto a day to drop'}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
