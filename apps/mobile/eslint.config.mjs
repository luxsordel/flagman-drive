import js from '@eslint/js';
import expo from 'eslint-plugin-expo';
import globals from 'globals';
import reactHooks from 'eslint-plugin-react-hooks';
import tseslint from 'typescript-eslint';

// eslint-config-expo is not used: it pulls eslint-plugin-react and eslint-plugin-import,
// which do not support ESLint 10 yet.
export default tseslint.config(
  { ignores: ['node_modules', '.expo', 'dist', 'web-build', 'expo-env.d.ts', 'public'] },
  js.configs.recommended,
  ...tseslint.configs.strictTypeChecked,
  reactHooks.configs.flat['recommended-latest'],
  {
    languageOptions: {
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
    plugins: { expo },
    rules: {
      '@typescript-eslint/no-explicit-any': 'error',
      'expo/no-dynamic-env-var': 'error',
      'expo/no-env-var-destructuring': 'error',
    },
  },
  { files: ['*.config.mjs'], ...tseslint.configs.disableTypeChecked },
  {
    files: ['scripts/*.mjs'],
    ...tseslint.configs.disableTypeChecked,
    languageOptions: { globals: globals.node, parserOptions: { projectService: false } },
  },
);
