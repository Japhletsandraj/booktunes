/**
 * Avatar generation — DiceBear, rendered locally.
 *
 * No API key and no network: `@dicebear/core` builds the SVG in the browser,
 * so avatars work offline and cost nothing. (DiceBear also runs a hosted HTTP
 * API; we deliberately don't use it.)
 *
 * `User.avatar_url` carries one of three things, distinguished by prefix:
 *
 *   null                  -> nothing chosen; `autoSpecFor` invents a stable one
 *   "http(s)://…"         -> an external image, used as-is (pre-existing behaviour)
 *   "dicebear:<style>?…"  -> a spec we render here
 *
 * The spec is stored rather than a baked data-URI for two reasons: it is ~60
 * bytes instead of ~3KB on every `/auth/me`, and it can be loaded back into the
 * studio for editing, which a flattened SVG cannot.
 */

import { createAvatar, type StyleOptions } from '@dicebear/core';

// Eager styles. Geometric and small (~40-125KB of source each), and they cover
// every auto-generated avatar — so the nav avatar renders on first paint with
// no async hop and no layout shift.
import * as glass from '@dicebear/glass';
import * as identicon from '@dicebear/identicon';
import * as initials from '@dicebear/initials';
import * as pixelArt from '@dicebear/pixel-art';
import * as rings from '@dicebear/rings';
import * as shapes from '@dicebear/shapes';
import * as thumbs from '@dicebear/thumbs';

type DicebearStyle = Parameters<typeof createAvatar>[0];

export interface AvatarStyleInfo {
  /** Stable key persisted inside the spec — never rename these. */
  key: string;
  label: string;
  creator: string;
  license: string;
  /**
   * CC BY 4.0 obliges us to credit the artist wherever the art is offered.
   * The studio renders that credit; CC0 styles need none but carry it anyway.
   */
  attributionRequired: boolean;
  /** Present when bundled eagerly; absent styles arrive via `load`. */
  style?: DicebearStyle;
  load?: () => Promise<DicebearStyle>;
}

/**
 * The styles offered in the studio.
 *
 * Ordered roughly by how well they suit the Y2K look — pixel art, identicons
 * and chrome rings first, illustrated faces after.
 *
 * The illustrated styles are dynamic imports because they are big: notionists,
 * adventurer and open-peeps are ~350-460KB of source *each*, and bundling all
 * of them eagerly put 1.4MB in the entry chunk for art that most users never
 * select. Now you download the one style you actually wear.
 */
export const AVATAR_STYLES: AvatarStyleInfo[] = [
  { key: 'pixelArt', label: 'pixel art', style: pixelArt, creator: 'DiceBear', license: 'CC0 1.0', attributionRequired: false },
  { key: 'identicon', label: 'identicon', style: identicon, creator: 'DiceBear', license: 'CC0 1.0', attributionRequired: false },
  { key: 'shapes', label: 'shapes', style: shapes, creator: 'DiceBear', license: 'CC0 1.0', attributionRequired: false },
  { key: 'rings', label: 'rings', style: rings, creator: 'DiceBear', license: 'CC0 1.0', attributionRequired: false },
  { key: 'glass', label: 'glass', style: glass, creator: 'DiceBear', license: 'CC0 1.0', attributionRequired: false },
  { key: 'thumbs', label: 'thumbs', style: thumbs, creator: 'DiceBear', license: 'CC0 1.0', attributionRequired: false },
  { key: 'initials', label: 'initials', style: initials, creator: 'DiceBear', license: 'CC0 1.0', attributionRequired: false },
  { key: 'notionists', label: 'notionists', creator: 'Zoish', license: 'CC0 1.0', attributionRequired: false, load: () => import('@dicebear/notionists') },
  { key: 'openPeeps', label: 'open peeps', creator: 'Pablo Stanley', license: 'CC0 1.0', attributionRequired: false, load: () => import('@dicebear/open-peeps') },
  { key: 'lorelei', label: 'lorelei', creator: 'Lisa Wischofsky', license: 'CC0 1.0', attributionRequired: false, load: () => import('@dicebear/lorelei') },
  { key: 'bottts', label: 'bottts', creator: 'Pablo Stanley', license: 'Free for personal and commercial use', attributionRequired: false, load: () => import('@dicebear/bottts') },
  { key: 'funEmoji', label: 'fun emoji', creator: 'Davis Uche', license: 'CC BY 4.0', attributionRequired: true, load: () => import('@dicebear/fun-emoji') },
  { key: 'adventurer', label: 'adventurer', creator: 'Lisa Wischofsky', license: 'CC BY 4.0', attributionRequired: true, load: () => import('@dicebear/adventurer') },
  { key: 'bigSmile', label: 'big smile', creator: 'Ashley Seo', license: 'CC BY 4.0', attributionRequired: true, load: () => import('@dicebear/big-smile') },
  { key: 'micah', label: 'micah', creator: 'Micah Lanier', license: 'CC BY 4.0', attributionRequired: true, load: () => import('@dicebear/micah') },
];

