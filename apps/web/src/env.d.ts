/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_GPU_CONTROL_VERSION?: string;
  readonly VITE_GPU_CONTROL_REVISION?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
