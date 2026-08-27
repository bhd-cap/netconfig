/// <reference types="vite/client" />

// Typed access to the build-time environment. Without this reference
// `import.meta.env` is untyped and `tsc` fails, which is why the production
// build (tsc && vite build) could not complete.
interface ImportMetaEnv {
  readonly VITE_API_URL?: string;
  readonly VITE_USE_POLLING?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
