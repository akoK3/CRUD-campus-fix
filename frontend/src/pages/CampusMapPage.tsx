import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { supabase } from '../lib/supabase'
import { useAuth } from '../auth/AuthProvider'
import { nameById, statusNameById, useLookups } from '../lib/useLookups'
import { Empty, ErrorNote, Loading, PriorityBadge, StatusBadge } from '../components/ui'
import { OPEN_STATUSES } from '../lib/domain'
import { formatRelative } from '../lib/format'
import type { Tables } from '../lib/database.types'

// Drop the campus image at frontend/public/campus-map.png and it appears here.
const MAP_IMAGE = '/campus-map.png'

interface BuildingPin extends Tables<'buildings'> {
  open: number
  total: number
  emergency: number
}

export default function CampusMapPage() {
  const { isSystemAdmin } = useAuth()
  const { lookups, ready } = useLookups()

  const [buildings, setBuildings] = useState<Tables<'buildings'>[]>([])
  const [tickets, setTickets] = useState<Tables<'tickets'>[]>([])
  const [buildingOfTicket, setBuildingOfTicket] = useState<Record<number, number>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const [imageOk, setImageOk] = useState<boolean | null>(null)

  const [selected, setSelected] = useState<number | null>(null)
  const [hovered, setHovered] = useState<number | null>(null)
  const [placing, setPlacing] = useState<number | null>(null)
  const [saving, setSaving] = useState(false)

  const imgRef = useRef<HTMLImageElement>(null)

  const load = useCallback(async () => {
    const [b, t] = await Promise.all([
      supabase.from('buildings').select('*').order('name'),
      supabase.from('tickets').select('*').is('deleted_at', null).limit(5000),
    ])
    if (b.error || t.error) {
      setError(b.error ?? t.error)
      setLoading(false)
      return
    }
    setBuildings(b.data ?? [])
    setTickets(t.data ?? [])

    // Resolve ticket -> building through location -> floor.
    const locationIds = [...new Set((t.data ?? []).map((x) => x.location_id).filter(Boolean))] as number[]
    if (locationIds.length) {
      const { data: locs } = await supabase
        .from('locations')
        .select('location_id, floors(building_id)')
        .in('location_id', locationIds)

      const byLocation = new Map<number, number>()
      for (const l of locs ?? []) {
        const floor = l.floors as unknown as { building_id: number } | null
        if (floor?.building_id) byLocation.set(l.location_id, floor.building_id)
      }
      const map: Record<number, number> = {}
      for (const x of t.data ?? []) {
        if (x.location_id && byLocation.has(x.location_id)) {
          map[x.ticket_id] = byLocation.get(x.location_id)!
        }
      }
      setBuildingOfTicket(map)
    }
    setLoading(false)
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const pins: BuildingPin[] = useMemo(() => {
    const counts = new Map<number, { open: number; total: number; emergency: number }>()
    for (const b of buildings) counts.set(b.building_id, { open: 0, total: 0, emergency: 0 })

    for (const t of tickets) {
      const bid = buildingOfTicket[t.ticket_id]
      if (!bid) continue
      const entry = counts.get(bid)
      if (!entry) continue
      entry.total += 1
      const s = statusNameById(lookups, t.status_id)
      if (s && OPEN_STATUSES.includes(s)) entry.open += 1
      if (t.is_emergency && s && OPEN_STATUSES.includes(s)) entry.emergency += 1
    }

    return buildings.map((b) => ({ ...b, ...counts.get(b.building_id)! }))
  }, [buildings, tickets, buildingOfTicket, lookups])

  const placed = pins.filter((p) => p.map_x != null && p.map_y != null)
  const unplaced = pins.filter((p) => p.map_x == null || p.map_y == null)

  const selectedTickets = useMemo(() => {
    if (selected == null) return []
    return tickets
      .filter((t) => buildingOfTicket[t.ticket_id] === selected)
      .sort((a, b) => Number(b.is_emergency) - Number(a.is_emergency))
  }, [selected, tickets, buildingOfTicket])

  async function placePin(e: React.MouseEvent<HTMLDivElement>) {
    if (placing == null || !imgRef.current) return
    const rect = imgRef.current.getBoundingClientRect()
    const x = ((e.clientX - rect.left) / rect.width) * 100
    const y = ((e.clientY - rect.top) / rect.height) * 100
    if (x < 0 || x > 100 || y < 0 || y > 100) return

    setSaving(true)
    const { error: err } = await supabase
      .from('buildings')
      .update({ map_x: Number(x.toFixed(2)), map_y: Number(y.toFixed(2)) })
      .eq('building_id', placing)
    setSaving(false)
    if (err) return setError(err)
    setPlacing(null)
    await load()
  }

  if (loading || !ready) return <Loading />

  return (
    <div className="content">
      <h1>Campus map</h1>
      <p className="muted small">
        Open tickets by building. Counts respect your access level — you see what you could
        see in the queue, nothing more.
      </p>

      <ErrorNote error={error} />

      {imageOk === false && (
        <div className="alert warn">
          <strong>No campus map image found.</strong> Save the campus map as{' '}
          <code>frontend/public/campus-map.png</code> and reload. Pin positions are stored as
          percentages, so they stay correct whatever size the image is.
        </div>
      )}

      {isSystemAdmin && placing != null && (
        <div className="alert ok">
          Click the map to place <strong>{pins.find((p) => p.building_id === placing)?.name}</strong>.{' '}
          <button className="sm" onClick={() => setPlacing(null)}>Cancel</button>
        </div>
      )}

      <div className="card" style={{ padding: '0.5rem' }}>
        <div
          className={`map-wrap ${placing != null ? 'placing' : ''}`}
          onClick={placePin}
        >
          <img
            ref={imgRef}
            src={MAP_IMAGE}
            alt="Campus map"
            onLoad={() => setImageOk(true)}
            onError={() => setImageOk(false)}
            style={{ display: imageOk === false ? 'none' : 'block' }}
          />

          {imageOk !== false &&
            placed.map((b) => {
              const tone = b.emergency > 0 ? 'danger' : b.open > 0 ? 'accent' : 'quiet'
              return (
                <button
                  key={b.building_id}
                  className={`map-pin ${tone} ${selected === b.building_id ? 'active' : ''}`}
                  style={{ left: `${b.map_x}%`, top: `${b.map_y}%` }}
                  title={`${b.name} — ${b.open} open of ${b.total}`}
                  aria-label={`${b.name}: ${b.open} open tickets, ${b.total} total tickets${b.emergency ? `, ${b.emergency} emergency` : ''}`}
                  onMouseEnter={() => setHovered(b.building_id)}
                  onMouseLeave={() => setHovered(null)}
                  onFocus={() => setHovered(b.building_id)}
                  onBlur={() => setHovered(null)}
                  onClick={(e) => {
                    e.stopPropagation()
                    setSelected(selected === b.building_id ? null : b.building_id)
                  }}
                >
                  <span className="map-pin-count">{b.open}</span>
                  <span className="map-pin-label">{b.name}</span>
                  {(hovered === b.building_id || selected === b.building_id) && (
                    <span className="map-tooltip" role="status">
                      <strong>{b.name}</strong>
                    </span>
                  )}
                </button>
              )
            })}
        </div>
      </div>

      <div className="row" style={{ alignItems: 'flex-start' }}>
        <div className="card" style={{ flex: '1 1 260px' }}>
          <h3>Buildings</h3>
          {pins.map((b) => (
            <div key={b.building_id} className="bar-row">
              <button
                className="sm"
                style={{ flex: 1, textAlign: 'left', border: 'none', background: 'none', padding: '0.2rem 0' }}
                onClick={() => setSelected(selected === b.building_id ? null : b.building_id)}
              >
                {b.name}
              </button>
              <span className={`badge ${b.emergency > 0 ? 'danger' : b.open > 0 ? 'accent' : ''}`}>
                {b.open} open
              </span>
              {isSystemAdmin && (
                <button
                  className="sm"
                  disabled={saving}
                  onClick={() => setPlacing(b.building_id)}
                  title="Click, then click the map"
                >
                  {b.map_x == null ? 'Place' : 'Move'}
                </button>
              )}
            </div>
          ))}

          {unplaced.length > 0 && (
            <div className="hint">
              {unplaced.length} building{unplaced.length === 1 ? '' : 's'} not yet placed on
              the map{isSystemAdmin ? '. Use Place to position them.' : '.'}
            </div>
          )}
        </div>

        <div className="card" style={{ flex: '2 1 380px' }}>
          <h3>
            {selected == null
              ? 'Select a building'
              : (pins.find((p) => p.building_id === selected)?.name ?? 'Tickets')}
          </h3>

          {selected == null ? (
            <p className="muted small">
              Click a pin or a building name to see its tickets.
            </p>
          ) : selectedTickets.length === 0 ? (
            <Empty title="No tickets for this building" />
          ) : (
            selectedTickets.map((t) => (
              <Link key={t.ticket_id} to={`/tickets/${t.ticket_id}`} className="ticket-row">
                <div className="title">
                  {t.is_emergency && <span className="badge danger">Emergency</span>} #
                  {t.ticket_id} · {t.title}
                </div>
                <div className="ticket-meta">
                  <StatusBadge status={statusNameById(lookups, t.status_id)} />
                  <PriorityBadge
                    priority={nameById(lookups.priorities, 'priority_id', t.priority_id)}
                  />
                  <span className="small muted">{formatRelative(t.created_at)}</span>
                </div>
              </Link>
            ))
          )}
        </div>
      </div>
    </div>
  )
}
