import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { BrandMark } from '../components/ui'

export default function CoverPage() {
  const [accessibilityOpen, setAccessibilityOpen] = useState(false)
  const [largeText, setLargeText] = useState(() => localStorage.getItem('caldwellfix-large-text') === 'true')
  const [highContrast, setHighContrast] = useState(() => localStorage.getItem('caldwellfix-high-contrast') === 'true')
  const [reducedMotion, setReducedMotion] = useState(() => localStorage.getItem('caldwellfix-reduced-motion') === 'true')
  const [strongFocus, setStrongFocus] = useState(() => localStorage.getItem('caldwellfix-strong-focus') === 'true')

  useEffect(() => {
    document.documentElement.dataset.textSize = largeText ? 'large' : 'normal'
    localStorage.setItem('caldwellfix-large-text', String(largeText))
  }, [largeText])

  useEffect(() => {
    document.documentElement.dataset.highContrast = String(highContrast)
    localStorage.setItem('caldwellfix-high-contrast', String(highContrast))
  }, [highContrast])

  useEffect(() => {
    document.documentElement.dataset.reducedMotion = String(reducedMotion)
    localStorage.setItem('caldwellfix-reduced-motion', String(reducedMotion))
  }, [reducedMotion])

  useEffect(() => {
    document.documentElement.dataset.strongFocus = String(strongFocus)
    localStorage.setItem('caldwellfix-strong-focus', String(strongFocus))
  }, [strongFocus])

  return (
    <main className="cover-page">
      <div className="cover-utilities">
        <a href="mailto:campusfix@caldwell.edu">Contact us</a>
        <button type="button" className="link" onClick={() => setAccessibilityOpen(!accessibilityOpen)} aria-expanded={accessibilityOpen}>
          Accessibility
        </button>
      </div>
      <div className="cover-card">
        <div className="cover-logo" aria-label="CaldwellFix">
          <BrandMark size={116} />
        </div>
        <h1>CaldwellFix</h1>
        <p className="cover-slogan">Fixing Our Campus, One Report at a Time.</p>

        <div className="cover-actions" aria-label="Get started">
          <Link className="primary cover-action" to="/login">Log in <span aria-hidden="true">→</span></Link>
          <Link className="secondary cover-action" to="/login?mode=signup">Sign up</Link>
        </div>

        {accessibilityOpen && (
          <section className="cover-accessibility" aria-label="Accessibility options">
            <div>
              <strong>Accessibility options</strong>
              <p>These settings are saved on this device and apply throughout CaldwellFix.</p>
            </div>
            <div className="accessibility-options">
              <button type="button" aria-pressed={largeText} onClick={() => setLargeText(!largeText)}>
                {largeText ? '✓ ' : ''}Larger text
              </button>
              <button type="button" aria-pressed={highContrast} onClick={() => setHighContrast(!highContrast)}>
                {highContrast ? '✓ ' : ''}High contrast
              </button>
              <button type="button" aria-pressed={reducedMotion} onClick={() => setReducedMotion(!reducedMotion)}>
                {reducedMotion ? '✓ ' : ''}Reduce motion
              </button>
              <button type="button" aria-pressed={strongFocus} onClick={() => setStrongFocus(!strongFocus)}>
                {strongFocus ? '✓ ' : ''}Extra keyboard focus
              </button>
            </div>
            <p className="accessibility-tip">Keyboard tip: use Tab to move between controls, Shift + Tab to move back, and Enter or Space to select.</p>
          </section>
        )}
      </div>
      <button type="button" className="cover-quit" onClick={() => window.location.assign('about:blank')}>× Quit</button>
    </main>
  )
}
