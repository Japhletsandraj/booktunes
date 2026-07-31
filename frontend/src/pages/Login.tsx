import { useState, type FormEvent } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { ApiError } from '../lib/api';
import { useAuth } from '../lib/auth';

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [identifier, setIdentifier] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Send the user back where they were headed before the auth redirect.
  const next = (location.state as { from?: string } | null)?.from ?? '/';

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(identifier.trim(), password);
      navigate(next, { replace: true });
    } catch (cause) {
      setError(
        cause instanceof ApiError
          ? cause.message
          : 'Something went wrong. Please try again.',
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-page">
      <div className="auth-container">
        <form className="login-container" onSubmit={onSubmit}>
          <h1 className="book-tunes-heading">BookTunes</h1>
          <h2>welcome back</h2>

          {error && (
            <div className="error-message" role="alert">
              {error}
            </div>
          )}

          <label htmlFor="identifier">username or email</label>
          <input
            id="identifier"
            type="text"
            autoComplete="username"
            required
            value={identifier}
            onChange={(event) => setIdentifier(event.target.value)}
          />

          <label htmlFor="password">password</label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />

          <button type="submit" className="auth-btn" disabled={busy}>
            {busy ? 'signing in…' : 'sign in ✦'}
          </button>

          <div className="auth-links">
            <p>
              no account?{' '}
              <Link className="auth-link" to="/register">
                make one
              </Link>
            </p>
          </div>
        </form>
      </div>
    </div>
  );
}
