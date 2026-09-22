import { useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { api } from '../api'

// Day color palette — distinct, dark-UI-friendly, colorblind-tolerant.
const DAY_COLORS = [
  '#e6194B', // red
  '#3cb44b', // green
  '#4363d8', // blue
  '#f58231', // orange
  '#911eb4', // purple
  '#42d4f4', // cyan
  '#f032e6', // magenta
  '#bfef45', // lime
  '#fabed4', // pink
  '#469990', // teal
]

export default function MapTab({ sessionId, state }) {
  const containerRef = useRef(null)
  const mapRef = useRef(null)
  const layersRef = useRef([])
  const initialFitDoneRef = useRef(false)
  const [data, setData] = useState(null)

  // ── 1. Fetch map data on session / state change ──────────────────────
  useEffect(() => {
    let cancelled = false
    api.getMap(sessionId)
      .then((res) => { if (!cancelled) setData(res) })
      .catch(console.error)
    return () => { cancelled = true }
  }, [sessionId, state])

  // ── 2. Create the map ONCE. This is the zoom-reset fix. ──────────────
  // All data-driven updates (markers, polylines) go through the layer
  // effect below. The view state is owned by Leaflet and never
  // overwritten by React re-renders.
  useEffect(() => {
    if (mapRef.current || !containerRef.current) return

    const map = L.map(containerRef.current, {
      zoomControl: true,
      scrollWheelZoom: true,
      doubleClickZoom: true,
      dragging: true,
      zoomSnap: 0.5,
      zoomDelta: 0.5,
    }).setView([10.0, 77.5], 7)

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '© OpenStreetMap contributors',
      maxZoom: 19,
    }).addTo(map)

    // Track user interaction so fitBounds never hijacks their pan.
    const markInteracted = () => { map._userHasInteracted = true }
    map.on('zoomstart', markInteracted)
    map.on('dragstart', markInteracted)

    mapRef.current = map

    // Some browsers (especially Safari) size the container after the
    // first paint. Call invalidateSize once on the next tick.
    const t = setTimeout(() => map.invalidateSize(), 100)

    return () => {
      clearTimeout(t)
      map.off('zoomstart', markInteracted)
      map.off('dragstart', markInteracted)
      map.remove()
      mapRef.current = null
    }
  }, [])

  // ── 3. Rebuild dynamic layers when data changes ─────────────────────
  useEffect(() => {
    const map = mapRef.current
    if (!map || !data) return

    // Remove previous dynamic layers
    layersRef.current.forEach((l) => map.removeLayer(l))
    layersRef.current = []

    const bounds = []
    const add = (layer) => {
      layer.addTo(map)
      layersRef.current.push(layer)
    }

    // ---- Origin marker -------------------------------------------------
    if (data.origin?.lat != null) {
      const { lat, lng, name } = data.origin
      add(L.marker([lat, lng], {
        icon: L.divIcon({
          className: '',
          html: `<div style="background:#d1495b;color:#fff;width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:14px;border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,0.45)">🏠</div>`,
          iconSize: [28, 28],
          iconAnchor: [14, 14],
        }),
      }).bindPopup(`<b>Origin:</b> ${name || ''}`))
      bounds.push([lat, lng])
    }

    // ---- Confirmed destination marker ----------------------------------
    if (data.destination?.lat != null) {
      const { lat, lng, name } = data.destination
      add(L.marker([lat, lng], {
        icon: L.divIcon({
          className: '',
          html: `<div style="background:#3f9b5f;color:#fff;width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:14px;border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,0.45)">📍</div>`,
          iconSize: [28, 28],
          iconAnchor: [14, 14],
        }),
      }).bindPopup(`<b>${name}</b>`))
      bounds.push([lat, lng])
    }

    // ---- Candidate markers (numbered, before confirmation) -------------
    (data.candidates || []).forEach((c, i) => {
      if (c.lat == null) return
      add(L.marker([c.lat, c.lng], {
        icon: L.divIcon({
          className: '',
          html: `<div style="background:#1c1c1e;color:#fff;width:24px;height:24px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;border:2px solid #4a4a4c;box-shadow:0 1px 4px rgba(0,0,0,0.4)">${i + 1}</div>`,
          iconSize: [24, 24],
          iconAnchor: [12, 12],
        }),
      }).bindPopup(
        `<b>${i + 1}. ${c.name}</b>` +
        (c.distance_km ? `<br/><small>${c.distance_km} km from origin</small>` : '')
      ))
      bounds.push([c.lat, c.lng])
    })

    // ---- Itinerary: colored stops + route polylines -------------------
    const itinerary = data.itinerary_stops || []
    itinerary.forEach((day, dayIdx) => {
      const color = DAY_COLORS[dayIdx % DAY_COLORS.length]
      const dayStops = (day.stops || []).filter((s) => s.lat != null)
      if (!dayStops.length) return

      const coords = dayStops.map((s) => [s.lat, s.lng])

      // Dashed line from origin to the first stop of the FIRST day only.
      if (dayIdx === 0 && data.origin?.lat != null) {
        add(L.polyline(
          [[data.origin.lat, data.origin.lng], coords[0]],
          { color, weight: 2, opacity: 0.5, dashArray: '6 8' }
        ))
      }

      // Solid route between consecutive stops of the same day.
      if (coords.length >= 2) {
        add(L.polyline(coords, {
          color, weight: 3, opacity: 0.85,
          lineCap: 'round', lineJoin: 'round',
        }))
      }

      // Numbered stop markers, colored by day.
      dayStops.forEach((s, i) => {
        add(L.marker([s.lat, s.lng], {
          icon: L.divIcon({
            className: '',
            html: `<div style="background:${color};color:#fff;width:26px;height:26px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;border:2px solid #fff;box-shadow:0 1px 5px rgba(0,0,0,0.5)">${i + 1}</div>`,
            iconSize: [26, 26],
            iconAnchor: [13, 13],
          }),
        }).bindPopup(
          `<b>Day ${day.day} — Stop ${i + 1}</b><br/>${s.name}` +
          (day.date ? `<br/><small>${day.date}</small>` : '')
        ))
        bounds.push([s.lat, s.lng])
      })
    })

    // ---- Auto-fit ONCE, and only if the user hasn't interacted --------
    // Without this guard, every chat reply triggers a state update,
    // which re-runs this effect and snaps the view back to fitBounds.
    if (bounds.length > 0 && !initialFitDoneRef.current && !map._userHasInteracted) {
      map.fitBounds(bounds, { padding: [40, 40], maxZoom: 12 })
      initialFitDoneRef.current = true
    }
  }, [data])

  // ── 4. Resize observer — Leaflet needs to know when the container
  //       changes size (e.g., when a sidebar collapses). Without this,
  //       tiles may render half-blank after a layout shift. ──────────
  useEffect(() => {
    if (!containerRef.current) return
    const observer = new ResizeObserver(() => {
      mapRef.current?.invalidateSize()
    })
    observer.observe(containerRef.current)
    return () => observer.disconnect()
  }, [])

  return (
    <div className="h-full flex flex-col min-h-0">
      {/* Day legend — appears once an itinerary exists */}
      {data?.itinerary_stops?.length > 0 && (
        <div className="flex flex-wrap gap-3 px-3 py-2 border-b border-border text-xs shrink-0">
          {data.itinerary_stops.map((day, i) => (
            <div key={day.day} className="flex items-center gap-2">
              <span
                className="inline-block w-3 h-3 rounded-full"
                style={{ background: DAY_COLORS[i % DAY_COLORS.length] }}
              />
              <span className="font-medium">Day {day.day}</span>
              {day.date && <span className="text-muted">{day.date}</span>}
            </div>
          ))}
        </div>
      )}

      {/* Map container — flex-1 + min-h-0 so it takes remaining height
          and can shrink. Without min-h-0, flexbox won't shrink it and
          the map overflows the pane. */}
      <div ref={containerRef} className="flex-1 min-h-0 w-full" />
    </div>
  )
}