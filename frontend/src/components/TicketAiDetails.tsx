import type { Tables } from '../lib/database.types'

type AiText = Pick<Tables<'tickets'>,
  'title' | 'description' | 'original_title' | 'original_text' | 'ai_explanation'>

export function TicketAiDetails({ ticket, isStaff }: { ticket: AiText; isStaff: boolean }) {
  const differs = (ticket.original_title != null && ticket.original_title !== ticket.title)
    || ticket.original_text !== (ticket.description ?? ticket.original_text)

  return <>
    {ticket.ai_explanation?.trim() && (
      <section className="card" aria-label="AI routing explanation">
        <h3>AI routing explanation</h3>
        <p style={{ whiteSpace: 'pre-wrap' }}>{ticket.ai_explanation}</p>
      </section>
    )}
    {isStaff && differs && (
      <details className="card">
        <summary>Original student submission</summary>
        <h3 style={{ marginTop: '1rem' }}>Original title</h3>
        <p style={{ whiteSpace: 'pre-wrap' }}>
          {ticket.original_title ?? 'The original title was not recorded for this older ticket.'}
        </p>
        <h3>Original description</h3>
        <p style={{ whiteSpace: 'pre-wrap' }}>{ticket.original_text}</p>
      </details>
    )}
  </>
}
