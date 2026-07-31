/**
 * Types mirroring `backend/app/schemas/*`.
 *
 * Kept hand-written rather than generated so the shapes the UI actually
 * consumes stay obvious. Anything nullable on the server is nullable here —
 * `preview_url` in particular is null for every YouTube Music track.
 */

export type ReadingLevel = 'beginner' | 'intermediate' | 'advanced';

export type LibraryStatus =
  | 'want_to_read'
  | 'currently_reading'
  | 'read'
  | 'abandoned';

export type PlaylistSource = 'deezer' | 'spotify' | 'youtube_music' | 'custom';

/** The API's uniform error envelope (see `app/core/errors.py`). */
export interface ApiErrorBody {
  error: { code: string; message: string; details?: Record<string, unknown> };
  request_id?: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface Message {
  message: string;
  success: boolean;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface User {
  id: string;
  username: string;
  email: string;
  full_name: string | null;
  avatar_url: string | null;
  reading_level: ReadingLevel | null;
  join_date: string | null;
  last_active: string | null;
  preferences: Record<string, unknown>;
}

export interface BookSummary {
  id: string;
  title: string;
  author: string;
  cover_url: string | null;
  genres: string[] | null;
  average_rating: number | null;
  publication_year: number | null;
  page_count: number | null;
}

export interface BookDetail extends BookSummary {
  description: string | null;
  isbn: string | null;
  moods: Record<string, number> | null;
  reading_level: string | null;
  rating_count: number;
  source_ids: Record<string, unknown> | null;
  created_at: string | null;
  user_status: LibraryStatus | null;
  user_rating: number | null;
  user_progress_percentage: number | null;
  has_playlist: boolean;
  similar_books: BookSummary[];
}

export interface Track {
  id: string;
  title: string;
  artist: string;
  album: string | null;
  duration_ms: number | null;
  preview_url: string | null;
  external_url: string | null;
  artwork_url: string | null;
  source: PlaylistSource;
}

export interface Playlist {
  id: string;
  book_id: string;
  playlist_name: string;
  description: string | null;
  source: PlaylistSource;
  source_playlist_id: string | null;
  playlist_url: string | null;
  tracks: Track[];
  mood_match_score: number | null;
  genre_match_score: number | null;
  created_at: string | null;
}

export interface PlaylistWithBook {
  playlist: Playlist;
  book: BookSummary;
  user_match_score: number | null;
}

export interface LibraryItem {
  book: BookSummary;
  status: LibraryStatus | null;
  rating: number | null;
  review_text: string | null;
  progress_percentage: number | null;
  started_reading_at: string | null;
  finished_reading_at: string | null;
  last_interaction_at: string | null;
  added_at: string;
}

export interface FactorContribution {
  name: string;
  score: number;
  weight: number;
  contribution: number;
  explanation: string;
}

export interface Recommendation {
  book: BookSummary;
  match_score: number;
  confidence_score: number;
  factors: FactorContribution[];
  playlist_available: boolean;
}

export interface RecommendationBatch {
  items: Recommendation[];
  generated_at: string;
  cached: boolean;
  strategy: Record<string, unknown>;
}

export interface PersonalizedSummary {
  book_id: string;
  user_id: string;
  summary: string;
  match_score: number;
  confidence_score: number;
  factors: FactorContribution[];
  generated_at: string;
}

export interface ReadingStats {
  total_books_read: number;
  total_books_in_progress: number;
  total_pages_read: number;
  total_reading_seconds: number;
  average_reading_speed: number | null;
  average_completion_rate: number;
  current_streak_days: number;
  longest_streak_days: number;
  favorite_genres: Array<Record<string, unknown>>;
  most_productive_hour: number | null;
  period_days: number | null;
}

export interface Progress {
  book_id: string;
  current_page: number;
  percentage: number;
  reading_speed: number | null;
  total_time_spent: number;
  last_read_at: string;
  device_type: string | null;
  sync_version: number;
  bookmarks: Array<Record<string, unknown>>;
  notes: Array<Record<string, unknown>>;
}

export interface CurrentlyReadingItem {
  book: BookSummary;
  progress: Progress;
  estimated_minutes_left: number | null;
}

export interface SyncResult {
  book_id: string;
  status: 'applied' | 'conflict' | 'unchanged' | 'error';
  sync_version: number;
  progress: Progress | null;
  message: string | null;
}

export interface TasteProfile {
  has_preference_vector: boolean;
  confidence: number;
  confidence_label: string;
  quiz_answers: Record<string, unknown>;
  reading_level: string | null;
}

export interface PreferenceQuizPayload {
  favorite_genres: string[];
  favorite_authors?: string[];
  preferred_moods?: string[];
  reading_level?: ReadingLevel | null;
  preferred_length?: 'short' | 'medium' | 'long' | 'any';
  music_genres?: string[];
  books_per_month?: number | null;
}
