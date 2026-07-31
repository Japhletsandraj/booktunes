/** Y2K popup notifications — little OS dialogs that stack in the corner. */

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';

type ToastKind = 'ok' | 'err' | 'info';

interface Toast {
  id: number;
  kind: ToastKind;
  title: string;
  body?: string;
}

interface ToastApi {
  ok: (title: string, body?: string) => void;
  err: (title: string, body?: string) => void;
  info: (title: string, body?: string) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

const TITLES: Record<ToastKind, string> = {
  ok: '✓ done',
  err: '✕ error',
  info: 'ⓘ notice',
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const push = useCallback((kind: ToastKind, title: string, body?: string) => {
    const id = nextId.current++;
    setItems((current) => [...current, { id, kind, title, body }]);
    window.setTimeout(
      () => setItems((current) => current.filter((t) => t.id !== id)),
      kind === 'err' ? 6500 : 4000,
    );
  }, []);

  const dismiss = useCallback((id: number) => {
    setItems((current) => current.filter((t) => t.id !== id));
  }, []);

  const api = useMemo<ToastApi>(
    () => ({
      ok: (title, body) => push('ok', title, body),
      err: (title, body) => push('err', title, body),
      info: (title, body) => push('info', title, body),
    }),
    [push],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toast-stack" role="status" aria-live="polite">
        {items.map((toast) => (
          <div key={toast.id} className={`toast toast--${toast.kind}`}>
            <div className="toast__bar">
              <span>{TITLES[toast.kind]}</span>
              <button
                type="button"
                className="toast__close"
                onClick={() => dismiss(toast.id)}
                aria-label="Dismiss"
              >
                ×
              </button>
            </div>
            <div className="toast__body">
              <strong>{toast.title}</strong>
              {toast.body && <p>{toast.body}</p>}
            </div>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const context = useContext(ToastContext);
  if (!context) throw new Error('useToast must be used inside <ToastProvider>');
  return context;
}
