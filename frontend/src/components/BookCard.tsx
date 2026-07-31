/**
 * A book rendered as an actual 3D object.
 *
 * The markup is three stacked faces inside a `preserve-3d` box — cover at
 * +11px, page block at -11px, and a spine rotated 90° to bridge them. On hover
 * the whole card rotates on Y, so the spine and the page edges swing into view
 * and it reads as a solid object rather than a picture of one.
 */

import { useState } from 'react';
import type { BookSummary } from '../lib/types';

interface Props {
  book: BookSummary;
  onOpen: (book: BookSummary) => void;
  /** Omit to hide the quick-add affordance (e.g. inside the library itself). */
  onAdd?: (book: BookSummary) => void;
  badge?: string | null;
  children?: React.ReactNode;
}

export default function BookCard({ book, onOpen, onAdd, badge, children }: Props) {
  const [broken, setBroken] = useState(false);
  const showCover = Boolean(book.cover_url) && !broken;

  return (
    // The wrapper carries layout + any trailing controls; the inner element is
    // the 3D object. Nesting .book-card inside .book-card would apply the hover
    // rotation twice.
    <div className="library-item">
      <div
        className="book-card"
        role="button"
        tabIndex={0}
        aria-label={`${book.title} by ${book.author}`}
        onClick={() => onOpen(book)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            onOpen(book);
          }
        }}
      >
        <div className="book-card__solid">
          <div className="book-card__pages" />
          <div className="book-card__spine" />
          <div className="book-card__face">
            {showCover ? (
              <img
                src={book.cover_url as string}
                alt=""
                loading="lazy"
                onError={() => setBroken(true)}
              />
            ) : (
              // Covers 404 often enough (Open Library gaps) that the fallback
              // needs to look deliberate rather than like a broken image.
              <div className="book-card__placeholder">{book.title}</div>
            )}
          </div>

          {badge && <span className="book-card__badge">{badge}</span>}

          {onAdd && (
            <button
              type="button"
              className="add-to-library-btn"
              title="Add to your library"
              aria-label={`Add ${book.title} to your library`}
              onClick={(event) => {
                event.stopPropagation();
                onAdd(book);
              }}
            >
              +
            </button>
          )}
        </div>

        <div className="book-info">
          <div className="book-title">{book.title}</div>
          <div className="book-authors">{book.author}</div>
          <div className="book-year">
            {book.publication_year ?? '—'}
            {book.average_rating != null && (
              <> · ★ {book.average_rating.toFixed(1)}</>
            )}
          </div>
        </div>
      </div>

      {children}
    </div>
  );
}
