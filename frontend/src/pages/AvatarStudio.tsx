/**
 * Avatar studio — pick a style, shuffle a seed, tune the frame, save.
 *
 * Everything renders locally through DiceBear, so the preview grid updates
 * with no network at all and there is no key to configure.
 *
 * What gets saved is the compact spec string (see lib/avatar.ts), not a baked
 * image, which is what lets this page reopen with your existing avatar loaded
 * rather than starting from scratch every time.
 */

import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ApiError, users as usersApi } from '../lib/api';
import { useAuth } from '../lib/auth';
import { useToast } from '../lib/toast';
import { InlineLoader } from '../components/ui';
import { SpecAvatar } from '../components/Avatar';
import {
  AVATAR_STYLES,
  BACKGROUND_PALETTE,
  autoSpecFor,
  ensureStyle,
  parseAvatarSpec,
  randomSeed,
  serializeAvatarSpec,
  styleInfo,
  type AvatarSpec,
  type BackgroundType,
} from '../lib/avatar';

/** Seeds that produce a recognisable result, for the "surprise me" button. */
const FUN_SEEDS = [
  'midnight-library',
  'paperback-ghost',
  'vinyl-crush',
  'neon-margin',
  'dogeared',
  'cassette-heart',
  'stacks',
  'liner-notes',
  'dust-jacket',
  'b-side',
];

