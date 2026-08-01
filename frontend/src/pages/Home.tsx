/**
 * Home: continue reading → personalised picks → trending → genre shelves.
 *
 * Each section loads independently and fails independently. A recommendation
 * engine that isn't warm yet, or a genre with no books, must not blank the
 * whole page — so every block renders its own empty/error state and the rest
 * of the page carries on.
 */

import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  ApiError,
  books as booksApi,
  library as libraryApi,
  reading as readingApi,
  recommendations as recsApi,
} from '../lib/api';
import { useAuth } from '../lib/auth';
import { prettyLabel } from '../lib/format';
import { useBookModal } from '../components/BookModalProvider';
import { useToast } from '../lib/toast';
import BookCard from '../components/BookCard';
import { CardSkeletons, EmptyState, Meter } from '../components/ui';
import type {
  BookSummary,
  CurrentlyReadingItem,
  Recommendation,
} from '../lib/types';

// Canonical slugs from app/utils/taxonomy.py — `Book.genres` is matched
// exactly, so "science fiction" with a space would return nothing.
const SHELF_GENRES = ['fantasy', 'mystery', 'science_fiction', 'romance'];

function useQuickAdd() {
  const toast = useToast();
  const navigate = useNavigate();
  const { user } = useAuth();
  const { notifyLibraryChanged } = useBookModal();

  return useCallback(
    async (book: BookSummary) => {
      // Adding writes to a shelf that only exists for an account. Sending a
      // signed-out visitor to /login beats letting the request 401 and
      // surfacing a failure they cannot act on.
      if (!user) {
        toast.info('sign in first', 'saving books needs an account.');
        navigate('/login');
        return;
      }
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
    },
    [toast, navigate, user, notifyLibraryChanged],
  );
}

function ContinueReading() {
  const { openBook, libraryVersion } = useBookModal();
  const [items, setItems] = useState<CurrentlyReadingItem[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    readingApi
      .currently(10)
      .then((result) => !cancelled && setItems(result))
      .catch(() => !cancelled && setItems([]));
    return () => {
      cancelled = true;
    };
  }, [libraryVersion]);

  if (!items || items.length === 0) return null;

  return (
    <section className="section">
      <h2>◈ continue reading</h2>
      <div className="row-scroll">
        {items.map((item) => (
          <BookCard
            key={item.book.id}
            book={item.book}
            onOpen={openBook}
            badge={`${Math.round(item.progress.percentage)}%`}
          >
            <div className="library-actions">
              <Meter value={item.progress.percentage} />
              {item.estimated_minutes_left != null && (
                <span className="user-rating">
                  ~{item.estimated_minutes_left} min left
                </span>
              )}
            </div>
          </BookCard>
        ))}
      </div>
    </section>
  );
}

