/** Small shared presentational pieces. */

import type { ReactNode } from 'react';

export function Loader({ label = 'loading' }: { label?: string }) {
  return (
    <div className="loading-container">
      <div className="cd" aria-hidden="true" />
      <div className="loading-spinner">{label}…</div>
    </div>
  );
}

export function InlineLoader({ label = 'loading' }: { label?: string }) {
  return <div className="loading">✦ {label}…</div>;
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <div className="empty-state">{children}</div>;
}

export function ErrorBox({ children }: { children: ReactNode }) {
  return (
    <div className="error-message" role="alert">
      {children}
    </div>
  );
}

export function SuccessBox({ children }: { children: ReactNode }) {
  return <div className="success-message">{children}</div>;
}

/** Read-only star display. `value` is on the API's 0-5 scale. */
export function Stars({ value }: { value: number | null | undefined }) {
  if (value === null || value === undefined) {
    return <span className="muted">unrated</span>;
  }
  const filled = Math.round(value);
  return (
    <span className="minecraft-rating" title={`${value.toFixed(1)} / 5`}>
      {'★'.repeat(filled)}
      {'☆'.repeat(Math.max(0, 5 - filled))}
    </span>
  );
}

/** Clickable 1-5 star rating. */
export function StarPicker({
  value,
  onChange,
  disabled = false,
}: {
  value: number | null;
  onChange: (next: number) => void;
  disabled?: boolean;
}) {
  return (
    <span className="star-row" role="group" aria-label="Rate this book">
      {[1, 2, 3, 4, 5].map((star) => (
        <button
          key={star}
          type="button"
          disabled={disabled}
          className={`star-btn${value !== null && star <= value ? '' : ' star-btn--off'}`}
          aria-label={`${star} star${star > 1 ? 's' : ''}`}
          onClick={() => onChange(star)}
        >
          ★
        </button>
      ))}
    </span>
  );
}

/** Holographic 0-100 progress meter. */
export function Meter({ value }: { value: number }) {
  const clamped = Math.max(0, Math.min(100, value));
  return (
    <div
      className="meter"
      role="meter"
      aria-valuenow={Math.round(clamped)}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div className="meter__fill" style={{ width: `${clamped}%` }} />
    </div>
  );
}

export function CardSkeletons({ count = 8 }: { count?: number }) {
  return (
    <div className="books-grid">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="skeleton skeleton--card" />
      ))}
    </div>
  );
}