const STYLES_BY_KEY = new Map(AVATAR_STYLES.map((entry) => [entry.key, entry]));

export const DEFAULT_STYLE_KEY = 'pixelArt';

/**
 * Styles used for auto-generated avatars.
 *
 * Two constraints, both deliberate. CC0 only: a user who never opens the
 * studio still gets a generated avatar, and that shouldn't quietly create an
 * attribution obligation nothing in the UI is discharging. Eager only: the
 * auto avatar is the one shown before any choice exists, so it has to render
 * synchronously on first paint.
 */
const AUTO_STYLE_KEYS = [
  'pixelArt',
  'identicon',
  'shapes',
  'rings',
  'glass',
  'thumbs',
];

/** Background swatches, drawn from the y2k.css palette. */
export const BACKGROUND_PALETTE = [
  'ffb6f0', // --pale-pink
  'b6f0ff', // --pale-ice
  'ff00b6', // --hot-pink
  '00b6ff', // --ice-blue
  'c4b5fd', // --violet-400
  '7c3aed', // --violet-700
  'b034f8', // --magenta
  'd8b4ec', // --orchid
  'f8f5ff', // --violet-050
  '250335', // --ink
];

export type BackgroundType = 'solid' | 'gradientLinear';

export interface AvatarSpec {
  styleKey: string;
  seed: string;
  /** Hex strings without '#'. Two entries make a gradient read as a gradient. */
  backgroundColor: string[];
  backgroundType: BackgroundType;
  /** 0–50, where 50 is a circle. */
  radius: number;
  /** 0–200 percent. */
  scale: number;
  flip: boolean;
  /** 0–360 degrees. */
  rotate: number;
}

export const SPEC_PREFIX = 'dicebear:';

export function styleInfo(key: string): AvatarStyleInfo {
  return STYLES_BY_KEY.get(key) ?? STYLES_BY_KEY.get(DEFAULT_STYLE_KEY)!;
}

/**
 * FNV-1a. Only needs to be stable and well-spread across buckets — this picks
 * a style, it does not protect anything.
 */
function hash(value: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < value.length; i += 1) {
    h ^= value.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h >>> 0;
}

/**
 * The avatar a user has before they choose anything.
 *
 * Derived from their identity, so it is stable across devices and sessions
 * without being stored, and two users are unlikely to share both style and
 * palette. Seeded on username rather than id so it survives the id being
 * absent in any lighter-weight user payload.
 */
export function autoSpecFor(user: { username: string; id?: string }): AvatarSpec {
  const identity = user.username || user.id || 'reader';
  const h = hash(identity);

  const styleKey = AUTO_STYLE_KEYS[h % AUTO_STYLE_KEYS.length];
  const first = BACKGROUND_PALETTE[(h >>> 8) % BACKGROUND_PALETTE.length];
  const second = BACKGROUND_PALETTE[(h >>> 16) % BACKGROUND_PALETTE.length];

  return {
    styleKey,
    seed: identity,
    // Identical stops would render a "gradient" indistinguishable from a solid.
    backgroundColor: first === second ? [first] : [first, second],
    backgroundType: first === second ? 'solid' : 'gradientLinear',
    radius: 50,
    scale: 100,
    flip: false,
    rotate: 0,
  };
}

export function serializeAvatarSpec(spec: AvatarSpec): string {
  const params = new URLSearchParams();
  params.set('seed', spec.seed);
  params.set('bg', spec.backgroundColor.join(','));
  params.set('bgType', spec.backgroundType);
  params.set('radius', String(spec.radius));
  params.set('scale', String(spec.scale));
  params.set('rotate', String(spec.rotate));
  if (spec.flip) params.set('flip', '1');
  return `${SPEC_PREFIX}${spec.styleKey}?${params.toString()}`;
}

const HEX = /^[0-9a-f]{6}$/i;

function clamp(value: number, min: number, max: number, fallback: number): number {
  return Number.isFinite(value) ? Math.min(max, Math.max(min, value)) : fallback;
}

/**
 * Parse a stored spec. Returns null for anything that isn't one — an external
 * URL, an empty string, or a spec written by a newer version whose style we
 * no longer ship.
 */
