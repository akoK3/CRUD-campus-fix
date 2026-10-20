import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { BrandMark, ErrorNote, Spinner } from '../components/ui'

type Mode = 'signin' | 'signup' | 'forgot'

const SUBMIT_LABEL: Record<Mode, string> = {
  signin: 'Sign in →',
  signup: 'Create account →',
  forgot: 'Send reset link →',
}

export default function LoginPage() {
  const { signIn, signUp, requestPasswordReset } = useAuth()
  const [searchParams] = useSearchParams()
  const [mode, setMode] = useState<Mode>(searchParams.get('mode') === 'signup' ? 'signup' : 'signin')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [firstName, setFirstName] = useState('')
  const [lastName, setLastName] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setNotice(null)
    setBusy(true)
    try {
      if (mode === 'signin') {
        await signIn(email.trim(), password)
      } else if (mode === 'forgot') {
        await requestPasswordReset(email.trim())
        // Same message whether or not the account exists, so this form
        // can't be used to probe which addresses are registered.
        setNotice(
          'If an account exists for that address, a reset link is on its way. Check your inbox.',
        )
      } else {
        const { needsConfirmation } = await signUp({
          email: email.trim(),
          password,
          firstName: firstName.trim(),
          lastName: lastName.trim(),
        })
        if (needsConfirmation) {
          setNotice('Account created. Check your email to confirm before signing in.')
          setMode('signin')
        }
      }
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  function switchMode(next: Mode) {
    setMode(next)
    setError(null)
    setNotice(null)
  }

  return (
    <div className="auth-page">
      <div className="auth-card">
        <div className="auth-logo">
          <BrandMark size={64} />
        </div>
        <h1>Welcome to CampusFix</h1>
        <p className="auth-sub">Report and track campus issues at Caldwell University.</p>

        {mode === 'forgot' ? (
          <div className="tabs">
            <button type="button" className="active">
              Reset password
            </button>
          </div>
        ) : (
          <div className="tabs" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={mode === 'signin'}
              className={mode === 'signin' ? 'active' : ''}
              onClick={() => switchMode('signin')}
            >
              Sign in
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === 'signup'}
              className={mode === 'signup' ? 'active' : ''}
              onClick={() => switchMode('signup')}
            >
              Create account
            </button>
          </div>
        )}

        <ErrorNote error={error} />
        {notice && <div className="alert ok">{notice}</div>}

        <form onSubmit={onSubmit}>
          {mode === 'signup' && (
            <div className="row">
              <div className="field">
                <label htmlFor="first">First name</label>
                <input
                  id="first"
                  value={firstName}
                  onChange={(e) => setFirstName(e.target.value)}
                  required
                  autoComplete="given-name"
                />
              </div>
              <div className="field">
                <label htmlFor="last">Last name</label>
                <input
                  id="last"
                  value={lastName}
                  onChange={(e) => setLastName(e.target.value)}
                  required
                  autoComplete="family-name"
                />
              </div>
            </div>
          )}

          <div className="field">
            <label htmlFor="email">Caldwell email</label>
            <input
              id="email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoComplete="email"
              placeholder="you@caldwell.edu"
            />
            <div className="hint">
              {mode === 'forgot'
                ? "We'll email you a link to choose a new password."
                : 'Accounts must use a @caldwell.edu address.'}
            </div>
          </div>

          {mode !== 'forgot' && (
            <div className="field">
              <div className="label-row">
                <label htmlFor="password">Password</label>
                {mode === 'signin' && (
                  <button type="button" className="link" onClick={() => switchMode('forgot')}>
                    Forgot password?
                  </button>
                )}
              </div>
              <input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={8}
                placeholder="Enter your password"
                autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
              />
            </div>
          )}

          <button className="primary block" type="submit" disabled={busy}>
            {busy ? <Spinner /> : SUBMIT_LABEL[mode]}
          </button>
        </form>

        {mode === 'forgot' && (
          <p className="auth-foot">
            <button type="button" className="link" onClick={() => switchMode('signin')}>
              ← Back to sign in
            </button>
          </p>
        )}

        <p className="auth-foot">
          New accounts are created as students. Staff and administrator access is granted by a
          system administrator.
        </p>
      </div>
    </div>
  )
}
