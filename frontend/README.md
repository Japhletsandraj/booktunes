# BookTunes — frontend

A 3D Y2K interface for the BookTunes API. React + TypeScript + Vite, with a
Three.js scene behind the whole app.

## Running it

The API must be up first (see `../backend/README.md`):

```bash
# from the repo root — Postgres (pgvector) + Redis
cd backend && docker compose up -d db redis

# migrations, then the API on :8000
DATABASE_URL=postgresql://booktunes:booktunes@localhost:5432/booktunes \
REDIS_URL=redis://localhost:6379/0 \
  python -m alembic upgrade head

DATABASE_URL=postgresql://booktunes:booktunes@localhost:5432/booktunes \
REDIS_URL=redis://localhost:6379/0 ENVIRONMENT=development \
  python -m uvicorn app.main:app --port 8000
```

Then:

```bash
cd frontend
npm install
npm run dev     # http://127.0.0.1:5173
```

`npm run dev` proxies `/api` and `/health` to `http://127.0.0.1:8000`, so the
browser sees one origin and CORS never comes into it during development.

> Both the dev server and the proxy target are pinned to `127.0.0.1` rather
> than `localhost`. Node 17+ resolves `localhost` to `::1` first, which makes
> Vite listen on IPv6 only — Chrome then fails to connect to a server `curl`
> can reach.

An empty catalogue makes for a dull home page. Seed some books:

```bash
cd backend
python -m scripts.seed_books --genres fantasy,mystery,science_fiction --limit 40
python -m scripts.rebuild_vector_index   # needed for semantic search + recs
```

## Scripts

| command | what it does |
| --- | --- |
| `npm run dev` | dev server with HMR and the API proxy |
| `npm run build` | typecheck (`tsc -b`) then bundle to `dist/` |
| `npm run preview` | serve the built bundle |
| `npm run lint` | typecheck only |

## Configuration

| variable | default | notes |
| --- | --- | --- |
| `VITE_API_BASE` | `/api/v1` | Set to an absolute URL (`https://host/api/v1`) when the API is on another origin. That origin must then appear in the API's `CORS_ORIGINS`. |
| `VITE_API_PROXY` | `http://127.0.0.1:8000` | Dev-proxy target only. |

## Layout

```
src/
  lib/       api client, auth context, toasts, formatting
  three/     Stage3D — the WebGL background
  components/ Layout, BookCard, BookModal, shared UI
  pages/     Login, Register, Quiz, Home, Discover, Library, Playlists, Profile
  styles/    y2k.css — the whole design system
```

### The API client

`src/lib/api.ts` wraps every endpoint. Two behaviours worth knowing:

- **Token refresh is single-flight.** Access tokens last 30 minutes. A 401
  triggers one `/auth/refresh`; concurrent 401s await that same promise instead
  of stampeding it and racing each other's token writes.
- **Errors are typed.** Failures throw `ApiError` carrying the server's `code`
  (`book_not_found`, `playlist_generation_failed`, …), so the UI branches on a
  code rather than matching on prose.

### Design system

`src/styles/y2k.css` is derived from `css_sheets/style.css` and
`css_sheets/style_ls.css`. Every class name and font in those files still
works; the surfaces became dimensional. Fixes carried over, all of which were
breaking layout in the originals:

- `body { display: 80 }` → a real `display: flex`
- `.container` had `min-height: 55vh` *and* `max-height: 80px`, clipping every
  page into an 80px window → the max-height is gone
- `.menu-bar { padding: 15rem 0 }` — a 240px-tall nav bar → `0.9rem`
- several rules sized *width* in `vh` (`min-width: 150vh`) → `min(x, 100%)`
- `.book-card`, `.row-scroll` and the `.minecraft-*` blocks were each declared
  twice with conflicting values → one definition each

Motion is heavy by design, so everything is disabled under
`prefers-reduced-motion: reduce`, including the WebGL loop (it renders one
static frame).

### Notes on API behaviour the UI has to respect

- **Genres and moods are snake_case slugs** (`science_fiction`, not "science
  fiction"). They come from `/books/genres` and `/books/moods` — the server's
  canonical taxonomy — and are only prettified for display. A slug this UI
  invented would silently match nothing.
- **Playlists generate on demand.** `GET /playlists/book/{id}` fans out to
  several upstream music searches when no playlist exists, so it is only
  requested when the *tunes* tab is actually opened.
- **`preview_url` is null for every YouTube Music track** — that source has no
  preview-clip concept. The player treats `external_url` as the primary action
  and inline audio as a bonus, rather than showing a dead play button.
- **Semantic search needs embeddings.** Without them the server falls back to
  keyword search, so the empty state says so instead of just "no results".
