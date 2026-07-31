/**
 * Profile: identity, reading stats, and the learned taste profile.
 *
 * The taste block exists because `/users/me/taste` deliberately exposes what
 * the recommender knows — confidence and signal count — so recommendations
 * aren't a black box. Low confidence gets an actionable message rather than
 * just a number.
 */

import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ApiError, users as usersApi } from '../lib/api';
import { useAuth } from '../lib/auth';
import { formatHours, prettyLabel } from '../lib/format';
import { useToast } from '../lib/toast';
import { InlineLoader, Meter } from '../components/ui';
import { Avatar } from '../components/Avatar';
import type { ReadingStats, TasteProfile } from '../lib/types';

export default function Profile() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const toast = useToast();

  const [stats, setStats] = useState<ReadingStats | null>(null);
  const [taste, setTaste] = useState<TasteProfile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rebuilding, setRebuilding] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  useEffect(() => {
    usersApi
      .stats()
      .then(setStats)
      .catch((cause: unknown) =>
        setError(
          cause instanceof ApiError ? cause.message : 'Could not load your stats.',
        ),
      );
    usersApi.taste().then(setTaste).catch(() => setTaste(null));
  }, []);

  if (!user) return <InlineLoader />;

  const rebuild = async () => {
    setRebuilding(true);
    try {
      const result = await usersApi.refreshPreferences();
      if (result.success) {
        toast.ok('profile rebuilt', result.message);
      } else {
        toast.info('not enough signal yet', result.message);
      }
      setTaste(await usersApi.taste());
    } catch (cause) {
      toast.err(
        'could not rebuild',
        cause instanceof ApiError ? cause.message : 'Unknown error.',
      );
    } finally {
      setRebuilding(false);
    }
  };

  const deleteAccount = async () => {
    try {
      await usersApi.deleteAccount();
      // The server cascades every dependent row; the local session is now
      // pointing at a user that no longer exists.
      await logout();
      navigate('/login', { replace: true });
    } catch (cause) {
      toast.err(
        'could not delete account',
        cause instanceof ApiError ? cause.message : 'Unknown error.',
      );
    }
  };

  const quizGenres = Array.isArray(taste?.quiz_answers?.favorite_genres)
    ? (taste!.quiz_answers.favorite_genres as string[])
    : [];
  const quizMoods = Array.isArray(taste?.quiz_answers?.preferred_moods)
    ? (taste!.quiz_answers.preferred_moods as string[])
    : [];

  return (
    <div className="profile-page">
      <div className="container">
        <div className="profile-header">
          <Avatar user={user} size={216} className="profile-avatar" />
          <Link className="btn btn-secondary avatar-edit-link" to="/profile/avatar">
            change avatar
          </Link>
          <div className="profile-username">{user.full_name || user.username}</div>
          <div className="profile-email">{user.email}</div>
          {user.join_date && (
            <div className="profile-join-date">
              member since {new Date(user.join_date).toLocaleDateString()}
            </div>
          )}
        </div>

        {error && <div className="error-message">{error}</div>}

        <section className="section">
          <h2>◈ reading stats</h2>
          {stats === null ? (
            <InlineLoader label="counting pages" />
          ) : (
            <div className="stats-grid">
              <div className="stat-item">
                <div className="stat-number">{stats.total_books_read}</div>
                <div className="stat-label">books read</div>
              </div>
              <div className="stat-item">
                <div className="stat-number">{stats.total_books_in_progress}</div>
                <div className="stat-label">in progress</div>
              </div>
              <div className="stat-item">
                <div className="stat-number">{stats.total_pages_read}</div>
                <div className="stat-label">pages</div>
              </div>
              <div className="stat-item">
                <div className="stat-number">
                  {formatHours(stats.total_reading_seconds)}
                </div>
                <div className="stat-label">time read</div>
              </div>
              <div className="stat-item">
                <div className="stat-number">{stats.current_streak_days}</div>
                <div className="stat-label">day streak</div>
              </div>
              <div className="stat-item">
                <div className="stat-number">{stats.longest_streak_days}</div>
                <div className="stat-label">best streak</div>
              </div>
            </div>
          )}
        </section>

        <section className="section">
          <h2>✦ what we've learned</h2>
          {taste === null ? (
            <InlineLoader label="reading your profile" />
          ) : (
            <div className="profile-bio">
              <div className="factor-row">
                <span>recommendation confidence</span>
                <strong>{Math.round(taste.confidence * 100)}%</strong>
              </div>
              <Meter value={taste.confidence * 100} />
              <p className="muted" style={{ marginTop: '0.6rem' }}>
                {taste.confidence_label}
              </p>

              <p className="muted" style={{ fontSize: '0.82rem' }}>
                preference vector:{' '}
                <strong>{taste.has_preference_vector ? 'built' : 'not built yet'}</strong>
                {taste.reading_level && <> · reading level: {taste.reading_level}</>}
              </p>

              {quizGenres.length > 0 && (
                <>
                  <p className="muted" style={{ marginTop: '0.8rem' }}>
                    favourite genres
                  </p>
                  <div className="genres-list">
                    {quizGenres.map((genre) => (
                      <span className="genre-tag" key={genre}>
                        {prettyLabel(genre)}
                      </span>
                    ))}
                  </div>
                </>
              )}

              {quizMoods.length > 0 && (
                <>
                  <p className="muted" style={{ marginTop: '0.8rem' }}>
                    preferred moods
                  </p>
                  <div className="genres-list">
                    {quizMoods.map((mood) => (
                      <span className="genre-tag" key={mood}>
                        {prettyLabel(mood)}
                      </span>
                    ))}
                  </div>
                </>
              )}

              <div className="btn-row" style={{ marginTop: '1.2rem' }}>
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={rebuild}
                  disabled={rebuilding}
                >
                  {rebuilding ? 'rebuilding…' : '⟳ rebuild taste profile'}
                </button>
                <Link className="btn btn-secondary" to="/quiz">
                  retake the quiz
                </Link>
              </div>
            </div>
          )}
        </section>

        {stats && stats.favorite_genres.length > 0 && (
          <section className="section">
            <h2>◇ most read genres</h2>
            <div className="genres-list">
              {stats.favorite_genres.slice(0, 12).map((entry, index) => (
                <span className="genre-tag" key={index}>
                  {String(entry.genre ?? entry.name ?? '')}
                  {entry.count != null && <> · {String(entry.count)}</>}
                </span>
              ))}
            </div>
          </section>
        )}

        <div className="profile-actions">
          <Link className="btn btn-primary" to="/profile/edit">
            edit profile
          </Link>
          {confirmDelete ? (
            <>
              <button type="button" className="btn btn-danger" onClick={deleteAccount}>
                yes, delete everything
              </button>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setConfirmDelete(false)}
              >
                cancel
              </button>
            </>
          ) : (
            <button
              type="button"
              className="btn btn-danger"
              onClick={() => setConfirmDelete(true)}
            >
              delete account
            </button>
          )}
        </div>

        {confirmDelete && (
          <p className="muted text-center" style={{ marginTop: '0.8rem' }}>
            This permanently removes your interactions, progress, saved playlists
            and preference vector. It cannot be undone.
          </p>
        )}
      </div>
    </div>
  );
}
