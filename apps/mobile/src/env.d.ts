// Expo inlines EXPO_PUBLIC_* variables into the bundle at build time; the app's tsconfig leaves
// out @types/node, so the ones the app reads are declared here.
declare namespace NodeJS {
  interface ProcessEnv {
    readonly EXPO_PUBLIC_API_URL?: string;
    readonly EXPO_PUBLIC_MAP_STYLE_URL?: string;
    // Read by app.config.ts at build time, not by the bundle.
    readonly EAS_PROJECT_ID?: string;
  }
  interface Process {
    readonly env: ProcessEnv;
  }
}

// Metro bundles global CSS on the web only: the web map imports the stylesheet of maplibre-gl.
declare module '*.css';
