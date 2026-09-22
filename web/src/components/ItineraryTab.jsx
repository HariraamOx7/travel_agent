function fmtMinutes(min) {
  if (!min && min !== 0) return null
  const h = Math.floor(min / 60)
  const m = Math.round(min % 60)
  return h ? (m ? `${h}h ${m}m` : `${h}h`) : `${m}m`
}

function StopRow({ stop }) {
  const times = stop.arrive && stop.depart ? `${stop.arrive}–${stop.depart}` : null
  return (
    <div className="bg-bg border border-border rounded-lg p-3 text-sm flex flex-col gap-1.5">
      <div className="flex items-start justify-between gap-3">
        <div className="flex flex-col">
          <span className="font-medium text-white">{stop.name}</span>
          {stop.activity && (
            <span className="text-xs text-accent">{stop.activity}</span>
          )}
        </div>
        <div className="flex flex-col items-end shrink-0">
          {times && (
            <span className="text-xs font-mono text-white/90">{times}</span>
          )}
          {stop.km_from_prev > 0 && (
            <span className="text-xs text-muted">{stop.km_from_prev} km</span>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
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
          <span className="text-[10px] text-muted">
            · {fmtMinutes(stop.visit_min)} on site
          </span>
        )}
        {stop.ascent_m > 0 && (
          <span className="text-[10px] text-muted">
            · +{stop.ascent_m} m
          </span>
        )}
      </div>

      {stop.note && (
        <span className="text-[11px] text-muted italic">{stop.note}</span>
      )}
    </div>
  )
}

export default function ItineraryTab({ state }) {
  const itinerary = state?.itinerary
  const days = itinerary?.days || []
  const unscheduled = itinerary?.unscheduled || []
  const validation = itinerary?.validation
  const allocation = itinerary?.allocation || []
  const multiDest = itinerary?.multi_destination
  const adventure = itinerary?.adventure_level || state?.adventure_level

  if (!itinerary || days.length === 0) {
    return (
      <div className="text-muted text-sm p-4 text-center border border-dashed border-border rounded-xl">
        Itinerary hasn't been built yet. Ask the agent in the chat to create one!
      </div>
    )
  }

  const degraded = itinerary.activity_source === 'default'

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap gap-2">
        {adventure && (
          <div className="bg-surface border border-border px-3 py-2 rounded-lg text-xs inline-flex items-center gap-2">
            <span>🥾</span>
            <span className="text-muted">Adventure level:</span>
            <span className="font-medium text-white capitalize">{adventure}</span>
            {itinerary.trek_cap > 0 && (
              <span className="text-muted">· up to {itinerary.trek_cap} trek{itinerary.trek_cap > 1 ? 's' : ''} total</span>
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
        {days.map((day, dayIdx) => (
          <div
            key={dayIdx}
            className="bg-surface border border-border rounded-xl p-4 space-y-3"
          >
            <div className="flex items-start justify-between border-b border-border pb-2 gap-3">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-semibold text-accent text-sm">Day {dayIdx + 1}</span>
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

            {!day.rest_day && day.stops?.length > 0 && (
              <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted">
                {fmtMinutes(day.effort_min) && (
                  <span>On foot: {fmtMinutes(day.effort_min)}</span>
                )}
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

            {day.rest_day || !day.stops?.length ? (
              <div className="text-muted text-xs italic py-2">
                Rest & leisure day.
              </div>
            ) : (
              <div className="space-y-2 mt-2">
                {day.stops.map((stop, sIdx) => (
                  <StopRow key={sIdx} stop={stop} />
                ))}
              </div>
            )}

            {day.lunch && (
              <div className="bg-bg/60 border border-border/80 rounded-lg p-2.5 text-xs flex items-center gap-2">
                <span>🍽️</span>
                <span className="text-muted">Lunch:</span>
                <span className="font-medium text-white">
                  {day.lunch.name || day.lunch}
                </span>
                {day.lunch_time && (
                  <span className="text-muted font-mono">at {day.lunch_time}</span>
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
        ))}
      </div>

      {unscheduled.length > 0 && (
        <div className="bg-surface border border-border rounded-xl p-4 space-y-2">
          <div className="text-sm font-semibold text-white">
            Left out of the schedule
          </div>
          {unscheduled.map((entry, idx) => (
            <div key={idx} className="text-xs flex flex-col gap-0.5">
              <span className="text-white/90 font-medium">
                {entry.name || entry}
              </span>
              {entry.reason && (
                <span className="text-muted">{entry.reason}</span>
              )}
            </div>
          ))}
        </div>
      )}

      {validation && validation.trek_rule_ok === false && (
        <div className="text-xs text-red-300 bg-red-950/40 border border-red-800/50 rounded-lg px-3 py-2">
          ⚠️ A day holds more than one trek — this should not happen. Please
          report it.
        </div>
      )}
    </div>
  )
}
