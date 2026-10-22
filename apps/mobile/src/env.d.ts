// Expo inlines EXPO_PUBLIC_* variables into the bundle at build time; the app's tsconfig leaves
// out @types/node, so the ones the app reads are declared here.
declare namespace NodeJS {
  interface ProcessEnv {
    readonly EXPO_PUBLIC_API_URL?: string;
  }
  interface Process {
    readonly env: ProcessEnv;
  }
}
