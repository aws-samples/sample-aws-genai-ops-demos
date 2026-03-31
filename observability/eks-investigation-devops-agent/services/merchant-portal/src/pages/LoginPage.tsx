import { useState, FormEvent } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import './LoginPage.css'

export default function LoginPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [isLoading, setIsLoading] = useState(false)

  const { login, isAuthenticated } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  const from = (location.state as { from?: { pathname: string } })?.from?.pathname || '/catalog'

  if (isAuthenticated) {
    navigate(from, { replace: true })
    return null
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError('')
    setIsLoading(true)
    console.log('Login attempt:', { email })

    try {
      await login(email, password)
      console.log('Login successful')
      navigate(from, { replace: true })
    } catch (err) {
      console.error('Login error:', err)
      const errorMessage = err instanceof Error ? err.message : 'Login failed'
      setError(errorMessage)
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-left">
        <div className="login-branding">
          <span className="login-logo">◆ Helios</span>
          <h2>Welcome back</h2>
          <p>Sign in to manage your store, track orders, and process payments securely.</p>
          <div className="login-features">
            <div className="login-feature">
              <span className="feature-icon">🔒</span>
              <span>Enterprise-grade security</span>
            </div>
            <div className="login-feature">
              <span className="feature-icon">⚡</span>
              <span>Real-time transaction monitoring</span>
            </div>
            <div className="login-feature">
              <span className="feature-icon">📊</span>
              <span>Advanced analytics dashboard</span>
            </div>
          </div>
        </div>
      </div>
      <div className="login-right">
        <div className="login-card">
          <h1>Sign In</h1>
          <p className="login-subtitle">Enter your credentials to continue</p>

          <form onSubmit={handleSubmit} className="login-form">
            <div className="form-group">
              <label htmlFor="username">Username</label>
              <input
                id="username"
                type="text"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="Enter your username"
                required
                autoComplete="username"
              />
            </div>

            <div className="form-group">
              <label htmlFor="password">Password</label>
              <input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Enter your password"
                required
                autoComplete="current-password"
              />
            </div>

            {error && <p className="error-message">{error}</p>}

            <button
              type="submit"
              className="btn-primary login-button"
              disabled={isLoading}
            >
              {isLoading ? 'Signing in...' : 'Sign In'}
            </button>
          </form>

          <p className="login-footer">
            Protected by Amazon Cognito
          </p>
        </div>
      </div>
    </div>
  )
}
