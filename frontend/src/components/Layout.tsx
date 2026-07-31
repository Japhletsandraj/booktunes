/** Banner + nav + page slot. Order is banner(1) / nav(2) / content(3). */

import { useEffect, useState } from 'react';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { health } from '../lib/api';
import { useAuth } from '../lib/auth';
import { useToast } from '../lib/toast';
import { Avatar } from './Avatar';

const NAV = [
  { to: '/', label: 'home', end: true },
  { to: '/discover', label: 'discover' },
  { to: '/library', label: 'library' },
  { to: '/playlists', label: 'playlists' },
  { to: '/profile', label: 'profile' },
];

const TICKER =
  '✦ welcome to booktunes ✦ every book has a soundtrack ✦ now playing: your next obsession ✦ best viewed at 1024×768 ✦ ';

function HealthPill() {
  const [state, setState] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    let cancelled = false;
    health()
      .then((body) => {
        if (cancelled) return;
        const degraded = body.degraded ?? [];
        setState({
          ok: body.status !== 'unhealthy',
          text: degraded.length ? `degraded: ${degraded.join(', ')}` : 'all systems go',
        });
      })
      .catch(() => {
        if (!cancelled) setState({ ok: false, text: 'api offline' });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!state) return null;
  return (
    <span className="health-pill" title="Backend dependency status">
      <span className={`health-dot${state.ok ? '' : ' health-dot--bad'}`} />
      {state.text}
    </span>
  );
}

export default function Layout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const toast = useToast();

  const onLogout = async () => {
    await logout();
    toast.info('signed out', 'see you on the next track.');
    navigate('/login', { replace: true });
  };

  return (
    <div className="layout">
      <header className="website-banner">
        <h1 className="banner-title chrome-text">BookTunes</h1>
        <p className="banner-subtitle">read in stereo</p>
        <div className="banner-marquee" aria-hidden="true">
          {/* Doubled so the -50% translate loops seamlessly. */}
          <span>{TICKER.repeat(2)}</span>
        </div>
      </header>

      <nav className="y2k-nav" aria-label="Main">
        <div className="nav-container">
          <div className="radio-nav-group">
            {NAV.map((item) => (
              <div className="radio-nav-item" key={item.to}>
                <NavLink
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) =>
                    `radio-nav-btn${isActive ? ' active' : ''}`
                  }
                >
                  {item.label}
                </NavLink>
              </div>
            ))}
          </div>

          <div className="nav-user">
            <HealthPill />
            {user && (
              <>
                <span className="welcome-msg">hi, {user.username}</span>
                <div className="user-avatar-container">
                  <Avatar
                    user={user}
                    size={80}
                    className="user-avatar"
                    onClick={() => navigate('/profile')}
                  />
                </div>
                <button type="button" className="logout-btn" onClick={onLogout}>
                  log out
                </button>
              </>
            )}
          </div>
        </div>
      </nav>

      <main className="main-content with-navbar">
        <div className="content-box">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
