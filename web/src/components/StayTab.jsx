export default function StayTab({ state }) {
  const stay = state.recommendations?.stay || []
  if (!stay.length) {
    return <div className="text-muted text-sm">No accommodation loaded yet.</div>
  }

  return (
    <div className="space-y-3">
      {stay.map((h, i) => (
        <div key={i} className="bg-bg border border-border rounded p-3">
          <div className="font-medium">{h.name}</div>
          <div className="text-muted text-xs mt-1">
            {h.type} · {h.nightly_label || `~₹${h.nightly_inr}/night`}
            {h.rating_label && ` · ${h.rating_label}`}
            {h.meal_plan && ` · ${h.meal_plan}`}
          </div>
        </div>
      ))}
    </div>
  )
}