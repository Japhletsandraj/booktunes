/**
 * Hosts the book modal once, at the app root.
 *
 * Every page opens books, and the modal itself can open another book (the
 * "similar" tab). Keeping one instance here means that navigation is just a
 * state swap rather than nested modals, and any page can call `openBook`
 * without rendering its own copy.
 */

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import BookModal from './BookModal';
import type { BookSummary } from '../lib/types';

interface BookModalApi {
  openBook: (book: BookSummary) => void;
  closeBook: () => void;
  /** Bumped whenever a library mutation happens, so pages can refetch. */
  libraryVersion: number;
  notifyLibraryChanged: () => void;
}

const BookModalContext = createContext<BookModalApi | null>(null);

export function BookModalProvider({ children }: { children: ReactNode }) {
  const [book, setBook] = useState<BookSummary | null>(null);
  const [libraryVersion, setLibraryVersion] = useState(0);

  const openBook = useCallback((next: BookSummary) => setBook(next), []);
  const closeBook = useCallback(() => setBook(null), []);
  const notifyLibraryChanged = useCallback(
    () => setLibraryVersion((value) => value + 1),
    [],
  );

  const value = useMemo<BookModalApi>(
    () => ({ openBook, closeBook, libraryVersion, notifyLibraryChanged }),
    [openBook, closeBook, libraryVersion, notifyLibraryChanged],
  );

  return (
    <BookModalContext.Provider value={value}>
      {children}
      {book && (
        <BookModal
          book={book}
          onClose={closeBook}
          onOpenBook={setBook}
          onLibraryChange={notifyLibraryChanged}
        />
      )}
    </BookModalContext.Provider>
  );
}

export function useBookModal(): BookModalApi {
  const context = useContext(BookModalContext);
  if (!context) {
    throw new Error('useBookModal must be used inside <BookModalProvider>');
  }
  return context;
}
