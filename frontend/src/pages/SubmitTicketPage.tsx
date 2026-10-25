import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { supabase } from '../lib/supabase'
import { uploadAttachment, validateImage } from '../lib/attachments'
import { useAuth } from '../auth/AuthProvider'
import { useLookups, statusIdByName } from '../lib/useLookups'
import { ErrorNote, Loading, Modal, Spinner } from '../components/ui'
import {
  CAMPUS_SAFETY_PHONE,
  EMERGENCY_PHONE,
  detectEmergency,
} from '../lib/emergency'
import {
  ACCEPTED_IMAGE_TYPES,
  MAX_ATTACHMENT_BYTES,
  MIN_DESCRIPTION_LENGTH,
  STATUS,
  UNSPECIFIED_FLOOR,
} from '../lib/domain'
import { classifyTicket } from '../lib/classify'
import { submissionText } from '../lib/submission'
import { formatBytes } from '../lib/format'

export default function SubmitTicketPage() {
  const nav = useNavigate()
  const { profile } = useAuth()
  const { lookups, ready } = useLookups()

  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [issueTypeId, setIssueTypeId] = useState<string>('')
  const [buildingId, setBuildingId] = useState<string>('')
  const [floorId, setFloorId] = useState<string>('')
  const [areaTypeId, setAreaTypeId] = useState<string>('')
  const [roomNumber, setRoomNumber] = useState('')
  const [file, setFile] = useState<File | null>(null)

  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [emergencyAck, setEmergencyAck] = useState(false)
  const [showEmergency, setShowEmergency] = useState(false)

  // "Unspecified" first, then ground floor, then numbered floors in order.
  const floorsForBuilding = useMemo(() => {
    const rows = lookups.floors.filter((f) => String(f.building_id) === buildingId)
    const rank = (n: string) =>
      n === UNSPECIFIED_FLOOR ? -2 : n === 'G' ? -1 : Number(n)
    return [...rows].sort((a, b) => rank(a.floor_number) - rank(b.floor_number))
  }, [lookups.floors, buildingId])

  const emergency = useMemo(
    () => detectEmergency(title, description),
    [title, description],
  )

  const descriptionTooShort =
    description.trim().length > 0 && description.trim().length < MIN_DESCRIPTION_LENGTH

  function onPickFile(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0] ?? null
    setError(null)
    if (!f) return setFile(null)
    const problem = validateImage(f)
    if (problem) {
      setError(new Error(problem))
      e.target.value = ''
      return setFile(null)
    }
    setFile(f)
  }

  async function reallySubmit() {
    if (!profile) return
    setBusy(true)
    setError(null)

    try {
      // 1. Optional location. A ticket without a precise location is still
      //    valid; an outdoor issue has no floor at all.
      //
      //    A building with no floor chosen must still record the building.
      //    Since a location reaches its building only through a floor, fall
      //    back to that building's "Unspecified" floor rather than dropping
      //    the building on the floor, which is what used to happen.
      let locationId: number | null = null
      let locationFailed = false

      let effectiveFloorId: number | null = floorId ? Number(floorId) : null
      if (effectiveFloorId === null && buildingId) {
        effectiveFloorId =
          lookups.floors.find(
            (f) =>
              String(f.building_id) === buildingId &&
              f.floor_number === UNSPECIFIED_FLOOR,
          )?.floor_id ?? null
      }

      if (effectiveFloorId !== null || roomNumber.trim() || areaTypeId) {
        const { data: loc, error: locErr } = await supabase
          .from('locations')
          .insert({
            floor_id: effectiveFloorId,
            area_type_id: areaTypeId ? Number(areaTypeId) : null,
            room_number: roomNumber.trim() || null,
          })
          .select('location_id')
          .single()

        if (locErr) {
          // A location failure must not cost the user their report, but it
          // must not be invisible either. Swallowing this is what left every
          // ticket unplaceable on the map.
          locationFailed = true
          console.error('Could not save ticket location:', locErr)
        } else if (loc) {
          locationId = loc.location_id
        }
      }

      const newStatus = statusIdByName(lookups, STATUS.NEW)
      if (!newStatus) throw new Error('Status table is not seeded. Contact an administrator.')

      // 2. Create the ticket. THIS IS THE COMMIT POINT. Everything after
      //    this may fail without costing the user their submission.
      const { data: ticket, error: ticketErr } = await supabase
        .from('tickets')
        .insert({
          reported_by: profile.user_id,
          ...submissionText(title, description),
          issue_type_id: issueTypeId ? Number(issueTypeId) : null,
          location_id: locationId,
          status_id: newStatus,
          is_emergency: emergency.isEmergency,
        })
        .select('ticket_id')
        .single()

      if (ticketErr) throw ticketErr
      const ticketId = ticket.ticket_id

      // Start AI immediately after saving; never await it during submission.
      // Slow inference or an unavailable backend cannot fail a saved ticket.
      void classifyTicket(ticketId).catch(() => undefined)

      // 3. Attachment. Best effort, but a failure is reported on the ticket
      //    page rather than silently dropped.
      let attachmentFailed = false
      if (file) {
        const upErr = await uploadAttachment(ticketId, file, profile.user_id)
        if (upErr) {
          attachmentFailed = true
          console.error('Could not upload photo:', upErr)
        }
      }

      nav(`/tickets/${ticketId}`, { state: { locationFailed, attachmentFailed } })
    } catch (err) {
      setError(err)
      setBusy(false)
    }
  }

  function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    // V-004: every ticket says where. Outdoor spots have no building, so an
    // area type or a landmark is enough on its own.
    if (!buildingId && !areaTypeId && !roomNumber.trim()) {
      setError(
        new Error(
          'Tell us where the problem is: choose a building, or for outdoor issues pick an area type or name the nearest landmark.',
        ),
      )
      return
    }
    if (emergency.isEmergency && !emergencyAck) {
      setShowEmergency(true)
      return
    }
    void reallySubmit()
  }

  if (!ready) return <Loading />

  return (
    <div className="content narrow">
      <h1>Report an issue</h1>
      <p className="muted small">
        Tell us what's wrong and where. You'll get updates as it's worked on.
      </p>

      <ErrorNote error={error} />

      {emergency.isEmergency && (
        <div className="alert error" role="alert">
          <strong>This may be an emergency.</strong> If anyone is in danger, call{' '}
          <strong>{EMERGENCY_PHONE}</strong> or Campus Safety at{' '}
          <strong>{CAMPUS_SAFETY_PHONE}</strong> now. Do not wait for a ticket.
        </div>
      )}

      <form onSubmit={onSubmit} className="card">
        <div className="field">
          <label htmlFor="title">What's the problem?</label>
          <input
            id="title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
            maxLength={120}
            placeholder="Radiator won't turn off"
          />
        </div>

        <div className="field">
          <label htmlFor="desc">Describe it</label>
          <textarea
            id="desc"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            required
            minLength={MIN_DESCRIPTION_LENGTH}
            placeholder="The radiator in my room has been stuck on since Friday. The room is about 85 degrees and the valve won't turn."
          />
          <div className="hint">
            {descriptionTooShort
              ? `A bit more detail, please — at least ${MIN_DESCRIPTION_LENGTH} characters so we can route this correctly.`
              : 'Include what you see, when it started, and anything you already tried.'}
          </div>
        </div>

        <div className="field">
          <label htmlFor="type">Category</label>
          <select
            id="type"
            value={issueTypeId}
            onChange={(e) => setIssueTypeId(e.target.value)}
          >
            <option value="">Not sure — let CampusFix decide</option>
            {lookups.issueTypes.map((it) => (
              <option key={it.issue_type_id} value={it.issue_type_id}>
                {it.name}
              </option>
            ))}
          </select>
          <div className="hint">
            Choose a category if you know it, or leave it blank for CampusFix to classify.
            Unclear reports may need staff review.
          </div>
        </div>

        <h3 style={{ marginTop: '1.25rem' }}>Where is it?</h3>

        <div className="row">
          <div className="field">
            <label htmlFor="building">Building</label>
            <select
              id="building"
              value={buildingId}
              onChange={(e) => {
                setBuildingId(e.target.value)
                setFloorId('')
              }}
            >
              <option value="">Select…</option>
              {lookups.buildings.map((b) => (
                <option key={b.building_id} value={b.building_id}>
                  {b.name}
                </option>
              ))}
            </select>
          </div>

          <div className="field">
            <label htmlFor="floor">Floor</label>
            <select
              id="floor"
              value={floorId}
              onChange={(e) => setFloorId(e.target.value)}
              disabled={!buildingId}
            >
              <option value="">Select…</option>
              {floorsForBuilding.map((f) => (
                <option key={f.floor_id} value={f.floor_id}>
                  {f.floor_number === UNSPECIFIED_FLOOR
                    ? "Not sure / anywhere in the building"
                    : f.floor_number === 'G'
                      ? 'Ground floor'
                      : `Floor ${f.floor_number}`}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="row">
          <div className="field">
            <label htmlFor="room">Room or nearest landmark</label>
            <input
              id="room"
              value={roomNumber}
              onChange={(e) => setRoomNumber(e.target.value)}
              placeholder="312"
            />
          </div>

          <div className="field">
            <label htmlFor="area">Area type</label>
            <select
              id="area"
              value={areaTypeId}
              onChange={(e) => setAreaTypeId(e.target.value)}
            >
              <option value="">Select…</option>
              {lookups.areaTypes.map((a) => (
                <option key={a.area_type_id} value={a.area_type_id}>
                  {a.name}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="field">
          <label htmlFor="photo">Photo (optional)</label>
          <input
            id="photo"
            type="file"
            accept={ACCEPTED_IMAGE_TYPES.join(',')}
            onChange={onPickFile}
          />
          <div className="hint">
            JPEG, PNG, WebP, GIF, or HEIC. Up to {formatBytes(MAX_ATTACHMENT_BYTES)}.
            {file && ` Selected: ${file.name} (${formatBytes(file.size)})`}
          </div>
        </div>

        <button className="primary" type="submit" disabled={busy || descriptionTooShort}>
          {busy ? <Spinner /> : 'Submit ticket'}
        </button>
      </form>

      {showEmergency && (
        <Modal title="This looks like an emergency" onClose={() => setShowEmergency(false)}>
          <div className="alert error">
            We detected {emergency.reasons.join(', ')} in your report.
          </div>
          <p>
            <strong>A ticket is not an emergency response.</strong> Nobody is watching this
            queue right now.
          </p>
          <p>
            If anyone is in danger, call <strong>{EMERGENCY_PHONE}</strong>. For campus
            incidents, call Campus Safety at <strong>{CAMPUS_SAFETY_PHONE}</strong>.
          </p>
          <p className="small muted">
            You can still file this for the record. It will be flagged as an emergency and
            sent to Campus Safety at the highest priority.
          </p>
          <div className="actions">
            <button className="primary" onClick={() => setShowEmergency(false)} type="button">
              Go back
            </button>
            <button
              type="button"
              onClick={() => {
                setEmergencyAck(true)
                setShowEmergency(false)
                void reallySubmit()
              }}
            >
              I've called — file the report
            </button>
          </div>
        </Modal>
      )}
    </div>
  )
}
