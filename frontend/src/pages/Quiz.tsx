/**
 * Cold-start preference quiz.
 *
 * `POST /auth/preferences` both stores the answers and immediately seeds the
 * user's preference embedding, which is what lets a brand-new account get real
 * recommendations instead of a popularity list. Only `favorite_genres` is
 * required by the server (min_length=1), so every later step is skippable.
 *
 * Genres and moods are fetched from `/books/genres` and `/books/moods` rather
 * than hardcoded — those lists are the server's canonical taxonomy, and a
 * genre this UI invents would silently match nothing.
 */

import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ApiError, books as booksApi } from '../lib/api';
import { prettyLabel } from '../lib/format';
import { useAuth } from '../lib/auth';
import { useToast } from '../lib/toast';
import type { ReadingLevel } from '../lib/types';
import { InlineLoader } from '../components/ui';

// Keys are the server's canonical slugs (app/utils/taxonomy.py) — snake_case,
// not prose. A key like "science fiction" would silently never match.
// Keys are the server's canonical slugs (app/utils/taxonomy.py) — snake_case,
// not prose. A key like "science fiction" would silently never match.
//
// Only used as the offline fallback when /books/genres and /books/moods can't
// be reached; the live lists come from the server.
const FALLBACK_GENRES = [
  'fiction', 'nonfiction', 'mystery', 'thriller', 'romance', 'fantasy',
  'science_fiction', 'horror', 'historical_fiction', 'literary_fiction',
  'young_adult', 'children', 'biography', 'memoir', 'history', 'science',
  'philosophy', 'poetry', 'self_help', 'business', 'travel', 'true_crime',
  'adventure', 'classics', 'graphic_novel', 'short_stories', 'dystopian',
];

const FALLBACK_MOODS = [
  'dark', 'uplifting', 'melancholic', 'tense', 'whimsical', 'romantic',
  'contemplative', 'epic', 'cozy', 'mysterious', 'humorous', 'bittersweet',
];

const LENGTHS = [
  { value: 'short', label: 'short', description: 'under 250 pages' },
  { value: 'medium', label: 'medium', description: '250–450 pages' },
  { value: 'long', label: 'long', description: 'give me a doorstop' },
  { value: 'any', label: 'any', description: "length doesn't matter" },
] as const;

const MUSIC_GENRES = [
  'ambient', 'classical', 'jazz', 'lo-fi', 'rock', 'indie',
  'electronic', 'hip hop', 'folk', 'metal', 'pop', 'soundtrack',
];

const LEVELS: ReadingLevel[] = ['beginner', 'intermediate', 'advanced'];

type StepId = 'genres' | 'moods' | 'length' | 'music' | 'pace';

const STEPS: Array<{ id: StepId; title: string; blurb: string }> = [
  {
    id: 'genres',
    title: 'what do you read?',
    blurb: 'Pick at least one — this is the only required answer.',
  },
  {
    id: 'moods',
    title: 'what should it feel like?',
    blurb: 'Mood drives both book picks and the soundtrack behind them.',
  },
  {
    id: 'length',
    title: 'how long?',
    blurb: 'We weight page count against your answer.',
  },
  {
    id: 'music',
    title: 'what do you listen to?',
    blurb: 'Used to score how well a generated playlist fits your taste.',
  },
  {
    id: 'pace',
    title: 'how fast do you read?',
    blurb: 'Sets your reading level and helps estimate time-to-finish.',
  },
];

function toggle(list: string[], value: string, max = 10): string[] {
  if (list.includes(value)) return list.filter((item) => item !== value);
  if (list.length >= max) return list;
  return [...list, value];
}

