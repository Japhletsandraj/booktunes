/**
 * Avatar rendering.
 *
 * Two components: `Avatar` for a user (resolving stored spec -> external URL ->
 * auto-generated), and `SpecAvatar` for a spec being edited in the studio,
 * where there is no user to resolve.
 *
 * Most styles are bundled eagerly and render synchronously. The illustrated
 * ones arrive as a dynamic chunk (see lib/avatar.ts), so these components show
 * the eager fallback first and upgrade when the chunk lands — the alternative
 * is an empty hole in the nav bar on every cold load.
 *
 * Rendering is memoised: building the SVG is real work, and avatars appear in
 * the nav on every page and 15 at a time in the studio grid.
 */

import { useEffect, useMemo, useState } from 'react';
import {
  autoSpecFor,
  ensureStyle,
  isStyleReady,
  renderAvatar,
  resolveAvatar,
  type AvatarSpec,
} from '../lib/avatar';

interface AvatarUser {
  avatar_url: string | null;
  username: string;
  id?: string;
}

/**
 * Re-render once a lazily-loaded style becomes available.
 *
 * Returns a counter rather than the style itself so the render path stays
 * synchronous — it just needs a reason to run again.
 */
function useStyleReady(styleKey: string): number {
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (isStyleReady(styleKey)) return;
    let cancelled = false;
    ensureStyle(styleKey).then((style) => {
      if (!cancelled && style) setTick((value) => value + 1);
    });
    return () => {
      cancelled = true;
    };
  }, [styleKey]);

  return tick;
}

export function SpecAvatar({
  spec,
  size = 128,
  className,
  alt = '',
  fallbackSeed,
}: {
  spec: AvatarSpec;
  size?: number;
  className?: string;
  alt?: string;
  /** Seed for the placeholder shown while an illustrated style loads. */
  fallbackSeed?: string;
}) {
  const tick = useStyleReady(spec.styleKey);

  const src = useMemo(() => {
    const rendered = renderAvatar(spec, size);
    if (rendered) return rendered;
    // Placeholder keeps the tile the right size and colour while we wait.
    return renderAvatar(
      autoSpecFor({ username: fallbackSeed ?? (spec.seed || 'reader') }),
      size,
    );
    // `tick` is the point: it re-runs this once the chunk resolves.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spec, size, fallbackSeed, tick]);

  return (
    <img
      className={className}
      src={src ?? undefined}
      width={size}
      height={size}
      alt={alt}
      loading="lazy"
    />
  );
}

export function Avatar({
  user,
  size = 128,
  className,
  alt = '',
  onClick,
}: {
  user: AvatarUser;
  size?: number;
  className?: string;
  alt?: string;
  onClick?: () => void;
}) {
  // An external URL that 404s would otherwise leave a broken-image icon; fall
  // back to the auto-generated avatar instead, which always renders.
  const [imageFailed, setImageFailed] = useState(false);

  const resolved = useMemo(() => resolveAvatar(user), [user]);
  const styleKey = resolved.kind === 'generated' ? resolved.spec.styleKey : '';
  const tick = useStyleReady(styleKey);

  const useImage = resolved.kind === 'image' && !imageFailed;

  const src = useMemo(() => {
    if (resolved.kind === 'image' && !imageFailed) return resolved.src;
    if (resolved.kind === 'generated') {
      const rendered = renderAvatar(resolved.spec, size);
      if (rendered) return rendered;
    }
    return renderAvatar(autoSpecFor(user), size);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resolved, imageFailed, user, size, tick]);

  return (
    <img
      className={className}
      src={src ?? undefined}
      width={size}
      height={size}
      alt={alt}
      onClick={onClick}
      onError={useImage ? () => setImageFailed(true) : undefined}
    />
  );
}
