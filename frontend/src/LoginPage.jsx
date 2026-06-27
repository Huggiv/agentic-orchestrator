import { useMemo, useState } from 'react'
import { login, signup, evaluatePassword, isPasswordStrong } from './services/auth'

const INITIAL_FORM = {
  name: '',
  email: '',
  password: '',
  confirm_password: '',
  company: '',
  mobile_no: '',
}

export default function LoginPage({ onAuthenticated }) {
  const [mode, setMode] = useState('login')
  const [form, setForm] = useState(INITIAL_FORM)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const isSignup = mode === 'signup'

  const passwordChecks = useMemo(() => evaluatePassword(form.password), [form.password])
  const passwordsMatch = form.password === form.confirm_password

  const ctaText = useMemo(() => {
    if (submitting) return 'Please wait...'
    return isSignup ? 'Create Account' : 'Login'
  }, [submitting, isSignup])

  const updateField = (field, value) => {
    setForm((prev) => ({ ...prev, [field]: value }))
  }

  const resetFlow = (nextMode) => {
    setMode(nextMode)
    setError('')
    setForm((prev) => ({ ...prev, password: '', confirm_password: '' }))
  }

  const handleSubmit = async (event) => {
    event.preventDefault()
    setError('')

    if (!form.email.trim()) {
      setError('Email is required.')
      return
    }
    if (!form.password) {
      setError('Password is required.')
      return
    }

    if (isSignup) {
      if (!form.name.trim()) {
        setError('Name is required for signup.')
        return
      }
      if (!isPasswordStrong(form.password)) {
        setError('Password does not meet the strength requirements.')
        return
      }
      if (!passwordsMatch) {
        setError('Password and confirm password do not match.')
        return
      }
    }

    setSubmitting(true)
    try {
      const result = isSignup
        ? await signup({
            name: form.name,
            email: form.email,
            password: form.password,
            confirm_password: form.confirm_password,
            company: form.company || null,
            mobile_no: form.mobile_no || null,
          })
        : await login({ email: form.email, password: form.password })

      if (!result.authenticated) {
        throw new Error('Authentication failed')
      }
      onAuthenticated(result.user)
    } catch (err) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-backdrop-glow" aria-hidden="true" />
      <section className="auth-card">
        <div className="auth-card-header">
          <p className="auth-kicker">AgentFlow Secure Access</p>
          <h1>{isSignup ? 'Create Your Account' : 'Welcome Back'}</h1>
          <p className="auth-subtitle">
            {isSignup
              ? 'Sign up with a strong password to get started.'
              : 'Sign in with your email and password.'}
          </p>
        </div>

        <div className="auth-mode-switch" role="tablist" aria-label="Authentication mode">
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'login'}
            className={mode === 'login' ? 'is-active' : ''}
            onClick={() => resetFlow('login')}
          >
            Login
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'signup'}
            className={mode === 'signup' ? 'is-active' : ''}
            onClick={() => resetFlow('signup')}
          >
            Signup
          </button>
        </div>

        <form onSubmit={handleSubmit} className="auth-form-grid">
          {isSignup && (
            <label>
              Name *
              <input
                value={form.name}
                onChange={(event) => updateField('name', event.target.value)}
                placeholder="Jane Doe"
                required
              />
            </label>
          )}

          <label>
            Email *
            <input
              type="email"
              value={form.email}
              onChange={(event) => updateField('email', event.target.value)}
              placeholder="jane@company.com"
              autoComplete="email"
              required
            />
          </label>

          <label>
            Password *
            <input
              type="password"
              value={form.password}
              onChange={(event) => updateField('password', event.target.value)}
              placeholder="Enter your password"
              autoComplete={isSignup ? 'new-password' : 'current-password'}
              required
            />
          </label>

          {isSignup && (
            <>
              <label>
                Confirm Password *
                <input
                  type="password"
                  value={form.confirm_password}
                  onChange={(event) => updateField('confirm_password', event.target.value)}
                  placeholder="Re-enter your password"
                  autoComplete="new-password"
                  required
                />
              </label>

              <ul className="auth-password-rules">
                {passwordChecks.map((rule) => (
                  <li key={rule.label} className={rule.passed ? 'is-passed' : ''}>
                    <span aria-hidden="true">{rule.passed ? '✓' : '○'}</span> {rule.label}
                  </li>
                ))}
                <li className={form.confirm_password && passwordsMatch ? 'is-passed' : ''}>
                  <span aria-hidden="true">{form.confirm_password && passwordsMatch ? '✓' : '○'}</span> Passwords match
                </li>
              </ul>

              <label>
                Company (Optional)
                <input
                  value={form.company}
                  onChange={(event) => updateField('company', event.target.value)}
                  placeholder="Acme Corp"
                />
              </label>

              <label>
                Mobile No (Optional)
                <input
                  value={form.mobile_no}
                  onChange={(event) => updateField('mobile_no', event.target.value)}
                  placeholder="+1 555 0100"
                />
              </label>
            </>
          )}

          <button type="submit" disabled={submitting}>{ctaText}</button>
        </form>

        {error && <p className="auth-error">{error}</p>}
      </section>
    </div>
  )
}
