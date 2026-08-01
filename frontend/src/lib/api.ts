/**
 * Typed client for the Booktunes API.
 *
 * Two things here are load-bearing:
 *
 *  1. Access tokens expire after 30 minutes. Rather than making every caller
 *     handle a 401, a failed request is retried once behind a single-flight
 *     refresh — concurrent 401s share one `/auth/refresh` call instead of
 *     stampeding it and racing each other's token writes.
 *  2. Every error surfaces as an `ApiError` carrying the server's `code`, so
 *     the UI can branch on `not_in_library` or `playlist_generation_failed`
 *     without string-matching prose.
 */

import type {
  BookDetail,
  BookSummary,
  CurrentlyReadingItem,
  LibraryItem,
  LibraryStatus,
  Message,
  Page,
  PersonalizedSummary,
  PlaylistSource,
  PlaylistWithBook,
  Playlist,
  PreferenceQuizPayload,
  Progress,
  ReadingLevel,
  ReadingStats,
  Recommendation,
  RecommendationBatch,
  SyncResult,
  TasteProfile,
  TokenPair,
  User,
} from './types';

/**
 * Every route below is written relative to the API's mount point, so BASE has
 * to end at `/api/v1` — `${BASE}/auth/login`, not `${BASE}/api/v1/auth/login`.
 *
 * VITE_API_BASE is set in the Vercel dashboard, where the natural thing to
 * paste is the bare service host. That silently drops the mount point and
 * every call 404s on /auth/... A dashboard value also beats any .env file in
 * the repo, so this cannot be fixed by committing one — hence normalising here
 * instead of trusting whoever typed it.
 *
 * `??` alone is not enough either: it only catches null/undefined, and an env
 * var defined-but-empty is a real Vercel state that would yield BASE = ''.
 */
const RAW_BASE = import.meta.env.VITE_API_BASE?.trim() || '/api/v1';
const BASE = RAW_BASE.replace(/\/+$/, '').endsWith('/api/v1')
  ? RAW_BASE.replace(/\/+$/, '')
  : `${RAW_BASE.replace(/\/+$/, '')}/api/v1`;

const ACCESS_KEY = 'booktunes.access';
const REFRESH_KEY = 'booktunes.refresh';

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;

  constructor(
    status: number,
    code: string,
    message: string,
    details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }

  /** True when re-authenticating could plausibly fix this. */
  get isAuth(): boolean {
    return this.status === 401;
  }
}

// --- Token storage -------------------------------------------------------

export const tokens = {
  get access(): string | null {
    return localStorage.getItem(ACCESS_KEY);
  },
  get refresh(): string | null {
    return localStorage.getItem(REFRESH_KEY);
  },
  set(pair: TokenPair) {
    localStorage.setItem(ACCESS_KEY, pair.access_token);
    localStorage.setItem(REFRESH_KEY, pair.refresh_token);
  },
  clear() {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
  },
};

/** Fired when refresh fails, so the app can bounce to /login exactly once. */
type SessionListener = () => void;
const sessionExpiredListeners = new Set<SessionListener>();

export function onSessionExpired(fn: SessionListener): () => void {
  sessionExpiredListeners.add(fn);
  return () => sessionExpiredListeners.delete(fn);
}

function announceSessionExpired() {
  tokens.clear();
  sessionExpiredListeners.forEach((fn) => fn());
}

// --- Core request --------------------------------------------------------

interface RequestOptions {
  method?: string;
  body?: unknown;
  auth?: boolean;
  query?: Record<string, string | number | boolean | null | undefined>;
  signal?: AbortSignal;
}

function buildUrl(
  path: string,
  query?: RequestOptions['query'],
): string {
  const url = `${BASE}${path}`;
  if (!query) return url;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== null && value !== undefined && value !== '') {
      params.set(key, String(value));
    }
  }
  const qs = params.toString();
  return qs ? `${url}?${qs}` : url;
}

async function toApiError(response: Response): Promise<ApiError> {
  let code = `http_${response.status}`;
  let message = response.statusText || 'Request failed.';
  let details: Record<string, unknown> = {};

  try {
    const body = await response.json();
    if (body?.error) {
      code = body.error.code ?? code;
      message = body.error.message ?? message;
      details = body.error.details ?? {};
    }
  } catch {
    // A non-JSON body (a proxy 502, say) leaves the defaults in place.
  }
  return new ApiError(response.status, code, message, details);
}

