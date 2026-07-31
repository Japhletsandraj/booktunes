/**
 * Session state.
 *
 * The quiz gate lives here because it is a property of the session, not of any
 * one page: `preferences.favorite_genres` is what `POST /auth/preferences`
 * writes, so its presence is the signal that cold-start onboarding is done.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { auth as authApi, onSessionExpired, tokens } from './api';
import type { PreferenceQuizPayload, ReadingLevel, User } from './types';

interface AuthState {
  user: User | null;
  loading: boolean;
  /** False until the preference quiz has been answered at least once. */
  hasCompletedQuiz: boolean;
  login: (identifier: string, password: string) => Promise<void>;
  register: (payload: {
    username: string;
    email: string;
    password: string;
    full_name?: string | null;
    reading_level?: ReadingLevel | null;
  }) => Promise<void>;
  logout: () => Promise<void>;
  saveQuiz: (payload: PreferenceQuizPayload) => Promise<void>;
  setUser: (user: User) => void;
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

function quizDone(user: User | null): boolean {
  if (!user) return false;
  const genres = user.preferences?.favorite_genres;
  return Array.isArray(genres) && genres.length > 0;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  // Restore the session on boot when a token is already in storage.
  useEffect(() => {
    let cancelled = false;

    (async () => {
      if (!tokens.access) {
        setLoading(false);
        return;
      }
      try {
        const me = await authApi.me();
        if (!cancelled) setUser(me);
      } catch {
        // An unusable token is worse than none — clear it so the app shows
        // the login screen rather than looping on 401s.
        tokens.clear();
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  // A failed refresh means the session is genuinely gone.
  useEffect(() => onSessionExpired(() => setUser(null)), []);

  const login = useCallback(async (identifier: string, password: string) => {
    tokens.set(await authApi.login(identifier, password));
    setUser(await authApi.me());
  }, []);

  const register = useCallback<AuthState['register']>(async (payload) => {
    tokens.set(await authApi.register(payload));
    setUser(await authApi.me());
  }, []);

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } catch {
      // Revocation is best-effort: Redis may be down, and the local session
      // should end regardless.
    }
    tokens.clear();
    setUser(null);
  }, []);

  const saveQuiz = useCallback(async (payload: PreferenceQuizPayload) => {
    setUser(await authApi.savePreferences(payload));
  }, []);

  const refreshUser = useCallback(async () => {
    setUser(await authApi.me());
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      user,
      loading,
      hasCompletedQuiz: quizDone(user),
      login,
      register,
      logout,
      saveQuiz,
      setUser,
      refreshUser,
    }),
    [user, loading, login, register, logout, saveQuiz, refreshUser],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used inside <AuthProvider>');
  return context;
}
