import { useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ApiError, users as usersApi } from '../lib/api';
import { useAuth } from '../lib/auth';
import { useToast } from '../lib/toast';
import { InlineLoader } from '../components/ui';
import { Avatar } from '../components/Avatar';
import { parseAvatarSpec } from '../lib/avatar';
import type { ReadingLevel } from '../lib/types';

const LEVELS: ReadingLevel[] = ['beginner', 'intermediate', 'advanced'];

export default function EditProfile() {
  const { user, setUser } = useAuth();
  const navigate = useNavigate();
  const toast = useToast();

  // A studio-made avatar lives in the same column as an external URL, but it
  // is a spec rather than a link — showing it in a `type="url"` field would be
  // both meaningless and invalid, so the field stays empty and the studio owns
  // that avatar instead.
  const usesStudioAvatar = parseAvatarSpec(user?.avatar_url) !== null;

  const [fullName, setFullName] = useState(user?.full_name ?? '');
  const [avatarUrl, setAvatarUrl] = useState(
    usesStudioAvatar ? '' : (user?.avatar_url ?? ''),
  );
  const [level, setLevel] = useState<ReadingLevel>(
    (user?.reading_level as ReadingLevel) ?? 'intermediate',
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!user) return <InlineLoader />;

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      // PATCH is partial — send null rather than "" so clearing a field
      // actually clears it server-side instead of storing an empty string.
      const updated = await usersApi.update({
        full_name: fullName.trim() || null,
        // Don't clobber a studio avatar with the empty URL field it is
        // deliberately absent from — only send this when it actually changed.
        ...(usesStudioAvatar && !avatarUrl.trim()
          ? {}
          : { avatar_url: avatarUrl.trim() || null }),
        reading_level: level,
      });
      setUser(updated);
      toast.ok('saved', 'your profile is up to date.');
      navigate('/profile');
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : 'Could not save your profile.',
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="container">
      <div className="edit-profile-container">
        <h1 className="book-tunes-heading">edit profile</h1>

        {error && (
          <div className="error-message" role="alert">
            {error}
          </div>
        )}

        <form className="edit-form" onSubmit={onSubmit}>
          <div className="form-group">
            <label htmlFor="username">username</label>
            <input id="username" type="text" value={user.username} disabled />
            <p className="form-note">usernames can't be changed after signup</p>
          </div>

          <div className="form-group">
            <label htmlFor="fullName">display name</label>
            <input
              id="fullName"
              type="text"
              maxLength={120}
              value={fullName}
              onChange={(event) => setFullName(event.target.value)}
            />
          </div>

          <div className="form-group text-center">
            <label>avatar</label>
            <Avatar user={user} size={216} className="profile-avatar" />
            <Link className="btn btn-primary avatar-edit-link" to="/profile/avatar">
              open avatar studio
            </Link>
            <p className="form-note">
              {usesStudioAvatar
                ? 'made in the studio — open it to tweak or start over'
                : 'generated from your username until you make your own'}
            </p>
          </div>

          <div className="form-group">
            <label htmlFor="avatarUrl">…or paste an image url</label>
            <input
              id="avatarUrl"
              type="url"
              placeholder="https://…"
              value={avatarUrl}
              onChange={(event) => setAvatarUrl(event.target.value)}
            />
            <p className="form-note">
              {usesStudioAvatar
                ? 'setting one here replaces your studio avatar'
                : 'optional — overrides the generated avatar'}
            </p>
          </div>

          {avatarUrl.trim() && (
            <div className="form-group text-center">
              <img
                className="profile-avatar"
                src={avatarUrl}
                alt="Avatar preview"
                onError={(event) => {
                  (event.target as HTMLImageElement).style.display = 'none';
                }}
              />
            </div>
          )}

          <div className="form-group">
            <label htmlFor="level">reading level</label>
            <select
              id="level"
              value={level}
              onChange={(event) => setLevel(event.target.value as ReadingLevel)}
            >
              {LEVELS.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
            <p className="form-note">
              used to weight page count and complexity in recommendations
            </p>
          </div>

          <div className="form-actions">
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => navigate('/profile')}
            >
              cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={busy}>
              {busy ? 'saving…' : 'save changes'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