export default function Quiz() {
  const { saveQuiz, user } = useAuth();
  const navigate = useNavigate();
  const toast = useToast();

  const [step, setStep] = useState(0);
  const [genres, setGenres] = useState<string[]>([]);
  const [moods, setMoods] = useState<string[]>([]);
  const [length, setLength] = useState<'short' | 'medium' | 'long' | 'any'>('any');
  const [musicGenres, setMusicGenres] = useState<string[]>([]);
  const [level, setLevel] = useState<ReadingLevel>(
    (user?.reading_level as ReadingLevel) ?? 'intermediate',
  );
  const [booksPerMonth, setBooksPerMonth] = useState(2);
  const [authors, setAuthors] = useState('');

  const [genreOptions, setGenreOptions] = useState<string[] | null>(null);
  const [moodOptions, setMoodOptions] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Taxonomy endpoints are unauthenticated and cheap; a failure here should
    // not block onboarding, so both fall back to the bundled slug lists.
    booksApi
      .genres()
      .then(setGenreOptions)
      .catch(() => setGenreOptions(FALLBACK_GENRES));
    booksApi
      .moods()
      .then(setMoodOptions)
      .catch(() => setMoodOptions(FALLBACK_MOODS));
  }, []);

  const current = STEPS[step];
  const isLast = step === STEPS.length - 1;
  const canAdvance = current.id !== 'genres' || genres.length > 0;
  const progress = useMemo(() => ((step + 1) / STEPS.length) * 100, [step]);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await saveQuiz({
        favorite_genres: genres,
        favorite_authors: authors
          .split(',')
          .map((name) => name.trim())
          .filter(Boolean)
          .slice(0, 10),
        preferred_moods: moods,
        reading_level: level,
        preferred_length: length,
        music_genres: musicGenres,
        books_per_month: booksPerMonth,
      });
      toast.ok('taste locked in', 'building your first recommendations…');
      navigate('/', { replace: true });
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : 'Could not save your answers.',
      );
      setBusy(false);
    }
  };

  return (
    <div className="quiz-page preference-quiz-page">
      <div className="preference-quiz-container">
        <div className="quiz-header">
          <h1 className="banner-title chrome-text" style={{ fontSize: '2.4rem' }}>
            tune your taste
          </h1>
          <p>{current.blurb}</p>
        </div>

        <div className="quiz-progress">
          <div className="progress-track">
            <div className="progress-bar" style={{ width: `${progress}%` }} />
          </div>
          <div className="progress-steps">
            {STEPS.map((item, index) => (
              <div
                key={item.id}
                className={`progress-step${
                  index === step ? ' active' : index < step ? ' done' : ''
                }`}
                title={item.title}
              >
                {index < step ? '✓' : index + 1}
              </div>
            ))}
          </div>
        </div>

        <h2 style={{ textAlign: 'center' }}>{current.title}</h2>

        {error && (
          <div className="error-message" role="alert">
            {error}
          </div>
        )}

        {current.id === 'genres' &&
          (genreOptions === null ? (
            <InlineLoader label="loading genres" />
          ) : (
            <>
              <div className="quiz-options">
                {genreOptions.map((genre) => (
                  <button
                    type="button"
                    key={genre}
                    className={`quiz-option${genres.includes(genre) ? ' selected' : ''}`}
                    aria-pressed={genres.includes(genre)}
                    onClick={() => setGenres((list) => toggle(list, genre))}
                  >
                    <span className="option-content">
                      <span className="option-label">{prettyLabel(genre)}</span>
                    </span>
                  </button>
                ))}
              </div>
              <p className="muted text-center">{genres.length}/10 selected</p>

              <div className="form-group" style={{ marginTop: '1rem' }}>
                <label htmlFor="authors">favourite authors (optional, comma separated)</label>
                <input
                  id="authors"
                  className="quiz-freeform"
                  placeholder="Ursula K. Le Guin, Terry Pratchett"
                  value={authors}
                  onChange={(event) => setAuthors(event.target.value)}
                />
              </div>
            </>
          ))}

        {current.id === 'moods' &&
          (moodOptions === null ? (
            <InlineLoader label="loading moods" />
          ) : (
            <>
              <div className="quiz-options">
                {moodOptions.map((mood) => (
                  <button
                    type="button"
                    key={mood}
                    className={`quiz-option${moods.includes(mood) ? ' selected' : ''}`}
                    aria-pressed={moods.includes(mood)}
                    onClick={() => setMoods((list) => toggle(list, mood))}
                  >
                    <span className="option-content">
                      <span className="option-label">{prettyLabel(mood)}</span>
                    </span>
                  </button>
                ))}
              </div>
              <p className="muted text-center">{moods.length}/10 selected</p>
            </>
          ))}

        {current.id === 'length' && (
          <div className="quiz-options">
            {LENGTHS.map((option) => (
              <button
                type="button"
                key={option.value}
                className={`quiz-option${length === option.value ? ' selected' : ''}`}
                aria-pressed={length === option.value}
                onClick={() => setLength(option.value)}
              >
                <span className="option-content">
                  <span className="option-label">{option.label}</span>
                  <span className="option-description">{option.description}</span>
                </span>
              </button>
            ))}
          </div>
        )}

        {current.id === 'music' && (
          <>
            <div className="quiz-options">
              {MUSIC_GENRES.map((genre) => (
                <button
                  type="button"
                  key={genre}
                  className={`quiz-option${musicGenres.includes(genre) ? ' selected' : ''}`}
                  aria-pressed={musicGenres.includes(genre)}
                  onClick={() => setMusicGenres((list) => toggle(list, genre))}
                >
                  <span className="option-content">
                    <span className="option-label">{genre}</span>
                  </span>
                </button>
              ))}
            </div>
            <p className="muted text-center">{musicGenres.length}/10 selected</p>
          </>
        )}

        {current.id === 'pace' && (
          <div className="stack">
            <div className="quiz-options">
              {LEVELS.map((value) => (
                <button
                  type="button"
                  key={value}
                  className={`quiz-option${level === value ? ' selected' : ''}`}
                  aria-pressed={level === value}
                  onClick={() => setLevel(value)}
                >
                  <span className="option-content">
                    <span className="option-label">{value}</span>
                  </span>
                </button>
              ))}
            </div>

            <div className="form-group">
              <label htmlFor="pace">
                books per month: <strong>{booksPerMonth}</strong>
              </label>
              <input
                id="pace"
                type="range"
                min={0}
                max={20}
                value={booksPerMonth}
                onChange={(event) => setBooksPerMonth(Number(event.target.value))}
              />
            </div>
          </div>
        )}

        <div className="quiz-actions">
          <button
            type="button"
            className="quiz-btn prev-btn"
            disabled={step === 0 || busy}
            onClick={() => setStep((value) => Math.max(0, value - 1))}
          >
            ← back
          </button>

          {isLast ? (
            <button
              type="button"
              className="quiz-btn next-btn"
              disabled={busy || genres.length === 0}
              onClick={submit}
            >
              {busy ? 'saving…' : 'finish ✦'}
            </button>
          ) : (
            <button
              type="button"
              className="quiz-btn next-btn"
              disabled={!canAdvance || busy}
              onClick={() => setStep((value) => value + 1)}
            >
              next →
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
