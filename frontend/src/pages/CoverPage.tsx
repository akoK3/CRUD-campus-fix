import { useState } from 'react'
import { Link } from 'react-router-dom'
import { BrandMark } from '../components/ui'

export default function CoverPage() {
  const [accessibilityOpen, setAccessibilityOpen] = useState(false)
  const [largeText, setLargeText] = useState(false)

  function toggleTextSize() {
    const next = !largeText
    setLargeText(next)
    document.documentElement.dataset.coverText = next ? 'large' : 'normal'
  }

  return (
    <main className="cover-page">
      <div className="cover-card">
        <div className="cover-logo" aria-label="CaldwellFix">
          <BrandMark size={104} />
        </div>
        <p className="cover-eyebrow">Caldwell University</p>
        <h1>CaldwellFix</h1>
        <p className="cover-slogan">Fixing our campus, one report at a time.</p>

        <div className="cover-actions" aria-label="Get started">
          <Link className="primary cover-action" to="/login?mode=signup">Create an account</Link>
          <Link className="secondary cover-action" to="/login">Log in</Link>
        </div>

        <div className="cover-links">
          <a href="mailto:campusfix@caldwell.edu">Contact us</a>
          <button type="button" className="link" onClick={() => setAccessibilityOpen(!accessibilityOpen)} aria-expanded={accessibilityOpen}>
            Accessibility
          </button>
          <button type="button" className="link" onClick={() => window.location.assign('about:blank')}>Quit</button>
        </div>

        {accessibilityOpen && (
          <section className="cover-accessibility" aria-label="Accessibility options">
            <strong>Accessibility</strong>
            <button type="button" onClick={toggleTextSize}>{largeText ? 'Use standard text' : 'Use larger text'}</button>
            <p>Use Tab to move through controls and Enter to select one.</p>
          </section>
        )}
      </div>
    </main>
  )
}
