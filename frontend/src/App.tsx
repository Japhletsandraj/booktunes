import { Suspense, lazy } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { AuthProvider, useAuth } from './lib/auth';
import { ToastProvider } from './lib/toast';
import { BookModalProvider } from './components/BookModalProvider';
import Stage3D from './three/Stage3D';
import Layout from './components/Layout';
import { Loader } from './components/ui';

import Login from './pages/Login';
import Register from './pages/Register';
import Quiz from './pages/Quiz';
import Home from './pages/Home';
import Discover from './pages/Discover';
import Library from './pages/Library';
import Playlists from './pages/Playlists';
import Profile from './pages/Profile';
import EditProfile from './pages/EditProfile';

// Split out: the studio is the only place that needs every avatar style, and
// it is visited rarely compared to the pages in the main bundle.
const AvatarStudio = lazy(() => import('./pages/AvatarStudio'));

/**
 * Gate for signed-in routes.
 *
 * Two redirects, in order: no session -> /login (remembering where you were
 * headed), and a session that has never answered the quiz -> /quiz. The quiz
 * seeds the preference vector, so skipping it leaves the recommender with
 * nothing but popularity to work with.
 */
function Protected({
  children,
  requireQuiz = true,
}: {
  children: JSX.Element;
  requireQuiz?: boolean;
}) {
  const { user, loading, hasCompletedQuiz } = useAuth();
  const location = useLocation();

  if (loading) return <Loader label="waking up booktunes" />;

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  if (requireQuiz && !hasCompletedQuiz) {
    return <Navigate to="/quiz" replace />;
  }

  return children;
}

/** Keeps signed-in users off /login and /register. */
function PublicOnly({ children }: { children: JSX.Element }) {
  const { user, loading } = useAuth();
  if (loading) return <Loader label="waking up booktunes" />;
  return user ? <Navigate to="/" replace /> : children;
}

function Router() {
  return (
    <Routes>
      <Route
        path="/login"
        element={
          <PublicOnly>
            <Login />
          </PublicOnly>
        }
      />
      <Route
        path="/register"
        element={
          <PublicOnly>
            <Register />
          </PublicOnly>
        }
      />

      {/* The quiz is itself behind auth, but must not require its own gate. */}
      <Route
        path="/quiz"
        element={
          <Protected requireQuiz={false}>
            <Quiz />
          </Protected>
        }
      />

      {/*
        Layout itself is public now — a first-time visitor lands on the home
        page and sees the catalogue, rather than a sign-in wall for a product
        they have not been shown yet. Auth moves down to the individual routes
        that genuinely need a user, so browsing stays open and only personal
        shelves are gated.
      */}
      <Route element={<Layout />}>
        <Route path="/" element={<Home />} />
        <Route path="/discover" element={<Discover />} />
        <Route
          path="/library"
          element={
            <Protected>
              <Library />
            </Protected>
          }
        />
        <Route
          path="/playlists"
          element={
            <Protected>
              <Playlists />
            </Protected>
          }
        />
        <Route
          path="/profile"
          element={
            <Protected>
              <Profile />
            </Protected>
          }
        />
        <Route
          path="/profile/edit"
          element={
            <Protected>
              <EditProfile />
            </Protected>
          }
        />
        <Route
          path="/profile/avatar"
          element={
            <Protected>
              <Suspense fallback={<Loader label="opening the avatar studio" />}>
                <AvatarStudio />
              </Suspense>
            </Protected>
          }
        />
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <>
      <Stage3D />
      <ToastProvider>
        <AuthProvider>
          <BookModalProvider>
            <Router />
          </BookModalProvider>
        </AuthProvider>
      </ToastProvider>
    </>
  );
}
