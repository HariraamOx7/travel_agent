export default function IdeasTab({ state }) {
  const rec = state.recommendations
  if (!rec) {
    return <div className="text-muted text-sm">Recommendations not loaded yet.</div>
  }

  const sections = [
    { title: '🏔️ Top attractions', items: rec.top_picks || [] },
    { title: '🌿 Hidden gems',      items: rec.hidden_gems || [] },
    { title: '🍽️ Food',            items: rec.food || [] },
  ]

  return (
    <div className="space-y-6">
      {sections.map((sec) => (
        <div key={sec.title}>
          <div className="font-semibold mb-2">{sec.title}</div>
          {sec.items.length === 0 && (
            <div className="text-muted text-sm">None yet.</div>
          )}
          <ul className="space-y-1">
            {sec.items.map((it, i) => (
              <li key={i} className="text-sm">
                • {it.name}
                {it.kind && <span className="text-muted text-xs"> · {it.kind}</span>}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  )
}