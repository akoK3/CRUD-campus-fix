import { supabase } from './supabase'

const API_URL = import.meta.env.VITE_API_URL
const finishedListeners = new Set<(ticketId: number) => void>()

/** Observe the end of the POST, including enrichment, within this browser tab. */
export function onClassificationFinished(listener: (ticketId: number) => void): () => void {
  finishedListeners.add(listener)
  return () => { finishedListeners.delete(listener) }
}

/**
 * Ask the backend to classify a ticket.
 *
 * Every failure mode here is non-fatal by design. The ticket is already
 * committed before this is called; if the classifier is down, misconfigured,
 * or simply absent, the ticket keeps the deterministic department derived
 * from its issue type and a human picks up the rest.
 *
 * Callers should treat this as fire-and-forget.
 */
export async function classifyTicket(ticketId: number): Promise<void> {
  if (!API_URL) return // No backend configured — deterministic routing stands.

  let requested = false
  try {
    const { data } = await supabase.auth.getSession()
    const token = data.session?.access_token
    if (!token) return

    // DeepSeek cold starts and enrichment can take minutes. Submission does
    // not await this request, so no browser-side deadline is needed.
    requested = true
    await fetch(`${API_URL}/api/tickets/${ticketId}/classify`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
    })
  } catch {
    // Session/network/tunnel failures cannot invalidate the saved ticket.
    // The backend persists routing independently of receiving its response.
  } finally {
    if (requested) {
      for (const listener of finishedListeners) {
        try { listener(ticketId) } catch { /* UI observers cannot fail submission. */ }
      }
    }
  }
}
