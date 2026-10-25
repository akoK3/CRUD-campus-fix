import { useCallback, useEffect, useMemo, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { supabase, ATTACHMENT_BUCKET } from '../lib/supabase'
import { useAuth } from '../auth/AuthProvider'
import {
  activeDepartments,
  nameById,
  statusIdByName,
  statusNameById,
  useLookups,
} from '../lib/useLookups'
import { Empty, ErrorNote, Loading, Modal, PriorityBadge, Spinner, StatusBadge } from '../components/ui'
import { daysSince, formatDateTime, formatRelative, humanize } from '../lib/format'
import {
  ACCEPTED_IMAGE_TYPES,
  ALLOWED_TRANSITIONS,
  REOPEN_WINDOW_DAYS,
  STATUS,
  WITHDRAWABLE,
} from '../lib/domain'
import type { Tables } from '../lib/database.types'
import { uploadAttachment, validateImage } from '../lib/attachments'
import { startPendingTicketPolling } from '../lib/ticketPolling'
import { onClassificationFinished } from '../lib/classify'
import { TicketAiDetails } from '../components/TicketAiDetails'

interface CommentRow extends Tables<'comments'> {
  users: { first_name: string; last_name: string } | null
}
interface HistoryRow extends Tables<'ticket_history'> {
  users: { first_name: string; last_name: string } | null
}
interface Person {
  user_id: string
  first_name: string
  last_name: string
}

export default function TicketDetailPage() {
  const { id } = useParams()
  const ticketId = Number(id)
  const nav = useNavigate()
  const routerLocation = useLocation()
  // Set by the submit page when the location row could not be written.
  const navState = routerLocation.state as
    | { locationFailed?: boolean; attachmentFailed?: boolean }
    | null
  const locationFailed = Boolean(navState?.locationFailed)
  const attachmentFailed = Boolean(navState?.attachmentFailed)
  const { profile, isStaff, isAdmin } = useAuth()
  const { lookups, ready } = useLookups()

  const [ticket, setTicket] = useState<Tables<'tickets'> | null>(null)
  const [comments, setComments] = useState<CommentRow[]>([])
  const [history, setHistory] = useState<HistoryRow[]>([])
  const [attachments, setAttachments] = useState<Tables<'attachments'>[]>([])
  const [signedUrls, setSignedUrls] = useState<Record<number, string>>({})
  const [reopenReq, setReopenReq] = useState<Tables<'ticket_reopen_requests'> | null>(null)
  const [assignee, setAssignee] = useState<Person | null>(null)
  const [deptStaff, setDeptStaff] = useState<Person[]>([])

  const [pollingExpired, setPollingExpired] = useState(false)
  const [loading, setLoading] = useState(true)
  const [notFound, setNotFound] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  const [draft, setDraft] = useState('')
  const [internal, setInternal] = useState(false)
  const [resolution, setResolution] = useState('')
  const [showResolve, setShowResolve] = useState(false)
  const [showReopen, setShowReopen] = useState(false)
  const [reopenReason, setReopenReason] = useState('')
  const [decisionNote, setDecisionNote] = useState('')
  const [showTransfer, setShowTransfer] = useState(false)
  const [transferTo, setTransferTo] = useState('')
  const [transferReason, setTransferReason] = useState('')

  const load = useCallback(async () => {
    const { data: t, error: tErr } = await supabase
      .from('tickets')
      .select('*')
      .eq('ticket_id', ticketId)
      .maybeSingle()

    if (tErr) {
      setError(tErr)
      setLoading(false)
      return
    }
    // RLS returns nothing rather than an error for another user's ticket.
    // An empty result here IS the access denial.
    if (!t) {
      setNotFound(true)
      setLoading(false)
      return
    }
    setTicket((current) => current?.ticket_id === t.ticket_id ? latestTicket(current, t) : t)

    const [c, h, a, r, asg] = await Promise.all([
      supabase
        .from('comments')
        .select('*, users(first_name, last_name)')
        .eq('ticket_id', ticketId)
        .order('created_at'),
      supabase
        .from('ticket_history')
        .select('*, users(first_name, last_name)')
        .eq('ticket_id', ticketId)
        .order('created_at', { ascending: false }),
      supabase.from('attachments').select('*').eq('ticket_id', ticketId),
      supabase
        .from('ticket_reopen_requests')
        .select('*')
        .eq('ticket_id', ticketId)
        .order('requested_at', { ascending: false })
        .limit(1)
        .maybeSingle(),
      supabase
        .from('ticket_assignments')
        .select('users!ticket_assignments_assigned_to_fkey(user_id, first_name, last_name)')
        .eq('ticket_id', ticketId)
        .is('unassigned_at', null)
        .maybeSingle(),
    ])

    setComments((c.data ?? []) as CommentRow[])
    setHistory((h.data ?? []) as HistoryRow[])
    setAttachments(a.data ?? [])
    setReopenReq(r.data ?? null)
    setAssignee((asg.data?.users as unknown as Person | null) ?? null)

    // Attachments live in a private bucket. Mint a short-lived signed URL
    // per object; there is no public path to these files.
    // One round trip per attachment, in parallel rather than in series.
    const attachmentRows = a.data ?? []
    const signed = await Promise.all(
      attachmentRows.map((att) =>
        supabase.storage
          .from(ATTACHMENT_BUCKET)
          .createSignedUrl(att.file_url, 300)
          .then((r) => [att.attachment_id, r.data?.signedUrl] as const),
      ),
    )
    const urls: Record<number, string> = {}
    for (const [id, url] of signed) if (url) urls[id] = url
    setSignedUrls(urls)
    setLoading(false)
  }, [ticketId])

  useEffect(() => {
    void load()
  }, [load])

  const readTicket = useCallback(async (signal: AbortSignal) => {
    const { data, error: readError } = await supabase.from('tickets').select('*')
      .eq('ticket_id', ticketId).abortSignal(signal).maybeSingle()
    return readError ? undefined : data
  }, [ticketId])

  useEffect(() => {
    if (ticket?.ai_routing_status !== 'PENDING') return
    setPollingExpired(false)
    return startPendingTicketPolling({
      read: readTicket,
      onUpdate: (next) => setTicket((current) => latestTicket(current, next)),
      onExpire: () => setPollingExpired(true),
    })
  }, [readTicket, ticket?.ai_routing_status])

  useEffect(() => {
    // Routing is persisted before enrichment. One final read after the POST
    // ends picks up polished text even if pending polling has already stopped.
    let active = true
    let controller: AbortController | undefined
    let deadline: ReturnType<typeof setTimeout> | undefined
    const unsubscribe = onClassificationFinished((finishedId) => {
      if (finishedId !== ticketId) return
      controller?.abort()
      clearTimeout(deadline)
      const requestController = new AbortController()
      controller = requestController
      const timeout = setTimeout(() => requestController.abort(), 15000)
      deadline = timeout
      void readTicket(requestController.signal).then((next) => {
        if (active && next) setTicket((current) => latestTicket(current, next))
      }).catch(() => undefined).finally(() => clearTimeout(timeout))
    })
    return () => {
      active = false
      unsubscribe()
      clearTimeout(deadline)
      controller?.abort()
    }
  }, [readTicket, ticketId])

  useEffect(() => {
    let active = true
    const controller = new AbortController()
    const departmentId = ticket?.department_id
    setDeptStaff([])
    if (departmentId) {
      void supabase.from('user_departments')
        .select('users(user_id, first_name, last_name, active)')
        .eq('department_id', departmentId).abortSignal(controller.signal)
        .then(({ data }) => {
          if (!active) return
          setDeptStaff(
            ((data ?? []) as unknown as { users: (Person & { active: boolean }) | null }[])
              .map((row) => row.users)
              .filter((u): u is Person & { active: boolean } => !!u && u.active)
              .sort((x, y) => x.first_name.localeCompare(y.first_name)),
          )
        }, () => undefined)
    }
    return () => { active = false; controller.abort() }
  }, [ticket?.department_id, ticketId])

  const statusName = useMemo(
    () => (ticket ? statusNameById(lookups, ticket.status_id) : null),
    [ticket, lookups],
  )

  const isOwner = !!ticket && ticket.reported_by === profile?.user_id
  const canWithdraw = isOwner && !!statusName && WITHDRAWABLE.includes(statusName)

  const resolvedDays = ticket?.resolved_at ? daysSince(ticket.resolved_at) : null
  const canReopen =
    isOwner &&
    statusName === STATUS.RESOLVED &&
    resolvedDays !== null &&
    resolvedDays <= REOPEN_WINDOW_DAYS &&
    reopenReq?.decision !== 'PENDING'

  // Assignment and resolution have their own controls (they need a person or
  // resolution notes), so they are not offered as bare status buttons.
  const allowed = statusName ? (ALLOWED_TRANSITIONS[statusName] ?? []) : []
  const transitions = allowed.filter((s) => s !== STATUS.ASSIGNED && s !== STATUS.RESOLVED)
  const canResolve = allowed.includes(STATUS.RESOLVED)
  const isOpen = !!statusName && statusName !== STATUS.RESOLVED && statusName !== STATUS.CLOSED
  const canAssign = isStaff && isOpen && ticket?.department_id != null
  // Department admins transfer; any staff member may route an unrouted ticket.
  const canTransfer = isStaff && isOpen && (isAdmin || ticket?.department_id == null)
  const lastDenial = history.find((h) => h.action === 'REOPEN_DENIED')

  // Supabase query builders are thenable but are not real Promises, so the
  // callback is typed as PromiseLike rather than Promise.
  async function run(fn: () => PromiseLike<unknown> | unknown) {
    setBusy(true)
    setError(null)
    try {
      const result = (await fn()) as { error?: unknown } | undefined
      if (result && typeof result === 'object' && 'error' in result && result.error) {
        throw result.error
      }
      await load()
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  async function addComment() {
    if (!draft.trim() || !profile) return
    await run(async () => {
      const res = await supabase.from('comments').insert({
        ticket_id: ticketId,
        user_id: profile.user_id,
        message: draft.trim(),
        is_internal: internal,
      })
      setDraft('')
      return res
    })
  }

  async function changeStatus(to: string) {
    const statusId = statusIdByName(lookups, to)
    if (!statusId) return
    await run(() =>
      supabase.from('tickets').update({ status_id: statusId }).eq('ticket_id', ticketId),
    )
  }

  async function resolveTicket() {
    const statusId = statusIdByName(lookups, STATUS.RESOLVED)
    if (!statusId) return
    await run(async () => {
      const res = await supabase
        .from('tickets')
        .update({
          status_id: statusId,
          resolution_notes: resolution.trim() || null,
        })
        .eq('ticket_id', ticketId)
      setShowResolve(false)
      setResolution('')
      return res
    })
  }

  async function assignTo(userId: string) {
    await run(() => supabase.rpc('assign_ticket', { p_ticket_id: ticketId, p_assignee: userId }))
  }

  async function unassign() {
    await run(() => supabase.rpc('unassign_ticket', { p_ticket_id: ticketId }))
  }

  async function setPriority(priorityId: string) {
    await run(() =>
      supabase
        .from('tickets')
        .update({ priority_id: priorityId ? Number(priorityId) : null })
        .eq('ticket_id', ticketId),
    )
  }

  async function transfer(departmentId: number, reason?: string) {
    await run(async () => {
      const res = await supabase.rpc('transfer_ticket', {
        p_ticket_id: ticketId,
        p_department_id: departmentId,
        p_reason: reason?.trim() || undefined,
      })
      if (!res.error) {
        setShowTransfer(false)
        setTransferReason('')
      }
      return res
    })
  }

  async function addPhoto(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0]
    e.target.value = ''
    if (!f || !profile) return
    const problem = validateImage(f)
    if (problem) {
      setError(new Error(problem))
      return
    }
    await run(async () => {
      const err = await uploadAttachment(ticketId, f, profile.user_id)
      if (err) throw err
    })
  }

  async function withdraw() {
    await run(async () => {
      const res = await supabase.rpc('withdraw_own_ticket', { p_ticket_id: ticketId })
      if (!res.error) nav('/tickets')
      return res
    })
  }

  async function requestReopen() {
    if (!profile) return
    await run(async () => {
      const res = await supabase.from('ticket_reopen_requests').insert({
        ticket_id: ticketId,
        requested_by: profile.user_id,
        reason: reopenReason.trim() || null,
      })
      setShowReopen(false)
      setReopenReason('')
      return res
    })
  }

  async function decideReopen(approve: boolean) {
    if (!reopenReq) return
    await run(async () => {
      const res = await supabase.rpc('decide_reopen', {
        p_request_id: reopenReq.request_id,
        p_approve: approve,
        p_note: decisionNote.trim() || undefined,
      })
      if (!res.error) setDecisionNote('')
      return res
    })
  }

  if (loading || !ready) return <Loading />

  if (notFound) {
    return (
      <div className="content narrow">
        <Empty
          title="Ticket not available"
          hint="It may not exist, or it may belong to someone else. Access is enforced by the database."
        />
      </div>
    )
  }

  if (!ticket) return <div className="content"><ErrorNote error={error} /></div>

  return (
    <div className="content">
      <button className="sm" onClick={() => nav(-1)}>
        ← Back
      </button>

      <h1 style={{ marginTop: '0.75rem' }}>
        {ticket.is_emergency && <span className="badge danger">Emergency</span>} #
        {ticket.ticket_id} · {ticket.title}
      </h1>

      <div className="ticket-meta" style={{ marginBottom: '1rem' }}>
        <StatusBadge status={statusName} />
        <PriorityBadge
          priority={nameById(lookups.priorities, 'priority_id', ticket.priority_id)}
        />
        <span className="badge">
          {nameById(lookups.departments, 'department_id', ticket.department_id) ??
            'Awaiting routing'}
        </span>
        <span className="badge">
          {nameById(lookups.issueTypes, 'issue_type_id', ticket.issue_type_id) ??
            'Uncategorised'}
        </span>
        <span className="badge">Routing: {humanize(ticket.ai_routing_status)}</span>
        <span className="small muted">Opened {formatRelative(ticket.created_at)}</span>
        {isStaff && (
          <span className="small muted">
            · {assignee ? `Assigned to ${assignee.first_name} ${assignee.last_name}` : 'Unassigned'}
          </span>
        )}
      </div>

      <ErrorNote error={error} />

      {locationFailed && (
        <div className="alert warn">
          The ticket was created, but its location could not be saved. Ask staff to set
          the building on the ticket so it appears on the campus map.
        </div>
      )}

      {attachmentFailed && (
        <div className="alert warn">
          The ticket was created, but the photo could not be uploaded. You can describe it in a
          comment, or file the photo again later.
        </div>
      )}

      {ticket.ai_routing_status === 'PENDING' && (
        <div className="alert" role="status">
          {pollingExpired
            ? 'Your ticket is saved. Automatic refresh paused after six minutes; reload the page to check the latest routing.'
            : 'Your ticket is saved. Classification is in progress; this page will update automatically.'}
        </div>
      )}

      {ticket.ai_routing_status === 'FAILED' && (
        <div className="alert warn">
          Automatic classification was unavailable, so this ticket was sent for manual
          review. Nothing was lost.
        </div>
      )}

      {reopenReq?.decision === 'PENDING' && (
        <div className="alert warn">
          <strong>Reopen requested</strong> {formatRelative(reopenReq.requested_at)}
          {reopenReq.reason && <> — “{reopenReq.reason}”</>}
          {isAdmin && (
            <>
              <input
                aria-label="Note to the requester (optional)"
                placeholder="Note to the requester (optional)"
                value={decisionNote}
                onChange={(e) => setDecisionNote(e.target.value)}
                style={{ marginTop: '0.6rem' }}
              />
              <div className="actions" style={{ marginTop: '0.5rem' }}>
                <button className="sm primary" disabled={busy} onClick={() => void decideReopen(true)}>
                  Approve
                </button>
                <button className="sm" disabled={busy} onClick={() => void decideReopen(false)}>
                  Decline
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {reopenReq?.decision === 'DENIED' && statusName === STATUS.RESOLVED && (
        <div className="alert warn">
          <strong>Reopen request declined</strong>{' '}
          {formatRelative(reopenReq.decided_at ?? reopenReq.requested_at)}
          {lastDenial?.new_value && <> — “{lastDenial.new_value}”</>}
        </div>
      )}

      {isStaff &&
        statusName === STATUS.NEEDS_REVIEW &&
        ticket.ai_suggested_department_id != null &&
        ticket.ai_suggested_department_id !== ticket.department_id && (
          <div className="alert warn">
            AI suggests{' '}
            <strong>
              {nameById(lookups.departments, 'department_id', ticket.ai_suggested_department_id)}
            </strong>
            {ticket.ai_confidence != null && (
              <> ({Math.round(ticket.ai_confidence * 100)}% confident)</>
            )}
            .
            {canTransfer && (
              <button
                className="sm"
                style={{ marginLeft: '0.6rem' }}
                disabled={busy}
                onClick={() => void transfer(ticket.ai_suggested_department_id!)}
              >
                Accept suggestion
              </button>
            )}
          </div>
        )}

      <div className="card">
        <h3>Description</h3>
        <p style={{ whiteSpace: 'pre-wrap' }}>{ticket.description ?? ticket.original_text}</p>

        <div className="small muted">
          Location:{' '}
          {ticket.location_id ? <LocationLine locationId={ticket.location_id} /> : 'Not specified'}
        </div>

        {ticket.resolution_notes && (
          <>
            <h3 style={{ marginTop: '1rem' }}>Resolution</h3>
            <p style={{ whiteSpace: 'pre-wrap' }}>{ticket.resolution_notes}</p>
            <div className="small muted">Resolved {formatDateTime(ticket.resolved_at)}</div>
          </>
        )}
      </div>

      <TicketAiDetails ticket={ticket} isStaff={isStaff} />

      {(attachments.length > 0 || isOpen) && (
        <div className="card">
          <h3>Photos</h3>
          {attachments.length > 0 ? (
            <div className="attachments">
              {attachments.map((a) => (
                <a key={a.attachment_id} href={signedUrls[a.attachment_id]} target="_blank" rel="noreferrer">
                  {signedUrls[a.attachment_id] ? (
                    <img src={signedUrls[a.attachment_id]} alt={a.file_name} />
                  ) : (
                    <span className="small muted">{a.file_name}</span>
                  )}
                </a>
              ))}
            </div>
          ) : (
            <p className="small muted">No photos yet.</p>
          )}
          {isOpen && (
            <label className="file-button">
              <input
                type="file"
                accept={ACCEPTED_IMAGE_TYPES.join(',')}
                onChange={(e) => void addPhoto(e)}
                disabled={busy}
              />
              + Add photo
            </label>
          )}
          {attachments.length > 0 && <div className="hint">Links expire after five minutes.</div>}
        </div>
      )}

      {/* ---- actions ---- */}
      <div className="card">
        <h3>Actions</h3>
        <div className="actions">
          {isStaff && transitions.map((s) => (
            <button key={s} className="sm" disabled={busy} onClick={() => void changeStatus(s)}>
              Move to {humanize(s)}
            </button>
          ))}
          {isStaff && canResolve && (
            <button className="sm primary" disabled={busy} onClick={() => setShowResolve(true)}>
              Resolve
            </button>
          )}
          {canAssign && profile && assignee?.user_id !== profile.user_id && (
            <button className="sm" disabled={busy} onClick={() => void assignTo(profile.user_id)}>
              {assignee ? 'Take over' : 'Claim'}
            </button>
          )}
          {canAssign && assignee && (
            <button className="sm" disabled={busy} onClick={() => void unassign()}>
              Unassign
            </button>
          )}
          {canTransfer && (
            <button className="sm" disabled={busy} onClick={() => setShowTransfer(true)}>
              {ticket.department_id == null ? 'Route to department' : 'Transfer'}
            </button>
          )}
          {canReopen && (
            <button className="sm primary" disabled={busy} onClick={() => setShowReopen(true)}>
              Request reopen
            </button>
          )}
          {canWithdraw && (
            <button className="sm danger" disabled={busy} onClick={() => void withdraw()}>
              Withdraw ticket
            </button>
          )}
          {busy && <Spinner />}
        </div>

        {isStaff && (canAssign || isOpen) && (
          <div className="row" style={{ marginTop: '0.9rem' }}>
            {canAssign && (
              <div>
                <label htmlFor="assign">Assign to</label>
                <select
                  id="assign"
                  value={assignee?.user_id ?? ''}
                  disabled={busy}
                  onChange={(e) => e.target.value && void assignTo(e.target.value)}
                >
                  <option value="">— Unassigned —</option>
                  {assignee && !deptStaff.some((p) => p.user_id === assignee.user_id) && (
                    <option value={assignee.user_id}>
                      {assignee.first_name} {assignee.last_name}
                    </option>
                  )}
                  {deptStaff.map((p) => (
                    <option key={p.user_id} value={p.user_id}>
                      {p.first_name} {p.last_name}
                    </option>
                  ))}
                </select>
              </div>
            )}
            {isOpen && (
              <div>
                <label htmlFor="priority">Priority</label>
                <select
                  id="priority"
                  value={ticket.priority_id ?? ''}
                  disabled={busy}
                  onChange={(e) => void setPriority(e.target.value)}
                >
                  <option value="">Not set</option>
                  {lookups.priorities.map((p) => (
                    <option key={p.priority_id} value={p.priority_id}>
                      {humanize(p.name)}
                    </option>
                  ))}
                </select>
              </div>
            )}
          </div>
        )}
        {isOwner && statusName === STATUS.RESOLVED && !canReopen && reopenReq?.decision !== 'PENDING' && (
          <div className="hint">
            The {REOPEN_WINDOW_DAYS}-day reopen window has closed. Add a comment instead, or
            file a new ticket.
          </div>
        )}
      </div>

      {/* ---- comments ---- */}
      <div className="card">
        <h3>Comments</h3>
        {comments.length === 0 && <p className="muted small">No comments yet.</p>}
        {comments.map((c) => (
          <div key={c.comment_id} className={`comment ${c.is_internal ? 'internal' : ''}`}>
            <div className="head">
              {c.users ? `${c.users.first_name} ${c.users.last_name}` : 'Unknown'} ·{' '}
              {formatRelative(c.created_at)}
              {c.is_internal && <> · <strong>Internal note</strong></>}
            </div>
            <div style={{ whiteSpace: 'pre-wrap' }}>{c.message}</div>
          </div>
        ))}

        <div className="field" style={{ marginTop: '0.75rem' }}>
          <label htmlFor="comment">Add a comment</label>
          <textarea
            id="comment"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={
              statusName === STATUS.RESOLVED && isOwner
                ? 'A comment will not reopen this ticket. Use Request reopen for that.'
                : 'Add detail, ask a question, or share an update.'
            }
          />
        </div>
        <div className="actions">
          <button className="primary" disabled={busy || !draft.trim()} onClick={() => void addComment()}>
            Post
          </button>
          {isStaff && (
            <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', margin: 0, fontWeight: 400 }}>
              <input
                type="checkbox"
                checked={internal}
                onChange={(e) => setInternal(e.target.checked)}
                style={{ width: 'auto' }}
              />
              Internal note (hidden from requester)
            </label>
          )}
        </div>
      </div>

      {/* ---- history ---- */}
      <div className="card">
        <h3>History</h3>
        <ul className="timeline">
          {history.filter((h) => h.action !== 'DEMO_SEED').map((h) => (
            <li key={h.history_id}>
              <strong>{humanize(h.action)}</strong>
              {h.old_value && h.new_value && <> · {humanize(h.old_value)} → {humanize(h.new_value)}</>}
              {!h.old_value && h.new_value && <> · {h.new_value}</>}
              <div className="small muted">
                {h.users ? `${h.users.first_name} ${h.users.last_name}` : 'System'} ·{' '}
                {formatDateTime(h.created_at)}
              </div>
            </li>
          ))}
        </ul>
      </div>

      {showResolve && (
        <Modal title="Resolve ticket" onClose={() => setShowResolve(false)}>
          <div className="field">
            <label htmlFor="res">What was done?</label>
            <textarea
              id="res"
              value={resolution}
              onChange={(e) => setResolution(e.target.value)}
              placeholder="Replaced the thermostatic valve and confirmed the radiator cycles normally."
            />
            <div className="hint">The requester will see this.</div>
          </div>
          <div className="actions">
            <button className="primary" disabled={busy} onClick={() => void resolveTicket()}>
              Resolve
            </button>
            <button onClick={() => setShowResolve(false)}>Cancel</button>
          </div>
        </Modal>
      )}

      {showTransfer && (
        <Modal
          title={ticket.department_id == null ? 'Route to department' : 'Transfer ticket'}
          onClose={() => setShowTransfer(false)}
        >
          <div className="field">
            <label htmlFor="to-dept">Department</label>
            <select id="to-dept" value={transferTo} onChange={(e) => setTransferTo(e.target.value)}>
              <option value="">Choose…</option>
              {activeDepartments(lookups)
                .filter((d) => d.department_id !== ticket.department_id)
                .map((d) => (
                  <option key={d.department_id} value={d.department_id}>
                    {d.name}
                  </option>
                ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="to-why">Reason (internal note)</label>
            <textarea
              id="to-why"
              value={transferReason}
              onChange={(e) => setTransferReason(e.target.value)}
              placeholder="The fault is in the network switch, not the wall socket."
            />
            <div className="hint">
              Any current assignee is removed; the receiving department assigns it.
            </div>
          </div>
          <div className="actions">
            <button
              className="primary"
              disabled={busy || !transferTo}
              onClick={() => void transfer(Number(transferTo), transferReason)}
            >
              {ticket.department_id == null ? 'Route' : 'Transfer'}
            </button>
            <button onClick={() => setShowTransfer(false)}>Cancel</button>
          </div>
        </Modal>
      )}

      {showReopen && (
        <Modal title="Request reopen" onClose={() => setShowReopen(false)}>
          <p className="small muted">
            An administrator will review this. You have {REOPEN_WINDOW_DAYS} days from
            resolution to ask.
          </p>
          <div className="field">
            <label htmlFor="why">Why should this be reopened?</label>
            <textarea
              id="why"
              value={reopenReason}
              onChange={(e) => setReopenReason(e.target.value)}
              placeholder="The radiator started sticking again two days after the repair."
            />
          </div>
          <div className="actions">
            <button className="primary" disabled={busy} onClick={() => void requestReopen()}>
              Send request
            </button>
            <button onClick={() => setShowReopen(false)}>Cancel</button>
          </div>
        </Modal>
      )}
    </div>
  )
}

function LocationLine({ locationId }: { locationId: number }) {
  const [text, setText] = useState('…')
  useEffect(() => {
    supabase
      .from('locations')
      .select('room_number, exact_description, area_types(name), floors(floor_number, buildings(name))')
      .eq('location_id', locationId)
      .maybeSingle()
      .then(({ data }) => {
        if (!data) return setText('Not specified')
        const floor = data.floors as unknown as
          | { floor_number: string; buildings: { name: string } | null }
          | null
        const area = (data.area_types as unknown as { name: string } | null)?.name
        setText(
          [
            floor?.buildings?.name,
            floor && `Floor ${floor.floor_number}`,
            data.room_number && `Room ${data.room_number}`,
            area,
          ]
            .filter(Boolean)
            .join(' · ') || 'Not specified',
        )
      })
  }, [locationId])
  return <>{text}</>
}

// Ignore older background responses and responses for a previous route. Only
// the ticket snapshot changes; drafts and action controls keep their state.
function latestTicket(current: Tables<'tickets'> | null, next: Tables<'tickets'>) {
  if (!current) return next
  if (current.ticket_id !== next.ticket_id) return current
  return Date.parse(next.updated_at) >= Date.parse(current.updated_at) ? next : current
}
