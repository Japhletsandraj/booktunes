/**
 * Search + browse.
 *
 * The semantic toggle maps to `?semantic=true`, which ranks by embedding
 * distance instead of keywords — it finds thematically similar books that
 * share no words with the query. It needs the books to have embeddings, so the
 * empty state says so rather than just showing "no results".
 *
 * Requests are aborted on change: typing "dragons" then "dragon riders"
 * otherwise races, and the slower first response can overwrite the second.
 */

import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import {
  ApiError,
  books as booksApi,
  library as libraryApi,
  recommendations as recsApi,
} from '../lib/api';
import { useBookModal } from '../components/BookModalProvider';
import { prettyLabel } from '../lib/format';
import { useToast } from '../lib/toast';
import BookCard from '../components/BookCard';
import { CardSkeletons, EmptyState } from '../components/ui';
import type { BookSummary } from '../lib/types';

type Mode = 'search' | 'genre' | 'mood';

const PAGE_SIZE = 24;

export default function Discover() {
  const { openBook, notifyLibraryChanged } = useBookModal();
  const toast = useToast();

  const [query, setQuery] = useState('');
  const [semantic, setSemantic] = useState(false);
  const [mode, setMode] = useState<Mode>('search');
  const [activeGenre, setActiveGenre] = useState<string | null>(null);
  const [activeMood, setActiveMood] = useState<string | null>(null);

  const [genres, setGenres] = useState<string[]>([]);
  const [moods, setMoods] = useState<string[]>([]);

  const [results, setResults] = useState<BookSummary[] | null>(null);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    booksApi.genres().then(setGenres).catch(() => setGenres([]));
    booksApi.moods().then(setMoods).catch(() => setMoods([]));
  }, []);

  const run = useCallback(
    async (nextOffset: number, append: boolean) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      setLoading(true);
      setError(null);

      try {
        if (mode === 'mood' && activeMood) {
          const items = await booksApi.byMood(activeMood, PAGE_SIZE);
          setResults(items);
          setTotal(items.length);
          setOffset(0);
          return;
        }

        if (mode === 'genre' && activeGenre) {
          const page = await booksApi.byGenre(activeGenre, PAGE_SIZE, nextOffset);
          setResults((current) =>
            append && current ? [...current, ...page.items] : page.items,
          );
          setTotal(page.total);
          setOffset(nextOffset);
          return;
        }

        const page = await booksApi.search(
          {
            q: query.trim() || undefined,
            semantic: semantic && Boolean(query.trim()),
            limit: PAGE_SIZE,
            offset: nextOffset,
          },
          controller.signal,
        );
        setResults((current) =>
          append && current ? [...current, ...page.items] : page.items,
        );
        setTotal(page.total);
        setOffset(nextOffset);
      } catch (cause) {
        if (controller.signal.aborted) return;
        setError(
          cause instanceof ApiError ? cause.message : 'Search failed. Try again.',
        );
        if (!append) setResults([]);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    },
    [mode, activeGenre, activeMood, query, semantic],
  );

  // Initial + facet-driven loads. Free-text search is submit-driven instead,
  // so typing doesn't fire a request per keystroke.
  useEffect(() => {
    if (mode === 'search' && query.trim()) return;
    run(0, false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, activeGenre, activeMood]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    setMode('search');
    setActiveGenre(null);
    setActiveMood(null);
    run(0, false);
  };

  const quickAdd = async (book: BookSummary) => {
    try {
      await libraryApi.add({ book_id: book.id, status: 'want_to_read' });
      toast.ok('added', `“${book.title}” is on your want-to-read shelf.`);
      notifyLibraryChanged();
    } catch (cause) {
      toast.err(
        'could not add',
        cause instanceof ApiError ? cause.message : 'Unknown error.',
      );
    }
  };

  const surpriseMe = async () => {
    if (moods.length === 0) return;
    const mood = moods[Math.floor(Math.random() * moods.length)];
    setLoading(true);
    setError(null);
    try {
      // The personalised mood route re-scores mood matches against your own
      // profile, so it beats the plain catalogue lookup when signed in.
      const items = await recsApi.byMood(mood, PAGE_SIZE);
      setMode('mood');
      setActiveMood(mood);
      setActiveGenre(null);
      setResults(items.map((item) => item.book));
      setTotal(items.length);
    } catch {
      setActiveMood(mood);
      setMode('mood');
    } finally {
      setLoading(false);
    }
  };

  const canLoadMore =
    mode !== 'mood' && results !== null && results.length < total && !loading;

  const heading =
    mode === 'genre' && activeGenre
      ? `genre: ${prettyLabel(activeGenre)}`
      : mode === 'mood' && activeMood
        ? `mood: ${prettyLabel(activeMood)}`
        : query.trim()
          ? `results for “${query.trim()}”`
          : 'the whole catalogue';

  return (
    <div className="discovery-page">
      <div className="container">
        <h1 className="holo-text" style={{ fontSize: '2rem' }}>
          ✦ discover
        </h1>

        <form className="search-form" onSubmit={onSubmit} role="search">
          <input
            className="search-input"
            type="search"
            placeholder="title, author, or a vibe…"
            aria-label="Search books"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <button type="submit" className="search-btn" disabled={loading}>
            {loading ? '…' : 'search'}
          </button>
          <button type="button" className="btn btn-secondary" onClick={surpriseMe}>
            surprise me
          </button>
        </form>

        <div className="filter-bar">
          <label className="toggle-semantic">
            <input
              type="checkbox"
              checked={semantic}
              onChange={(event) => setSemantic(event.target.checked)}
            />
            semantic search
          </label>
          <span className="muted" style={{ fontSize: '0.78rem' }}>
            rank by meaning, not keywords — finds books that share no words with
            your query
          </span>
        </div>

        <div className="filter-bar">
          <button
            type="button"
            className={`chip${mode === 'search' && !activeGenre ? ' active' : ''}`}
            onClick={() => {
              setMode('search');
              setActiveGenre(null);
              setActiveMood(null);
            }}
          >
            all
          </button>
          {genres.slice(0, 14).map((genre) => (
            <button
              type="button"
              key={genre}
              className={`chip${activeGenre === genre ? ' active' : ''}`}
              onClick={() => {
                setMode('genre');
                setActiveGenre(genre);
                setActiveMood(null);
              }}
            >
              {prettyLabel(genre)}
            </button>
          ))}
        </div>

        <div className="filter-bar">
          {moods.slice(0, 12).map((mood) => (
            <button
              type="button"
              key={mood}
              className={`chip${activeMood === mood ? ' active' : ''}`}
              onClick={() => {
                setMode('mood');
                setActiveMood(mood);
                setActiveGenre(null);
              }}
            >
              ✧ {prettyLabel(mood)}
            </button>
          ))}
        </div>

        <div className="page-head">
          <h2 style={{ margin: 0, fontSize: '1.2rem' }}>{heading}</h2>
          {results !== null && (
            <span className="muted">
              {mode === 'mood' ? results.length : total} book
              {(mode === 'mood' ? results.length : total) === 1 ? '' : 's'}
            </span>
          )}
        </div>

        {error && <div className="error-message">{error}</div>}

        {results === null && loading ? (
          <CardSkeletons count={12} />
        ) : results && results.length === 0 ? (
          <EmptyState>
            {semantic
              ? 'No semantic matches. Semantic search only ranks books that already have embeddings — run the rebuild job, or untick the toggle for keyword search.'
              : 'Nothing matched. Try a broader query, or pick a genre chip above.'}
          </EmptyState>
        ) : (
          <>
            <div className="books-grid">
              {results?.map((book) => (
                <BookCard
                  key={book.id}
                  book={book}
                  onOpen={openBook}
                  onAdd={quickAdd}
                />
              ))}
            </div>

            {canLoadMore && (
              <div className="text-center" style={{ marginTop: '1.6rem' }}>
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => run(offset + PAGE_SIZE, true)}
                >
                  load more ↓
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
