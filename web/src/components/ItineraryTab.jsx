export default function ItineraryTab({ state }) {
  const itinerary = state?.itinerary
  const days = itinerary?.days || []

  if (!itinerary || days.length === 0) {
    return (
      <div className="text-muted text-sm p-4 text-center border border-dashed border-border rounded-xl">
        Itinerary hasn't been built yet. Ask the agent in the chat to create one!
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {state.travel_mode && (
        <div className="bg-surface border border-border px-3 py-2 rounded-lg text-xs inline-flex items-center gap-2">
          <span>🚗</span>
          <span className="text-muted">Travel Mode:</span>
          <span className="font-medium text-white capitalize">{state.travel_mode}</span>
        </div>
      )}

      <div className="space-y-6">
        {days.map((day, dayIdx) => (
          <div
            key={dayIdx}
            className="bg-surface border border-border rounded-xl p-4 space-y-3"
          >
            <div className="flex items-center justify-between border-b border-border pb-2">
              <div className="flex items-center gap-2">
                <span className="font-semibold text-accent text-sm">Day {dayIdx + 1}</span>
                {day.theme && (
                  <span className="text-xs bg-accent/10 border border-accent/20 px-2 py-0.5 rounded text-accent">
                    {day.theme}
                  </span>
                )}
              </div>
              <span className="text-xs text-muted">
                {day.date || `Day ${dayIdx + 1}`}
              </span>
            </div>

            {day.expected_rain_mm > 5 && (
              <div className="text-xs text-blue-300 bg-blue-950/40 border border-blue-800/50 rounded px-2.5 py-1">
                🌧️ Expected rain: {day.expected_rain_mm} mm
              </div>
            )}

            {day.rest_day || !day.stops?.length ? (
              <div className="text-muted text-xs italic py-2">
                Rest & leisure day.
              </div>
            ) : (
              <div className="space-y-2 mt-2">
                {day.stops.map((stop, sIdx) => (
                  <div
                    key={sIdx}
                    className="bg-bg border border-border rounded-lg p-3 text-sm flex flex-col gap-1"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-medium text-white">{stop.name}</span>
                      {stop.km_from_prev > 0 && (
                        <span className="text-xs text-muted">
                          {stop.km_from_prev} km
                        </span>
                      )}
                    </div>
                    {stop.kind && (
                      <span className="text-xs text-muted capitalize">
                        {stop.kind.replace(/_/g, ' ')}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}

            {day.lunch && (
              <div className="bg-bg/60 border border-border/80 rounded-lg p-2.5 text-xs flex items-center gap-2">
                <span>🍽️</span>
                <span className="text-muted">Lunch:</span>
                <span className="font-medium text-white">{day.lunch.name || day.lunch}</span>
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
    </div>
  )
}
