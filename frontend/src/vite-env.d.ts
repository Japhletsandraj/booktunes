/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Overrides the API base path. Defaults to `/api/v1` (dev-proxied). */
  readonly VITE_API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
