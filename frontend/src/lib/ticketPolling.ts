/** Bounded, sequential refresh of a saved ticket while routing is pending. */
export function startPendingTicketPolling<T extends { ai_routing_status: string }>({
  read,
  onUpdate,
  onExpire,
  intervalMs = 3000,
  maxDurationMs = 6 * 60 * 1000,
}: {
  read: (signal: AbortSignal) => Promise<T | null | undefined>
  onUpdate: (ticket: T) => void
  onExpire?: () => void
  intervalMs?: number
  maxDurationMs?: number
}): () => void {
  const controller = new AbortController()
  let stopped = false
  let timer: ReturnType<typeof setTimeout>
  const deadline = setTimeout(() => {
    stop()
    onExpire?.()
  }, maxDurationMs)

  function stop() {
    stopped = true
    clearTimeout(timer)
    clearTimeout(deadline)
    controller.abort()
  }

  async function tick() {
    try {
      const ticket = await read(controller.signal)
      if (stopped) return
      if (ticket === null) return stop() // Missing/inaccessible: do not keep querying.
      if (ticket) {
        if (ticket.ai_routing_status !== 'PENDING') stop()
        onUpdate(ticket)
      }
    } catch {
      // A transient read error must not turn a saved ticket into a failure.
    }
    if (!stopped) timer = setTimeout(() => { void tick() }, intervalMs)
  }

  timer = setTimeout(() => { void tick() }, intervalMs)
  return stop
}