export function parseAvatarSpec(value: string | null | undefined): AvatarSpec | null {
  if (!value || !value.startsWith(SPEC_PREFIX)) return null;

  const body = value.slice(SPEC_PREFIX.length);
  const split = body.indexOf('?');
  const styleKey = split === -1 ? body : body.slice(0, split);
  if (!STYLES_BY_KEY.has(styleKey)) return null;

  const params = new URLSearchParams(split === -1 ? '' : body.slice(split + 1));

  const colors = (params.get('bg') ?? '')
    .split(',')
    .map((entry) => entry.trim().replace(/^#/, ''))
    .filter((entry) => HEX.test(entry))
    .slice(0, 2);

  const bgType = params.get('bgType');

  return {
    styleKey,
    seed: params.get('seed') ?? '',
    backgroundColor: colors.length ? colors : [BACKGROUND_PALETTE[0]],
    backgroundType: bgType === 'gradientLinear' ? 'gradientLinear' : 'solid',
    radius: clamp(Number(params.get('radius')), 0, 50, 50),
    scale: clamp(Number(params.get('scale')), 50, 200, 100),
    flip: params.get('flip') === '1',
    rotate: clamp(Number(params.get('rotate')), 0, 360, 0),
  };
}

/** Styles resolved from a dynamic import, kept for the rest of the session. */
const loadedStyles = new Map<string, DicebearStyle>();
const inFlight = new Map<string, Promise<DicebearStyle | null>>();

function resolvedStyle(key: string): DicebearStyle | null {
  const info = styleInfo(key);
  return info.style ?? loadedStyles.get(info.key) ?? null;
}

/**
 * Make a style renderable, fetching its chunk if needed.
 *
 * Resolves to null if the chunk fails to load — an offline user mid-session
 * should fall back to a style that is already in memory, not see a crash.
 */
export function ensureStyle(key: string): Promise<DicebearStyle | null> {
  const info = styleInfo(key);
  const ready = resolvedStyle(info.key);
  if (ready) return Promise.resolve(ready);

  const existing = inFlight.get(info.key);
  if (existing) return existing;

  const pending = (info.load ? info.load() : Promise.reject(new Error('no loader')))
    .then((style) => {
      loadedStyles.set(info.key, style);
      return style;
    })
    .catch(() => null)
    .finally(() => inFlight.delete(info.key));

  inFlight.set(info.key, pending);
  return pending;
}

/** True when a spec can be rendered right now, without awaiting a chunk. */
export function isStyleReady(key: string): boolean {
  return resolvedStyle(key) !== null;
}

function optionsFor(spec: AvatarSpec, size: number): StyleOptions<Record<string, unknown>> {
  return {
    seed: spec.seed,
    size,
    radius: spec.radius,
    scale: spec.scale,
    flip: spec.flip,
    rotate: spec.rotate,
    backgroundColor: spec.backgroundColor,
    backgroundType: [spec.backgroundType],
  };
}

/**
 * Render a spec to an inline `data:` URI usable as an `<img src>`.
 *
 * Returns null when the style's chunk hasn't loaded yet; call `ensureStyle`
 * and render again. Callers that need something to show meanwhile can fall
 * back to the auto avatar, which only ever uses eager styles.
 */
export function renderAvatar(spec: AvatarSpec, size = 128): string | null {
  const style = resolvedStyle(spec.styleKey);
  if (!style) return null;
  return createAvatar(style, optionsFor(spec, size)).toDataUri();
}

export type ResolvedAvatar =
  | { kind: 'image'; src: string }
  | { kind: 'generated'; spec: AvatarSpec; isAuto: boolean };

/**
 * Work out what to show for a user, in priority order: a stored spec, then an
 * external URL, then the auto-generated fallback.
 */
export function resolveAvatar(user: {
  avatar_url: string | null;
  username: string;
  id?: string;
}): ResolvedAvatar {
  const spec = parseAvatarSpec(user.avatar_url);
  if (spec) return { kind: 'generated', spec, isAuto: false };

  const url = user.avatar_url?.trim();
  if (url) return { kind: 'image', src: url };

  return { kind: 'generated', spec: autoSpecFor(user), isAuto: true };
}

/**
 * Convenience for plain `<img src>` call sites.
 *
 * Always returns something renderable: a spec whose style hasn't loaded yet
 * falls back to the user's auto avatar, which uses eager styles only.
 */
export function avatarSrcFor(
  user: { avatar_url: string | null; username: string; id?: string },
  size = 128,
): string {
  const resolved = resolveAvatar(user);
  if (resolved.kind === 'image') return resolved.src;
  return renderAvatar(resolved.spec, size) ?? renderAvatar(autoSpecFor(user), size) ?? '';
}

/** A fresh random seed, used by the studio's shuffle controls. */
export function randomSeed(): string {
  return Math.random().toString(36).slice(2, 10);
}
