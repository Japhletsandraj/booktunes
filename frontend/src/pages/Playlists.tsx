/**
 * Saved playlists, presented as a rack of spinning discs.
 *
 * Preview clips are null for every YouTube Music track by design, so the
 * per-track action here is "open externally"; the inline player only appears
 * where the source actually returned a preview URL.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { ApiError, playlists as playlistsApi } from '../lib/api';
import { useBookModal } from '../components/BookModalProvider';
import { formatDuration } from '../lib/format';
import { useToast } from '../lib/toast';
import { EmptyState, InlineLoader, Meter } from '../components/ui';
import type { PlaylistWithBook, Track } from '../lib/types';

export default function Playlists() {
  const { openBook } = useBookModal();
  const toast = useToast();

  const [items, setItems] = useState<PlaylistWithBook[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [playingId, setPlayingId] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setItems(await playlistsApi.mine(50));
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : 'Could not load your playlists.',
      );
      setItems([]);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

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
    audio.play().catch(() => setPlayingId(null));
    audioRef.current = audio;
    setPlayingId(track.id);
  };

  const unsave = async (playlistId: string, name: string) => {
    try {
      await playlistsApi.unsave(playlistId);
      toast.ok('removed', `“${name}” is off your rack.`);
      load();
    } catch (cause) {
      toast.err(
        'could not remove',
        cause instanceof ApiError ? cause.message : 'Unknown error.',
      );
    }
  };

  return (
    <div className="container">
      <h1 className="holo-text" style={{ fontSize: '2rem' }}>
        your rack
      </h1>
      <p className="section-lead">
        soundtracks you saved. each one was built from its book's mood and genre.
      </p>

      {error && <div className="error-message">{error}</div>}

      {items === null ? (
        <InlineLoader label="loading your playlists" />
      ) : items.length === 0 ? (
        <EmptyState>
          No saved playlists yet. Open any book, hit the <strong>tunes</strong>{' '}
          tab, then <strong>save playlist</strong> —{' '}
          <Link to="/discover">start here</Link>.
        </EmptyState>
      ) : (
        <div className="stack">
          {items.map(({ playlist, book, user_match_score }) => {
            const isOpen = expanded === playlist.id;
            return (
              <div
                key={playlist.id}
                className="activity-item"
                style={{ flexDirection: 'column', alignItems: 'stretch' }}
              >
                <div className="playlist-head">
                  <div
                    className={`cd${isOpen && playingId ? '' : ' cd--paused'}`}
                    aria-hidden="true"
                  />

                  <div className="playlist-meta">
                    <div className="activity-book">{playlist.playlist_name}</div>
                    <button
                      type="button"
                      className="btn btn-secondary"
                      style={{
                        fontSize: '0.72rem',
                        padding: '0.2rem 0.6rem',
                        marginBottom: '0.4rem',
                      }}
                      onClick={() => openBook(book)}
                    >
                      {book.title}
                    </button>
                    <div>
                      <span className="playlist-source">{playlist.source}</span>{' '}
                      <span className="muted" style={{ fontSize: '0.78rem' }}>
                        {playlist.tracks.length} tracks
                      </span>
                    </div>

                    {user_match_score != null && (
                      <div style={{ marginTop: '0.5rem', maxWidth: 260 }}>
                        <div className="factor-row">
                          <span>taste match</span>
                          <strong>{Math.round(user_match_score)}%</strong>
                        </div>
                        <Meter value={user_match_score} />
                      </div>
                    )}
                  </div>

                  <div className="btn-row">
                    <button
                      type="button"
                      className="btn btn-primary"
                      onClick={() => setExpanded(isOpen ? null : playlist.id)}
                    >
                      {isOpen ? '▲ hide' : '▼ tracks'}
                    </button>
                    <button
                      type="button"
                      className="btn btn-danger"
                      onClick={() => unsave(playlist.id, playlist.playlist_name)}
                    >
                      unsave
                    </button>
                  </div>
                </div>

                {isOpen && (
                  <ul className="track-list" style={{ marginTop: '0.8rem' }}>
                    {playlist.tracks.map((track, index) => (
                      <li className="track" key={`${track.id}-${index}`}>
                        <span className="track__num">
                          {String(index + 1).padStart(2, '0')}
                        </span>
                        {track.artwork_url ? (
                          <img
                            className="track__art"
                            src={track.artwork_url}
                            alt=""
                            loading="lazy"
                          />
                        ) : (
                          <div className="track__art" aria-hidden="true" />
                        )}
                        <div className="track__text">
                          <div className="track__title">{track.title}</div>
                          <div className="track__artist">
                            {track.artist}
                            {track.duration_ms
                              ? ` · ${formatDuration(track.duration_ms)}`
                              : ''}
                          </div>
                        </div>
                        <div className="track__actions">
                          <button
                            type="button"
                            className="track__btn"
                            disabled={!track.preview_url}
                            title={
                              track.preview_url
                                ? 'Play 30s preview'
                                : 'No preview clip from this source'
                            }
                            onClick={() => toggleTrack(track)}
                          >
                            {playingId === track.id ? '❚❚' : '▶'}
                          </button>
                          {track.external_url && (
                            <a
                              className="track__btn"
                              href={track.external_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              title="Open externally"
                            >
                              ↗
                            </a>
                          )}
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
