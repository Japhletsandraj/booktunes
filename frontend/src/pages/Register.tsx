import { useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ApiError } from '../lib/api';
import { useAuth } from '../lib/auth';
import type { ReadingLevel } from '../lib/types';

const LEVELS: ReadingLevel[] = ['beginner', 'intermediate', 'advanced'];

// Mirrors the server rules in schemas/auth.py so the user learns about a bad
// password before a round trip, not after.
const USERNAME_RE = /^[a-zA-Z0-9_.-]{3,30}$/;

function passwordProblem(value: string): string | null {
  if (value.length < 8) return 'Password must be at least 8 characters.';
  if (new TextEncoder().encode(value).length > 72) {
    return 'Password must be at most 72 bytes.';
  }
  if (!/[a-zA-Z]/.test(value) || !/\d/.test(value)) {
    return 'Password needs at least one letter and one digit.';
  }
  return null;
}

export default function Register() {
  const { register } = useAuth();
  const navigate = useNavigate();

  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [fullName, setFullName] = useState('');
  const [level, setLevel] = useState<ReadingLevel>('intermediate');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);

    if (!USERNAME_RE.test(username)) {
      setError(
        'Username may contain only letters, numbers, dots, hyphens and underscores (3-30 characters).',
      );
      return;
    }
    const pwProblem = passwordProblem(password);
    if (pwProblem) {
      setError(pwProblem);
      return;
    }

    setBusy(true);
    try {
      await register({
        username: username.toLowerCase(),
        email: email.trim(),
        password,
        full_name: fullName.trim() || null,
        reading_level: level,
      });
      // New accounts always land on the quiz — it seeds the preference vector
      // that makes the first set of recommendations non-generic.
      navigate('/quiz', { replace: true });
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
          <h2>join the club</h2>

          {error && (
            <div className="error-message" role="alert">
              {error}
            </div>
          )}

          <label htmlFor="username">username</label>
          <input
            id="username"
            type="text"
            autoComplete="username"
            required
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
          <p className="field-hint">letters, numbers, . - _ — 3 to 30 characters</p>

          <label htmlFor="email">email</label>
          <input
            id="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />

          <label htmlFor="password">password</label>
          <input
            id="password"
            type="password"
            autoComplete="new-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          <p className="field-hint">at least 8 characters, with a letter and a digit</p>

          <label htmlFor="fullName">display name (optional)</label>
          <input
            id="fullName"
            type="text"
            autoComplete="name"
            value={fullName}
            onChange={(event) => setFullName(event.target.value)}
          />

          <label htmlFor="level">reading level</label>
          <select
            id="level"
            className="status-select"
            value={level}
            onChange={(event) => setLevel(event.target.value as ReadingLevel)}
            style={{ marginBottom: '1rem' }}
          >
            {LEVELS.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>

          <button type="submit" className="auth-btn" disabled={busy}>
            {busy ? 'creating…' : 'create account ✦'}
          </button>

          <div className="auth-links">
            <p>
              already here?{' '}
              <Link className="auth-link" to="/login">
                sign in
              </Link>
            </p>
          </div>
        </form>
      </div>
    </div>
  );
}
