/** Preserve the submission verbatim while keeping display fields trimmed. */
export function submissionText(title: string, description: string) {
  return {
    original_title: title,
    original_text: description,
    title: title.trim(),
    description: description.trim(),
  }
}