// Single-flight refresh: the first 401 starts the refresh, everyone else
// awaits the same promise.
let refreshInFlight: Promise<boolean> | null = null;

async function refreshAccessToken(): Promise<boolean> {
  const refreshToken = tokens.refresh;
  if (!refreshToken) return false;

  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      try {
        const response = await fetch(`${BASE}/auth/refresh`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: refreshToken }),
        });
        if (!response.ok) return false;
        tokens.set((await response.json()) as TokenPair);
        return true;
      } catch {
        return false;
      } finally {
        // Cleared on the next microtask so awaiters resolve off this promise
        // before a subsequent 401 can start a fresh one.
        queueMicrotask(() => {
          refreshInFlight = null;
        });
      }
    })();
  }
  return refreshInFlight;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, auth = true, query, signal } = options;

  const send = async (): Promise<Response> => {
    const headers: Record<string, string> = {};
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (auth && tokens.access) {
      headers.Authorization = `Bearer ${tokens.access}`;
    }
    return fetch(buildUrl(path, query), {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  };

  let response: Response;
  try {
    response = await send();
  } catch (cause) {
    if (signal?.aborted) throw cause;
    throw new ApiError(
      0,
      'network_error',
      'Could not reach the BookTunes server. Is the API running?',
    );
  }

  if (response.status === 401 && auth && tokens.refresh) {
    if (await refreshAccessToken()) {
      response = await send();
    } else {
      announceSessionExpired();
      throw await toApiError(response);
    }
  }

  if (!response.ok) throw await toApiError(response);

  // 204 and empty bodies are legitimate for DELETE-ish routes.
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

// --- Auth ----------------------------------------------------------------

export const auth = {
  register: (payload: {
    username: string;
    email: string;
    password: string;
    full_name?: string | null;
    reading_level?: ReadingLevel | null;
  }) => request<TokenPair>('/auth/register', { method: 'POST', body: payload, auth: false }),

  login: (identifier: string, password: string) =>
    request<TokenPair>('/auth/login', {
      method: 'POST',
      body: { identifier, password },
      auth: false,
    }),

  logout: () => request<Message>('/auth/logout', { method: 'POST' }),

  me: () => request<User>('/auth/me'),

  savePreferences: (payload: PreferenceQuizPayload) =>
    request<User>('/auth/preferences', { method: 'POST', body: payload }),
};

// --- Books ---------------------------------------------------------------

export interface BookSearchQuery {
  q?: string;
  genre?: string;
  author?: string;
  min_rating?: number;
  max_pages?: number;
  reading_level?: string;
  year_from?: number;
  year_to?: number;
  semantic?: boolean;
  limit?: number;
  offset?: number;
}

export const books = {
  /** `GET /api/v1/books` — the catalogue search route. */
  search: (query: BookSearchQuery, signal?: AbortSignal) =>
    request<Page<BookSummary>>('/books', { query: query as never, signal }),

  trending: (limit = 20) =>
    request<BookSummary[]>('/books/trending', { query: { limit }, auth: false }),

  genres: () => request<string[]>('/books/genres', { auth: false }),

  moods: () => request<string[]>('/books/moods', { auth: false }),

  byGenre: (genre: string, limit = 20, offset = 0) =>
    request<Page<BookSummary>>(`/books/genre/${encodeURIComponent(genre)}`, {
      query: { limit, offset },
      auth: false,
    }),

  byMood: (mood: string, limit = 20, minStrength = 0.4) =>
    request<BookSummary[]>(`/books/mood/${encodeURIComponent(mood)}`, {
      query: { limit, min_strength: minStrength },
      auth: false,
    }),

  detail: (bookId: string) => request<BookDetail>(`/books/${bookId}`),
};

// --- Library -------------------------------------------------------------

export const library = {
  list: (status?: LibraryStatus, limit = 50, offset = 0) =>
    request<Page<LibraryItem>>('/library', { query: { status, limit, offset } }),

  add: (payload: {
    book_id: string;
    status?: LibraryStatus;
    rating?: number | null;
    review_text?: string | null;
  }) => request<LibraryItem>('/library', { method: 'POST', body: payload }),

  update: (
    bookId: string,
    payload: {
      status?: LibraryStatus;
      rating?: number | null;
      review_text?: string | null;
    },
  ) => request<LibraryItem>(`/library/${bookId}`, { method: 'PUT', body: payload }),

  remove: (bookId: string) =>
    request<Message>(`/library/${bookId}`, { method: 'DELETE' }),
};

// --- Recommendations -----------------------------------------------------

export const recommendations = {
  personalized: (limit = 20, genre?: string, refresh = false) =>
    request<RecommendationBatch>('/recommendations/personalized', {
      query: { limit, genre, refresh },
    }),

  byMood: (mood: string, limit = 20) =>
    request<Recommendation[]>(`/recommendations/mood/${encodeURIComponent(mood)}`, {
      query: { limit },
    }),

  summary: (bookId: string) =>
    request<PersonalizedSummary>(`/recommendations/book/${bookId}/summary`),

  feedback: (payload: {
    book_id: string;
    feedback_type: string;
    reason?: string | null;
  }) => request<Message>('/recommendations/feedback', { method: 'POST', body: payload }),
};

// --- Playlists -----------------------------------------------------------

export const playlists = {
  forBook: (bookId: string) =>
    request<PlaylistWithBook>(`/playlists/book/${bookId}`),

  generate: (payload: {
    book_id: string;
    source?: PlaylistSource | null;
    track_count?: number;
    force_regenerate?: boolean;
  }) => request<Playlist>('/playlists/generate', { method: 'POST', body: payload }),

  save: (playlistId: string) =>
    request<Message>('/playlists/save', {
      method: 'POST',
      body: { playlist_id: playlistId },
    }),

  unsave: (playlistId: string) =>
    request<Message>(`/playlists/save/${playlistId}`, { method: 'DELETE' }),

  mine: (limit = 50, offset = 0) =>
    request<PlaylistWithBook[]>('/playlists/user', { query: { limit, offset } }),
};

// --- Reading -------------------------------------------------------------

export const reading = {
  currently: (limit = 20) =>
    request<CurrentlyReadingItem[]>('/reading/currently', { query: { limit } }),

  updateProgress: (payload: {
    book_id: string;
    current_page?: number | null;
    percentage?: number | null;
    session_seconds?: number;
    device_type?: string | null;
    base_version?: number | null;
  }) => request<SyncResult>('/reading/progress', { method: 'POST', body: payload }),

  getProgress: (bookId: string, fromVersion?: number) =>
    request<SyncResult>(`/reading/progress/${bookId}`, {
      query: { from_version: fromVersion },
    }),

  stats: (periodDays?: number) =>
    request<ReadingStats>('/reading/stats', { query: { period_days: periodDays } }),

  streak: (tzOffsetMinutes = -new Date().getTimezoneOffset()) =>
    request<Record<string, unknown>>('/reading/streak', {
      query: { tz_offset_minutes: tzOffsetMinutes },
    }),
};

export type { Progress };

// --- Users ---------------------------------------------------------------

export const users = {
  me: () => request<User>('/users/me'),

  update: (payload: {
    full_name?: string | null;
    avatar_url?: string | null;
    reading_level?: ReadingLevel | null;
  }) => request<User>('/users/me', { method: 'PATCH', body: payload }),

  stats: () => request<ReadingStats>('/users/me/stats'),

  taste: () => request<TasteProfile>('/users/me/taste'),

  refreshPreferences: () =>
    request<Message>('/users/me/refresh-preferences', { method: 'POST' }),

  deleteAccount: () => request<Message>('/users/me', { method: 'DELETE' }),
};

// --- Meta ----------------------------------------------------------------

export async function health(): Promise<{
  status: string;
  dependencies: Record<string, boolean>;
  degraded: string[];
}> {
  // /health lives at the API root, not under /api/v1. In dev BASE is the
  // relative "/api/v1" so this resolves to the proxied "/health"; in
  // production BASE is absolute, and hardcoding "/health" would hit the
  // static site's own host instead of the API.
  const root = BASE.replace(/\/api\/v1\/?$/, '');
  const response = await fetch(`${root}/health`);
  if (!response.ok) throw new Error(`health check returned ${response.status}`);
  return response.json();
}
