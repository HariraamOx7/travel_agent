import { useEffect, useState, useRef } from 'react'
import Map, { Marker, Popup, Source, Layer, NavigationControl } from 'react-map-gl/maplibre'
import 'maplibre-gl/dist/maplibre-gl.css'
import { api } from '../api'

// OSM is used only for the displayed basemap. Ola can remain in use by the
// backend for geocoding and routing without exposing its vector-tile style to
// MapLibre.
const OSM_STYLE = {
  version: 8,
  sources: {
    openstreetmap: {
      type: 'raster',
      tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
      tileSize: 256,
      attribution: '© OpenStreetMap contributors',
    },
  },
  layers: [
    { id: 'openstreetmap', type: 'raster', source: 'openstreetmap' },
  ],
}

export default function MapTab({ sessionId }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState(null)   // popup state
  const [mapError, setMapError] = useState(null)
  const mapRef = useRef(null)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const d = await api.getMap(sessionId)
        if (!cancelled) setData(d)
      } catch (e) {
        console.error(e)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    const t = setInterval(load, 4000)
    return () => { cancelled = true; clearInterval(t) }
  }, [sessionId])

  // Automatically frame origin, destination, and candidates within view
  useEffect(() => {
    if (!mapRef.current || !data) return
    const points = []
    if (data.origin?.lng != null && data.origin?.lat != null) {
      points.push([data.origin.lng, data.origin.lat])
    }
    if (data.destination?.lng != null && data.destination?.lat != null) {
      points.push([data.destination.lng, data.destination.lat])
    }
    if (data.candidates) {
      data.candidates.forEach((c) => {
        if (c.lng != null && c.lat != null) points.push([c.lng, c.lat])
      })
    }
    if (points.length >= 2) {
      const minLng = Math.min(...points.map((p) => p[0]))
      const maxLng = Math.max(...points.map((p) => p[0]))
      const minLat = Math.min(...points.map((p) => p[1]))
      const maxLat = Math.max(...points.map((p) => p[1]))
      mapRef.current.fitBounds(
        [
          [minLng, minLat],
          [maxLng, maxLat],
        ],
        { padding: 70, maxZoom: 13, duration: 1000 }
      )
    } else if (points.length === 1) {
      mapRef.current.flyTo({
        center: points[0],
        zoom: 8,
        duration: 1000,
      })
    }
  }, [data])

  if (loading) return <div className="text-muted text-sm">Loading map…</div>
  if (!data) return <div className="text-muted text-sm">No map data.</div>

  const centre =
    data.destination && data.origin ? [
      (data.origin.lng + data.destination.lng) / 2,
      (data.origin.lat + data.destination.lat) / 2,
    ]
    : data.destination ? [data.destination.lng, data.destination.lat]
    : data.candidates?.[0] ? [data.candidates[0].lng, data.candidates[0].lat]
    : data.origin ? [data.origin.lng, data.origin.lat]
    : null

  if (!centre) {
    return (
      <div className="text-muted text-sm">
        No location yet — chat with the agent to pick an origin and destination.
      </div>
    )
  }

  // Build route line GeoJSON for the origin → destination leg.
  const routeGeoJSON = data.origin && data.destination ? {
    type: 'Feature',
    properties: {},
    geometry: {
      type: 'LineString',
      coordinates: [
        [data.origin.lng, data.origin.lat],
        [data.destination.lng, data.destination.lat],
      ],
    },
  } : null

  // Build day-by-day itinerary paths.
  const itineraryLines = (data.itinerary_stops || [])
    .filter((day) => day.stops.length > 1)
    .map((day) => ({
      day: day.day,
      geojson: {
        type: 'Feature',
        properties: {},
        geometry: {
          type: 'LineString',
          coordinates: day.stops.map((s) => [s.lng, s.lat]),
        },
      },
    }))

  return (
    <div style={{ height: '520px', width: '100%', borderRadius: '12px', overflow: 'hidden' }}>
      <Map
        ref={mapRef}
        initialViewState={{
          longitude: centre[0],
          latitude: centre[1],
          zoom: 6,
        }}
        mapStyle={OSM_STYLE}
        style={{ width: '100%', height: '100%' }}
        onClick={() => setSelected(null)}
        onError={(event) => {
          const message = event?.error?.message || 'Unable to load the Ola Maps basemap.'
          console.error('Ola Maps error:', event?.error)
          setMapError(message)
        }}
      >
        <NavigationControl position="top-right" />

        {/* Origin pin */}
        {data.origin && (
          <Marker
            longitude={data.origin.lng}
            latitude={data.origin.lat}
            anchor="bottom"
            onClick={(e) => {
              e.originalEvent.stopPropagation()
              setSelected({ kind: 'origin', payload: data.origin })
            }}
          >
            <div
              style={{
                width: 26, height: 26, borderRadius: '50%',
                background: '#d1495b', color: '#fff',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                border: '2px solid #fff', cursor: 'pointer',
                boxShadow: '0 2px 6px rgba(0,0,0,0.4)',
              }}
              title={`Origin: ${data.origin.name}`}
            >
              ★
            </div>
          </Marker>
        )}

        {/* Confirmed destination */}
        {data.destination && (
          <Marker
            longitude={data.destination.lng}
            latitude={data.destination.lat}
            anchor="bottom"
            onClick={(e) => {
              e.originalEvent.stopPropagation()
              setSelected({ kind: 'destination', payload: data.destination })
            }}
          >
            <div
              style={{
                width: 26, height: 26, borderRadius: '50%',
                background: '#3f9b5f', color: '#fff',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                border: '2px solid #fff', cursor: 'pointer',
                boxShadow: '0 2px 6px rgba(0,0,0,0.4)',
              }}
              title={`Destination: ${data.destination.name}`}
            >
              ⚑
            </div>
          </Marker>
        )}

        {/* Candidate numbered pins (before confirmation) */}
        {!data.destination &&
          (data.candidates || []).map((c, i) => (
            <Marker
              key={c.name}
              longitude={c.lng}
              latitude={c.lat}
              anchor="center"
              onClick={(e) => {
                e.originalEvent.stopPropagation()
                setSelected({ kind: 'candidate', payload: { ...c, index: i + 1 } })
              }}
            >
              <div
                style={{
                  width: 24, height: 24, borderRadius: '50%',
                  background: '#1c1c1e', color: '#fff',
                  border: '2px solid #4a4a4c',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 12, fontWeight: 600, cursor: 'pointer',
                  boxShadow: '0 2px 6px rgba(0,0,0,0.4)',
                }}
              >
                {i + 1}
              </div>
            </Marker>
          ))}

        {/* Origin → destination route line */}
        {routeGeoJSON && (
          <Source id="route" type="geojson" data={routeGeoJSON}>
            <Layer
              id="route-line"
              type="line"
              paint={{
                'line-color': '#ff8a3d',
                'line-width': 2,
                'line-dasharray': [2, 2],
              }}
            />
          </Source>
        )}

        {/* Per-day itinerary paths */}
        {itineraryLines.map((it) => (
          <Source key={`day-${it.day}`} id={`day-${it.day}`} type="geojson" data={it.geojson}>
            <Layer
              id={`day-${it.day}-line`}
              type="line"
              paint={{ 'line-color': '#4a90d9', 'line-width': 2, 'line-opacity': 0.75 }}
            />
          </Source>
        ))}

        {/* Itinerary stop dots */}
        {(data.itinerary_stops || []).flatMap((day) =>
          day.stops.map((s) => (
            <Marker
              key={`${day.day}-${s.name}`}
              longitude={s.lng}
              latitude={s.lat}
              anchor="center"
              onClick={(e) => {
                e.originalEvent.stopPropagation()
                setSelected({
                  kind: 'itinerary',
                  payload: { ...s, day: day.day },
                })
              }}
            >
              <div
                style={{
                  width: 10, height: 10, borderRadius: '50%',
                  background: '#fff', border: '2px solid #1c1c1e',
                  cursor: 'pointer',
                }}
              />
            </Marker>
          ))
        )}

        {/* Popup for the selected marker */}
        {selected && (
          <Popup
            longitude={selected.payload.lng}
            latitude={selected.payload.lat}
            anchor="top"
            onClose={() => setSelected(null)}
            closeOnClick={false}
            className="text-black"
          >
            <div style={{ fontSize: 13 }}>
              {selected.kind === 'origin' && (
                <>
                  <strong>Origin</strong>
                  <div>{selected.payload.name}</div>
                </>
              )}
              {selected.kind === 'destination' && (
                <>
                  <strong>Destination</strong>
                  <div>{selected.payload.name}</div>
                </>
              )}
              {selected.kind === 'candidate' && (
                <>
                  <strong>
                    {selected.payload.index}. {selected.payload.name}
                  </strong>
                  {selected.payload.distance_km != null && (
                    <div style={{ color: '#666' }}>
                      {selected.payload.distance_km} km from origin
                    </div>
                  )}
                </>
              )}
              {selected.kind === 'itinerary' && (
                <>
                  <strong>
                    Day {selected.payload.day}: {selected.payload.name}
                  </strong>
                </>
              )}
            </div>
          </Popup>
        )}

        {mapError && (
          <div
            style={{
              position: 'absolute', left: 12, bottom: 12, zIndex: 2,
              maxWidth: 360, padding: '8px 10px', borderRadius: 8,
              background: 'rgba(127, 29, 29, 0.92)', color: '#fff', fontSize: 12,
            }}
          >
            Map tiles could not load: {mapError}
          </div>
        )}
      </Map>
    </div>
  )
}
