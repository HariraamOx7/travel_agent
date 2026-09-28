export default function StayTab({ state }) {
  const stay = state.recommendations?.stay || []
  if (!stay.length) {
    return <div className="text-muted text-sm">No accommodation loaded yet.</div>
  }

  const mealIncluded = (plan) =>
    /included|half|full|board/i.test(plan || '')

  return (
    <div className="space-y-4 fade-up">
      <div className="flex items-center justify-between">
        <span className="section-title">Accommodation</span>
        <span className="pill py-0.5">{stay.length} option{stay.length > 1 ? 's' : ''}</span>
      </div>

      <div className="space-y-3">
        {stay.map((h, i) => (
          <div
            key={i}
            className="bg-bg/70 border border-border rounded-2xl p-4 hover:border-accent/50 transition"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex items-start gap-3 min-w-0">
                <span className="shrink-0 w-9 h-9 grid place-items-center rounded-xl bg-surface border border-border text-base">
                  🏨
                </span>
                <div className="min-w-0">
                  <div className="font-medium leading-snug">{h.name}</div>
                  <div className="text-muted text-xs mt-0.5">
                    {h.type && <span className="capitalize">{String(h.type).replace(/_/g, ' ')}</span>}
                    {h.rating_label && (
                      <span> · ⭐ {h.rating_label}</span>
                    )}
                  </div>
                </div>
              </div>
              <div className="text-right shrink-0">
                <div className="text-sm font-semibold text-accent tabular-nums">
                  {h.nightly_label || `~₹${h.nightly_inr}`}
                </div>
                <div className="text-[10px] text-muted">per night</div>
              </div>
            </div>

            {h.meal_plan && (
              <div
                className={`mt-3 inline-flex items-center gap-1.5 text-[11px] px-2.5 py-1 rounded-full border ${
                  mealIncluded(h.meal_plan)
                    ? 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30'
                    : 'bg-surface text-muted border-border'
                }`}
              >
                🍽️ {h.meal_plan}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}