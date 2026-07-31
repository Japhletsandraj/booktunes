/**
 * The book detail modal — the "minecraft modal" from the source stylesheet,
 * kept pixel-styled and given a blocky 3D mascot, plus tabs for the parts of
 * the API that hang off a single book.
 *
 * Playlist behaviour deserves a note: `GET /playlists/book/{id}` generates one
 * on demand if none exists, which fans out to several upstream music searches
 * and can take a few seconds. So it is only fetched when the Soundtrack tab is
 * actually opened, never eagerly with the book detail.
 *
 * Previews are null for every YouTube Music track (the API documents this —
 * YT Music has no preview-clip concept). The player therefore treats
 * `external_url` as the primary action and inline audio as the bonus, rather
 * than showing a broken play button.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ApiError,
  books as booksApi,
  library as libraryApi,
  playlists as playlistsApi,
  recommendations as recsApi,
} from '../lib/api';
import { formatDuration, prettyLabel } from '../lib/format';
import { useToast } from '../lib/toast';
import type {
  BookDetail,
  BookSummary,
  LibraryStatus,
  PersonalizedSummary,
  PlaylistWithBook,
  Track,
} from '../lib/types';
import { InlineLoader, Meter, StarPicker, Stars } from './ui';

type Tab = 'about' | 'tunes' | 'why' | 'similar';

const TABS: Array<{ id: Tab; label: string }> = [
  { id: 'about', label: 'about' },
  { id: 'tunes', label: 'tunes' },
  { id: 'why', label: 'why this' },
  { id: 'similar', label: 'similar' },
];

const STATUS_LABELS: Record<LibraryStatus, string> = {
  want_to_read: 'want to read',
  currently_reading: 'currently reading',
  read: 'read',
  abandoned: 'abandoned',
};

// --- Track row -----------------------------------------------------------

function TrackRow({
  track,
  index,
  playingId,
  onToggle,
}: {
  track: Track;
  index: number;
  playingId: string | null;
  onToggle: (track: Track) => void;
}) {
  const isPlaying = playingId === track.id;
  const playable = Boolean(track.preview_url);

  return (
    <li className="track">
      <span className="track__num">{String(index + 1).padStart(2, '0')}</span>

      {track.artwork_url ? (
        <img className="track__art" src={track.artwork_url} alt="" loading="lazy" />
      ) : (
        <div className="track__art" aria-hidden="true" />
      )}

      <div className="track__text">
        <div className="track__title">{track.title}</div>
        <div className="track__artist">
          {track.artist}
          {track.duration_ms ? ` · ${formatDuration(track.duration_ms)}` : ''}
        </div>
      </div>

      <div className="track__actions">
        {isPlaying && (
          <span className="eq" aria-label="Now playing">
            <span />
            <span />
            <span />
            <span />
          </span>
        )}

        <button
          type="button"
          className="track__btn"
          disabled={!playable}
          title={
            playable
              ? isPlaying
                ? 'Pause preview'
                : 'Play 30s preview'
              : 'No preview clip for this source — use the open button'
          }
          aria-label={isPlaying ? 'Pause preview' : 'Play preview'}
          onClick={() => onToggle(track)}
        >
          {isPlaying ? '❚❚' : '▶'}
        </button>

        {track.external_url && (
          <a
            className="track__btn"
            href={track.external_url}
            target="_blank"
            rel="noopener noreferrer"
            title="Open in the music service"
            aria-label={`Open ${track.title} externally`}
          >
            ↗
          </a>
        )}
      </div>
    </li>
  );
}

// --- Tabs ----------------------------------------------------------------

function TunesTab({ bookId }: { bookId: string }) {
  const toast = useToast();
  const [data, setData] = useState<PlaylistWithBook | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [playingId, setPlayingId] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await playlistsApi.forBook(bookId));
    } catch (cause) {
      setError(
        cause instanceof ApiError
          ? cause.message
          : 'Could not load a soundtrack for this book.',
      );
    } finally {
      setLoading(false);
    }
  }, [bookId]);

  useEffect(() => {
    load();
  }, [load]);

  // Stop audio when the tab or modal goes away.
  useEffect(
    () => () => {
      audioRef.current?.pause();
      audioRef.current = null;
    },
    [],
  );

  const toggleTrack = (track: Track) => {
    if (!track.preview_url) return;

    if (playingId === track.id) {
      audioRef.current?.pause();
      setPlayingId(null);
      return;
    }

    audioRef.current?.pause();
    const audio = new Audio(track.preview_url);
    audio.volume = 0.75;
    audio.addEventListener('ended', () => setPlayingId(null));
    audio.play().catch(() => {
      toast.err('playback blocked', 'your browser refused to autoplay audio.');
      setPlayingId(null);
    });
    audioRef.current = audio;
    setPlayingId(track.id);
  };

  const regenerate = async () => {
    setLoading(true);
    try {
      await playlistsApi.generate({ book_id: bookId, force_regenerate: true });
      await load();
      toast.ok('remixed', 'a fresh soundtrack was generated.');
    } catch (cause) {
      toast.err(
        'could not regenerate',
        cause instanceof ApiError ? cause.message : 'Unknown error.',
      );
      setLoading(false);
    }
  };

  const savePlaylist = async () => {
    if (!data) return;
    setSaving(true);
    try {
      const result = await playlistsApi.save(data.playlist.id);
      toast.ok('saved', result.message);
    } catch (cause) {
      toast.err(
        'could not save',
        cause instanceof ApiError ? cause.message : 'Unknown error.',
      );
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <InlineLoader label="spinning up a soundtrack" />;

  if (error) {
    return (
      <div className="stack">
        <div className="error-message">{error}</div>
        <p className="muted" style={{ fontSize: '0.62rem', lineHeight: 2 }}>
          Playlists come from Deezer playlist search resolved through YouTube
          Music. Neither needs an API key, so this is usually a transient
          network problem rather than a missing setting.
        </p>
        <button type="button" className="btn btn-primary" onClick={load}>
          try again
        </button>
      </div>
    );
  }

  if (!data) return null;

  const { playlist, user_match_score } = data;

  return (
    <div className="stack">
      <div className="playlist-head">
        <div className={`cd${playingId ? '' : ' cd--paused'}`} aria-hidden="true" />
        <div className="playlist-meta">
          <h3>{playlist.playlist_name}</h3>
          <span className="playlist-source">{playlist.source}</span>
          {user_match_score != null && (
            <div style={{ marginTop: '0.5rem' }}>
              <div className="factor-row">
                <span>taste match</span>
                <strong>{Math.round(user_match_score)}%</strong>
              </div>
              <Meter value={user_match_score} />
            </div>
          )}
        </div>
      </div>

      {playlist.tracks.length === 0 ? (
        <div className="empty-state">This playlist came back empty.</div>
      ) : (
        <ul className="track-list">
          {playlist.tracks.map((track, index) => (
            <TrackRow
              key={`${track.id}-${index}`}
              track={track}
              index={index}
              playingId={playingId}
              onToggle={toggleTrack}
            />
          ))}
        </ul>
      )}

      <div className="btn-row">
        <button
          type="button"
          className="btn btn-primary"
          onClick={savePlaylist}
          disabled={saving}
        >
          {saving ? 'saving…' : '♥ save playlist'}
        </button>
        <button type="button" className="btn btn-secondary" onClick={regenerate}>
          ⟳ remix
        </button>
        {playlist.playlist_url && (
          <a
            className="btn btn-secondary"
            href={playlist.playlist_url}
            target="_blank"
            rel="noopener noreferrer"
          >
            ↗ open
          </a>
        )}
      </div>
    </div>
  );
}

function WhyTab({ bookId }: { bookId: string }) {
  const [data, setData] = useState<PersonalizedSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    recsApi
      .summary(bookId)
      .then((result) => !cancelled && setData(result))
      .catch((cause: unknown) => {
        if (cancelled) return;
        setError(
          cause instanceof ApiError ? cause.message : 'Could not build a summary.',
        );
      });
    return () => {
      cancelled = true;
    };
  }, [bookId]);

  if (error) return <div className="error-message">{error}</div>;
  if (!data) return <InlineLoader label="reading your taste" />;

  return (
    <div className="stack">
      <p>{data.summary}</p>

      <div>
        <div className="factor-row">
          <span>overall match</span>
          <strong>{Math.round(data.match_score)}%</strong>
        </div>
        <Meter value={data.match_score} />
      </div>

      <div>
        <div className="factor-row">
          <span>confidence</span>
          <strong>{Math.round(data.confidence_score)}%</strong>
        </div>
        <Meter value={data.confidence_score} />
      </div>

      <div style={{ marginTop: '0.6rem' }}>
        {data.factors.map((factor) => (
          <div key={factor.name} style={{ marginBottom: '0.55rem' }}>
            <div className="factor-row">
              <span>{factor.name.replace(/_/g, ' ')}</span>
              <strong>{Math.round(factor.score)}</strong>
            </div>
            <Meter value={factor.score} />
          </div>
        ))}
      </div>
    </div>
  );
}

// --- Modal ---------------------------------------------------------------

export default function BookModal({
  book,
  onClose,
  onOpenBook,
  onLibraryChange,
}: {
  book: BookSummary;
  onClose: () => void;
  onOpenBook: (next: BookSummary) => void;
  onLibraryChange?: () => void;
}) {
  const toast = useToast();
  const [tab, setTab] = useState<Tab>('about');
  const [detail, setDetail] = useState<BookDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const dialogRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setLoadError(null);
    setTab('about');

    booksApi
      .detail(book.id)
      .then((result) => !cancelled && setDetail(result))
      .catch((cause: unknown) => {
        if (cancelled) return;
        setLoadError(
          cause instanceof ApiError ? cause.message : 'Could not load this book.',
        );
      });

    return () => {
      cancelled = true;
    };
  }, [book.id]);

  // Escape to close, and lock background scroll while open.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    dialogRef.current?.focus();

    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = previousOverflow;
    };
  }, [onClose]);

  const addToLibrary = async (status: LibraryStatus) => {
    setBusy(true);
    try {
      await libraryApi.add({ book_id: book.id, status });
      setDetail((current) => (current ? { ...current, user_status: status } : current));
      toast.ok('shelved', `“${book.title}” → ${STATUS_LABELS[status]}.`);
      onLibraryChange?.();
    } catch (cause) {
      toast.err(
        'could not add',
        cause instanceof ApiError ? cause.message : 'Unknown error.',
      );
    } finally {
      setBusy(false);
    }
  };

  const rate = async (rating: number) => {
    setBusy(true);
    try {
      // PUT requires an existing row, so add first when the book is new to
      // the shelf. `add` is idempotent server-side, which makes this safe.
      if (detail?.user_status) {
        await libraryApi.update(book.id, { rating });
      } else {
        await libraryApi.add({ book_id: book.id, status: 'read', rating });
      }
      setDetail((current) =>
        current
          ? { ...current, user_rating: rating, user_status: current.user_status ?? 'read' }
          : current,
      );
      toast.ok('rated', `${rating}★ recorded — recommendations will adjust.`);
      onLibraryChange?.();
    } catch (cause) {
      toast.err(
        'could not rate',
        cause instanceof ApiError ? cause.message : 'Unknown error.',
      );
    } finally {
      setBusy(false);
    }
  };

  const notInterested = async () => {
    setBusy(true);
    try {
      const result = await recsApi.feedback({
        book_id: book.id,
        feedback_type: 'not_interested',
      });
      toast.ok('noted', result.message);
      onLibraryChange?.();
      onClose();
    } catch (cause) {
      toast.err(
        'could not send feedback',
        cause instanceof ApiError ? cause.message : 'Unknown error.',
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="minecraft-modal"
      role="presentation"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="minecraft-modal-content"
        role="dialog"
        aria-modal="true"
        aria-label={book.title}
        tabIndex={-1}
        ref={dialogRef}
      >
        <button
          type="button"
          className="minecraft-close"
          onClick={onClose}
          aria-label="Close"
        >
          ×
        </button>

        <div className="minecraft-modal-flex">
          <div>
            <div className="minecraft-tabs" role="tablist">
              {TABS.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  role="tab"
                  aria-selected={tab === item.id}
                  className={`minecraft-tab-btn${tab === item.id ? ' active' : ''}`}
                  onClick={() => setTab(item.id)}
                >
                  {item.label}
                </button>
              ))}
            </div>

            <div className="minecraft-character" aria-hidden="true">
              <div className="mc-head" />
              <div className="mc-arms" />
              <div className="mc-body" />
              <div className="mc-legs" />
              <div className="mc-caption">your librarian</div>
            </div>
          </div>

          <div className="minecraft-details">
            {loadError && <div className="error-message">{loadError}</div>}

            <h2>{book.title}</h2>
            <h4>by {book.author}</h4>

            {tab === 'about' && (
              <div className="minecraft-tab-content">
                {book.cover_url && (
                  <img className="minecraft-book-img" src={book.cover_url} alt="" />
                )}

                <Stars value={book.average_rating} />

                <p>
                  {detail
                    ? detail.description ||
                      'No description on file for this one — the catalogue entry came from a source that omits blurbs.'
                    : 'Loading…'}
                </p>

                <div style={{ clear: 'both' }} />

                {detail && (
                  <>
                    <div className="genres-list">
                      {(detail.genres ?? []).map((genre) => (
                        <span className="genre-tag" key={genre}>
                          {prettyLabel(genre)}
                        </span>
                      ))}
                      {detail.page_count && (
                        <span className="genre-tag">{detail.page_count} pages</span>
                      )}
                      {detail.reading_level && (
                        <span className="genre-tag">{detail.reading_level}</span>
                      )}
                    </div>

                    {detail.moods && Object.keys(detail.moods).length > 0 && (
                      <div style={{ marginTop: '1rem' }}>
                        {Object.entries(detail.moods)
                          .sort((a, b) => Number(b[1]) - Number(a[1]))
                          .slice(0, 5)
                          .map(([mood, strength]) => (
                            <div key={mood} style={{ marginBottom: '0.45rem' }}>
                              <div className="factor-row">
                                <span>{prettyLabel(mood)}</span>
                                <strong>{Math.round(Number(strength) * 100)}%</strong>
                              </div>
                              <Meter value={Number(strength) * 100} />
                            </div>
                          ))}
                      </div>
                    )}

                    <div style={{ marginTop: '1.1rem' }}>
                      <div className="factor-row">
                        <span>your rating</span>
                        <StarPicker
                          value={detail.user_rating}
                          onChange={rate}
                          disabled={busy}
                        />
                      </div>
                      {detail.user_status && (
                        <p
                          className="muted"
                          style={{
                            background: 'none',
                            border: 'none',
                            padding: 0,
                            fontSize: '0.55rem',
                          }}
                        >
                          on your shelf as: {STATUS_LABELS[detail.user_status]}
                        </p>
                      )}
                    </div>

                    <div className="btn-row" style={{ marginTop: '1rem' }}>
                      <button
                        type="button"
                        className="btn btn-primary"
                        disabled={busy}
                        onClick={() => addToLibrary('want_to_read')}
                      >
                        + want to read
                      </button>
                      <button
                        type="button"
                        className="btn btn-secondary"
                        disabled={busy}
                        onClick={() => addToLibrary('currently_reading')}
                      >
                        ▶ reading now
                      </button>
                      <button
                        type="button"
                        className="btn btn-danger"
                        disabled={busy}
                        onClick={notInterested}
                      >
                        ✕ not for me
                      </button>
                    </div>
                  </>
                )}
              </div>
            )}

            {tab === 'tunes' && (
              <div className="minecraft-tab-content">
                <TunesTab bookId={book.id} />
              </div>
            )}

            {tab === 'why' && (
              <div className="minecraft-tab-content">
                <WhyTab bookId={book.id} />
              </div>
            )}

            {tab === 'similar' && (
              <div className="minecraft-tab-content">
                {!detail ? (
                  <InlineLoader />
                ) : detail.similar_books.length === 0 ? (
                  <div className="empty-state">
                    No neighbours yet — similarity needs this book to have an
                    embedding, which the nightly job builds.
                  </div>
                ) : (
                  <div className="books-grid">
                    {detail.similar_books.map((similar) => (
                      <div
                        key={similar.id}
                        className="book-card"
                        role="button"
                        tabIndex={0}
                        onClick={() => onOpenBook(similar)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter') onOpenBook(similar);
                        }}
                      >
                        <div className="book-card__solid">
                          <div className="book-card__pages" />
                          <div className="book-card__spine" />
                          <div className="book-card__face">
                            {similar.cover_url ? (
                              <img src={similar.cover_url} alt="" loading="lazy" />
                            ) : (
                              <div className="book-card__placeholder">
                                {similar.title}
                              </div>
                            )}
                          </div>
                        </div>
                        <div className="book-info">
                          <div className="book-title">{similar.title}</div>
                          <div className="book-authors">{similar.author}</div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