export default function AvatarStudio() {
  const { user, setUser } = useAuth();
  const navigate = useNavigate();
  const toast = useToast();

  // Start from whatever is saved; if that's an external URL or nothing at all,
  // start from the auto-generated avatar so the page never opens blank.
  const [spec, setSpec] = useState<AvatarSpec | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user || spec) return;
    setSpec(parseAvatarSpec(user.avatar_url) ?? autoSpecFor(user));
  }, [user, spec]);

  // This is the one page that shows every style at once, so pull the lazy
  // chunks now. `ensureStyle` dedupes, and each resolved style re-renders its
  // own tile through SpecAvatar.
  useEffect(() => {
    AVATAR_STYLES.forEach((entry) => {
      if (entry.load) void ensureStyle(entry.key);
    });
  }, []);

  // The grid previews the *current* seed and background in every style, so
  // switching styles is a like-for-like comparison rather than a surprise.
  const gridSpecs = useMemo(() => {
    if (!spec) return [];
    return AVATAR_STYLES.map((entry) => ({
      entry,
      preview: { ...spec, styleKey: entry.key } as AvatarSpec,
    }));
  }, [spec]);

  if (!user || !spec) return <InlineLoader label="warming up the studio" />;

  const patch = (changes: Partial<AvatarSpec>) =>
    setSpec((current) => (current ? { ...current, ...changes } : current));

  const info = styleInfo(spec.styleKey);

  const toggleColor = (hex: string) => {
    const selected = spec.backgroundColor;
    let next: string[];
    if (selected.includes(hex)) {
      // Never empty — a background with no colour renders as transparent,
      // which reads as broken against the page's own gradient.
      next = selected.length === 1 ? selected : selected.filter((c) => c !== hex);
    } else {
      // Two stops max: DiceBear's linear gradient only uses the first two.
      next = selected.length >= 2 ? [selected[1], hex] : [...selected, hex];
    }
    patch({
      backgroundColor: next,
      backgroundType: next.length > 1 ? 'gradientLinear' : spec.backgroundType,
    });
  };

  const surpriseMe = () => {
    const style = AVATAR_STYLES[Math.floor(Math.random() * AVATAR_STYLES.length)];
    const first = BACKGROUND_PALETTE[Math.floor(Math.random() * BACKGROUND_PALETTE.length)];
    const second = BACKGROUND_PALETTE[Math.floor(Math.random() * BACKGROUND_PALETTE.length)];
    const gradient = first !== second && Math.random() > 0.4;
    patch({
      styleKey: style.key,
      seed: FUN_SEEDS[Math.floor(Math.random() * FUN_SEEDS.length)] + '-' + randomSeed(),
      backgroundColor: gradient ? [first, second] : [first],
      backgroundType: gradient ? 'gradientLinear' : 'solid',
      rotate: 0,
      flip: Math.random() > 0.5,
    });
  };

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const updated = await usersApi.update({ avatar_url: serializeAvatarSpec(spec) });
      setUser(updated);
      toast.ok('avatar saved', 'looking good out there.');
      navigate('/profile');
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : 'Could not save your avatar.');
    } finally {
      setBusy(false);
    }
  };

  const resetToAuto = async () => {
    setBusy(true);
    setError(null);
    try {
      // null, not a spec: this restores the *auto* avatar, so it keeps
      // following the username rather than freezing today's generated one.
      const updated = await usersApi.update({ avatar_url: null });
      setUser(updated);
      setSpec(autoSpecFor(updated));
      toast.info('back to auto', 'your avatar is generated from your username again.');
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : 'Could not reset your avatar.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="container">
      <div className="avatar-studio">
        <h1 className="book-tunes-heading">avatar studio</h1>
        <p className="muted text-center avatar-studio__blurb">
          every avatar is drawn in your browser — no account, no API key, no upload.
        </p>

        {error && (
          <div className="error-message" role="alert">
            {error}
          </div>
        )}

        <div className="avatar-studio__layout">
          {/* --- Live preview ------------------------------------------- */}
          <aside className="avatar-preview-panel">
            <div className="avatar-preview-frame">
              <SpecAvatar spec={spec} size={200} className="avatar-preview-img" alt="Your avatar preview" />
            </div>

            <div className="avatar-preview-sizes" aria-hidden="true">
              <SpecAvatar spec={spec} size={64} className="avatar-chip" />
              <SpecAvatar spec={spec} size={40} className="avatar-chip" />
              <SpecAvatar spec={spec} size={28} className="avatar-chip" />
            </div>
            <p className="form-note text-center">how it looks in the nav bar</p>

            <div className="btn-row avatar-studio__actions">
              <button type="button" className="btn btn-primary" onClick={save} disabled={busy}>
                {busy ? 'saving…' : '✓ use this avatar'}
              </button>
              <button type="button" className="btn btn-secondary" onClick={surpriseMe} disabled={busy}>
                ✦ surprise me
              </button>
            </div>
            <div className="btn-row avatar-studio__actions">
              <button type="button" className="btn btn-secondary" onClick={() => navigate('/profile')}>
                cancel
              </button>
              <button type="button" className="btn btn-danger" onClick={resetToAuto} disabled={busy}>
                reset to auto
              </button>
            </div>

            <p className="form-note text-center avatar-credit">
              “{info.label}” by {info.creator} · {info.license}
              {info.attributionRequired && (
                <>
                  <br />
                  this style asks for credit when you use it.
                </>
              )}
            </p>
          </aside>

          {/* --- Controls ------------------------------------------------ */}
          <div className="avatar-controls">
            <section className="section">
              <h2>◈ style</h2>
              <div className="avatar-style-grid">
                {gridSpecs.map(({ entry, preview }) => (
                  <button
                    type="button"
                    key={entry.key}
                    className={`avatar-style-tile${entry.key === spec.styleKey ? ' is-selected' : ''}`}
                    onClick={() => patch({ styleKey: entry.key })}
                    aria-pressed={entry.key === spec.styleKey}
                    title={`${entry.label} — ${entry.creator}`}
                  >
                    <SpecAvatar spec={preview} size={72} className="avatar-style-img" />
                    <span className="avatar-style-label">{entry.label}</span>
                  </button>
                ))}
              </div>
            </section>

            <section className="section">
              <h2>✦ seed</h2>
              <p className="form-note">
                the seed decides the face. same seed, same avatar — every time, on every device.
              </p>
              <div className="avatar-seed-row">
                <input
                  type="text"
                  className="avatar-seed-input"
                  value={spec.seed}
                  maxLength={64}
                  placeholder="type anything"
                  onChange={(event) => patch({ seed: event.target.value })}
                  aria-label="Avatar seed"
                />
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => patch({ seed: randomSeed() })}
                >
                  shuffle
                </button>
              </div>
              <div className="btn-row" style={{ marginTop: '0.5rem' }}>
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => patch({ seed: user.username })}
                >
                  use my username
                </button>
              </div>
            </section>

            <section className="section">
              <h2>◇ background</h2>
              <p className="form-note">
                pick one for a solid, two for a gradient.
              </p>
              <div className="avatar-swatches">
                {BACKGROUND_PALETTE.map((hex) => {
                  const index = spec.backgroundColor.indexOf(hex);
                  return (
                    <button
                      type="button"
                      key={hex}
                      className={`avatar-swatch${index !== -1 ? ' is-selected' : ''}`}
                      style={{ background: `#${hex}` }}
                      onClick={() => toggleColor(hex)}
                      aria-pressed={index !== -1}
                      aria-label={`Background #${hex}`}
                      title={`#${hex}`}
                    >
                      {index !== -1 && <span className="avatar-swatch-order">{index + 1}</span>}
                    </button>
                  );
                })}
              </div>

              {spec.backgroundColor.length > 1 && (
                <div className="avatar-range-row">
                  <label htmlFor="bgType">fill</label>
                  <select
                    id="bgType"
                    value={spec.backgroundType}
                    onChange={(event) =>
                      patch({ backgroundType: event.target.value as BackgroundType })
                    }
                  >
                    <option value="gradientLinear">gradient</option>
                    <option value="solid">solid (first colour)</option>
                  </select>
                </div>
              )}
            </section>

            <section className="section">
              <h2>frame</h2>

              <div className="avatar-range-row">
                <label htmlFor="radius">corner {spec.radius === 50 ? '(circle)' : `${spec.radius}%`}</label>
                <input
                  id="radius"
                  type="range"
                  min={0}
                  max={50}
                  value={spec.radius}
                  onChange={(event) => patch({ radius: Number(event.target.value) })}
                />
              </div>

              <div className="avatar-range-row">
                <label htmlFor="scale">zoom {spec.scale}%</label>
                <input
                  id="scale"
                  type="range"
                  min={50}
                  max={200}
                  step={5}
                  value={spec.scale}
                  onChange={(event) => patch({ scale: Number(event.target.value) })}
                />
              </div>

              <div className="avatar-range-row">
                <label htmlFor="rotate">rotate {spec.rotate}°</label>
                <input
                  id="rotate"
                  type="range"
                  min={0}
                  max={360}
                  step={5}
                  value={spec.rotate}
                  onChange={(event) => patch({ rotate: Number(event.target.value) })}
                />
              </div>

              <div className="btn-row">
                <button
                  type="button"
                  className={`btn ${spec.flip ? 'btn-primary' : 'btn-secondary'}`}
                  onClick={() => patch({ flip: !spec.flip })}
                  aria-pressed={spec.flip}
                >
                  ⇄ mirror
                </button>
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => patch({ radius: 50, scale: 100, rotate: 0, flip: false })}
                >
                  reset frame
                </button>
              </div>
            </section>
          </div>
        </div>
      </div>
    </div>
  );
}
