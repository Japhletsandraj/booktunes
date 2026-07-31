/**
 * The user's shelf.
 *
 * Status counts come from four `total` values rather than one full fetch —
 * `Page.total` is computed server-side, so a `limit=1` request per status is
 * enough for the stat tiles and avoids pulling the whole library to count it.
 */

import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { ApiError, library as libraryApi } from '../lib/api';
import { useBookModal } from '../components/BookModalProvider';
import { useToast } from '../lib/toast';
import BookCard from '../components/BookCard';
import { CardSkeletons, EmptyState, Meter, StarPicker } from '../components/ui';
import type { LibraryItem, LibraryStatus } from '../lib/types';

const STATUSES: Array<{ value: LibraryStatus; label: string }> = [
  { value: 'want_to_read', label: 'want to read' },
  { value: 'currently_reading', label: 'reading' },
  { value: 'read', label: 'read' },
  { value: 'abandoned', label: 'abandoned' },
];

export default function Library() {
  const { openBook, libraryVersion, notifyLibraryChanged } = useBookModal();
  const toast = useToast();

  const [filter, setFilter] = useState<LibraryStatus | null>(null);
  const [items, setItems] = useState<LibraryItem[] | null>(null);
  const [counts, setCounts] = useState<Record<LibraryStatus, number> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const loadCounts = useCallback(async () => {
    try {
      const pages = await Promise.all(
        STATUSES.map((status) => libraryApi.list(status.value, 1, 0)),
      );
      setCounts(
        Object.fromEntries(
          STATUSES.map((status, index) => [status.value, pages[index].total]),
        ) as Record<LibraryStatus, number>,
      );
    } catch {
      setCounts(null);
    }
  }, []);

  const load = useCallback(async () => {
    setError(null);
    try {
      const page = await libraryApi.list(filter ?? undefined, 100, 0);
      setItems(page.items);
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : 'Could not load your library.',
      );
      setItems([]);
    }
  }, [filter]);

  useEffect(() => {
    setItems(null);
    load();
  }, [load, libraryVersion]);

  useEffect(() => {
    loadCounts();
  }, [loadCounts, libraryVersion]);

  const changeStatus = async (bookId: string, status: LibraryStatus) => {
    setBusyId(bookId);
    try {
      await libraryApi.update(bookId, { status });
      toast.ok('moved', 'shelf updated.');
      notifyLibraryChanged();
    } catch (cause) {
      toast.err(
        'could not update',
        cause instanceof ApiError ? cause.message : 'Unknown error.',
      );
    } finally {
      setBusyId(null);
    }
  };

  const rate = async (bookId: string, rating: number) => {
    setBusyId(bookId);
    try {
      await libraryApi.update(bookId, { rating });
      toast.ok('rated', `${rating}★ saved.`);
      notifyLibraryChanged();
    } catch (cause) {
      toast.err(
        'could not rate',
        cause instanceof ApiError ? cause.message : 'Unknown error.',
      );
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (bookId: string, title: string) => {
    setBusyId(bookId);
    try {
      const result = await libraryApi.remove(bookId);
      toast.ok(`removed “${title}”`, result.message);
      notifyLibraryChanged();
    } catch (cause) {
      toast.err(
        'could not remove',
        cause instanceof ApiError ? cause.message : 'Unknown error.',
      );
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="library-page">
      <div className="container">
        <h1 className="holo-text" style={{ fontSize: '2rem' }}>
          your library
        </h1>

        <div className="library-stats">
          {STATUSES.map((status) => (
            <div
              key={status.value}
              className={`stat-card${filter === status.value ? ' active' : ''}`}
              role="button"
              tabIndex={0}
              onClick={() =>
                setFilter((current) =>
                  current === status.value ? null : status.value,
                )
              }
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  setFilter((current) =>
                    current === status.value ? null : status.value,
                  );
                }
              }}
            >
              <div className="stat-number">
                {counts ? counts[status.value] : '—'}
              </div>
              <div className="stat-label">
                {status.label}
              </div>
            </div>
          ))}
        </div>

        <div className="library-filter">
          <button
            type="button"
            className={`filter-btn${filter === null ? ' active' : ''}`}
            onClick={() => setFilter(null)}
          >
            everything
          </button>
          {STATUSES.map((status) => (
            <button
              type="button"
              key={status.value}
              className={`filter-btn${filter === status.value ? ' active' : ''}`}
              onClick={() => setFilter(status.value)}
            >
              {status.label}
            </button>
          ))}
        </div>

        {error && <div className="error-message">{error}</div>}

        {items === null ? (
          <CardSkeletons count={8} />
        ) : items.length === 0 ? (
          <EmptyState>
            {filter
              ? 'Nothing on this shelf yet.'
              : 'Your library is empty.'}{' '}
            <Link to="/discover">Go find something</Link>.
          </EmptyState>
        ) : (
          <div className="books-grid">
            {items.map((item) => (
              <BookCard
                key={item.book.id}
                book={item.book}
                onOpen={openBook}
                badge={
                  item.progress_percentage != null
                    ? `${Math.round(item.progress_percentage)}%`
                    : null
                }
              >
                <div className="library-actions">
                  {item.progress_percentage != null && (
                    <Meter value={item.progress_percentage} />
                  )}

                  <select
                    className="status-select"
                    value={item.status ?? 'want_to_read'}
                    disabled={busyId === item.book.id}
                    aria-label={`Shelf for ${item.book.title}`}
                    onChange={(event) =>
                      changeStatus(
                        item.book.id,
                        event.target.value as LibraryStatus,
                      )
                    }
                  >
                    {STATUSES.map((status) => (
                      <option key={status.value} value={status.value}>
                        {status.label}
                      </option>
                    ))}
                  </select>

                  <div className="user-rating">
                    <StarPicker
                      value={item.rating}
                      disabled={busyId === item.book.id}
                      onChange={(rating) => rate(item.book.id, rating)}
                    />
                  </div>

                  <button
                    type="button"
                    className="btn btn-danger"
                    style={{ fontSize: '0.75rem', padding: '0.3rem 0.6rem' }}
                    disabled={busyId === item.book.id}
                    onClick={() => remove(item.book.id, item.book.title)}
                  >
                    remove
                  </button>
                </div>
              </BookCard>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
