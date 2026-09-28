// Small shared formatting helpers for the UI.

// "22–25 Sep 2026" / "28 Sep – 2 Oct 2026"; null when dates are missing.
export function fmtRange(start, end) {
  if (!start || !end) return null
  try {
    const a = new Date(`${start}T00:00:00`)
    const b = new Date(`${end}T00:00:00`)
    const day = (d) => d.getDate()
    const mon = (d) => d.toLocaleString('en', { month: 'short' })
    if (a.getMonth() === b.getMonth() && a.getFullYear() === b.getFullYear()) {
      return `${day(a)}–${day(b)} ${mon(b)} ${b.getFullYear()}`
    }
    return `${day(a)} ${mon(a)} – ${day(b)} ${mon(b)} ${b.getFullYear()}`
  } catch {
    return `${start} → ${end}`
  }
}