function ForYou() {
  const { openBook, libraryVersion } = useBookModal();
  const quickAdd = useQuickAdd();
  const [items, setItems] = useState<Recommendation[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async (refresh = false) => {
    setError(null);
    try {
      const batch = await recsApi.personalized(18, undefined, refresh);
      setItems(batch.items);
    } catch (cause) {
      setError(
        cause instanceof ApiError
          ? cause.message
          : 'Could not load recommendations.',
      );
      setItems([]);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load, libraryVersion]);

  const refresh = async () => {
    setRefreshing(true);
    setItems(null);
    await load(true);
    setRefreshing(false);
  };

  return (
    <section className="section">
      <div className="page-head">
        <h2 style={{ margin: 0 }}>✦ picked for you</h2>
        <button
          type="button"
          className="btn btn-secondary"
          onClick={refresh}
          disabled={refreshing}
        >
          {refreshing ? 'recomputing…' : '⟳ refresh'}
        </button>
      </div>

      {error && <div className="error-message">{error}</div>}

      {items === null ? (
        <CardSkeletons count={6} />
      ) : items.length === 0 ? (
        <EmptyState>
          Nothing personalised yet. Rate a few books or{' '}
          <Link to="/discover">discover something</Link> — the engine needs
          signal before it can pick for you.
        </EmptyState>
      ) : (
        <div className="books-grid">
          {items.map((item) => (
            <BookCard
              key={item.book.id}
              book={item.book}
              onOpen={openBook}
              onAdd={quickAdd}
              badge={item.playlist_available ? '♫ tunes' : null}
            >
              <div className="library-actions">
                <div className="factor-row">
                  <span>match</span>
                  <strong>{Math.round(item.match_score)}%</strong>
                </div>
                <Meter value={item.match_score} />
              </div>
            </BookCard>
          ))}
        </div>
      )}
    </section>
  );
}

// An empty catalogue makes the API stock itself in the background, so an empty
// first response is a "not yet" rather than a "never". Re-ask a few times to
// pick the books up once the ingest lands, then stop — a genre upstream has
// nothing for would otherwise poll forever.
const RESTOCK_POLLS = 5;
const RESTOCK_INTERVAL_MS = 6000;

function Trending() {
  const { openBook } = useBookModal();
  const quickAdd = useQuickAdd();
  const [items, setItems] = useState<BookSummary[] | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    booksApi
      .trending(20)
      .then((result) => !cancelled && setItems(result))
      .catch(() => !cancelled && setItems([]));
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  useEffect(() => {
    if (items === null || items.length > 0 || attempt >= RESTOCK_POLLS) return;
    const timer = setTimeout(() => setAttempt((n) => n + 1), RESTOCK_INTERVAL_MS);
    return () => clearTimeout(timer);
  }, [items, attempt]);

  const stocking = items !== null && items.length === 0 && attempt < RESTOCK_POLLS;

  return (
    <section className="section">
      <h2>▲ trending this week</h2>
      {items === null ? (
        <CardSkeletons count={6} />
      ) : stocking ? (
        <>
          <EmptyState>
            Stocking the shelves from Open Library — this takes a few seconds on
            a cold start. Books will appear here on their own.
          </EmptyState>
          <CardSkeletons count={6} />
        </>
      ) : items.length === 0 ? (
        <EmptyState>
          Nothing in the catalogue yet. Try again shortly, or run the full seed
          with <code>python -m scripts.seed_books</code>.
        </EmptyState>
      ) : (
        <div className="row-scroll">
          {items.map((book) => (
            <BookCard
              key={book.id}
              book={book}
              onOpen={openBook}
              onAdd={quickAdd}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function GenreShelf({ genre }: { genre: string }) {
  const { openBook } = useBookModal();
  const quickAdd = useQuickAdd();
  const [items, setItems] = useState<BookSummary[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    booksApi
      .byGenre(genre, 15)
      .then((page) => !cancelled && setItems(page.items))
      .catch(() => !cancelled && setItems([]));
    return () => {
      cancelled = true;
    };
  }, [genre]);

  // A genre with nothing in it is noise, not information — drop the shelf.
  if (items !== null && items.length === 0) return null;

  return (
    <div className="genre-section">
      <h3>{prettyLabel(genre)}</h3>
      {items === null ? (
        <div className="loading">loading {prettyLabel(genre)}…</div>
      ) : (
        <div className="row-scroll">
          {items.map((book) => (
            <BookCard key={book.id} book={book} onOpen={openBook} onAdd={quickAdd} />
          ))}
        </div>
      )}
    </div>
  );
}

export default function Home() {
  const { user } = useAuth();

  return (
    <div className="home-page">
      <div className="container">
        <div className="page-head">
          <div>
            <h1 className="holo-text" style={{ fontSize: '2rem', margin: 0 }}>
              {user ? `hey ${user.full_name || user.username} ✦` : 'welcome to booktunes ✦'}
            </h1>
            <p className="muted">
              every book here comes with a soundtrack. open one and hit “tunes”.
            </p>
          </div>
          {!user && (
            <Link className="btn" to="/register">
              make an account
            </Link>
          )}
        </div>

        {/*
          Both of these call endpoints that require a bearer token, so they are
          mounted only for a signed-in user — rendering them logged out would
          fire two guaranteed 401s on every visit to the landing page.
        */}
        {user && (
          <>
            <ContinueReading />
            <ForYou />
          </>
        )}
        <Trending />

        <section className="section">
          <h2>◇ browse by genre</h2>
          {SHELF_GENRES.map((genre) => (
            <GenreShelf key={genre} genre={genre} />
          ))}
        </section>
      </div>
    </div>
  );
}
