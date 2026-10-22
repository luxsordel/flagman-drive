// The app has no Node typings (it runs in React Native), so the Node resolver is typed locally.
interface NodeResolver {
  resolve: (id: string, options?: { paths: string[] }) => string;
}
const { resolve } = require as unknown as NodeResolver;

const packageDir = (name: string): string =>
  resolve(`${name}/package.json`).replace(/[\\/]package\.json$/, '');

// npm workspaces can hoist a newer React for packages that peer on `react: "*"` while the app
// keeps the SDK's exact version; two copies break hooks at runtime. Root `overrides` pins one
// copy, and this test fails if a dependency change brings a second one back.
describe('dependency tree', () => {
  it('resolves a single React for the app, the renderers and the test renderer', () => {
    const appReact = resolve('react/package.json');
    for (const consumer of ['react-dom', 'react-native', 'react-reconciler', 'expo-router']) {
      const react = resolve('react/package.json', { paths: [packageDir(consumer)] });
      expect({ consumer, react }).toEqual({ consumer, react: appReact });
    }
  });
});
