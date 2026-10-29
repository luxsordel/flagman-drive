// The stand build guard in app.config.ts (#123): it runs when the config is imported.
const loadConfig = (env: Record<string, string>): void => {
  const saved: Partial<Record<string, string>> = { ...process.env };
  Object.assign(process.env, env);
  try {
    jest.isolateModules(() => {
      jest.requireActual('../app.config');
    });
  } finally {
    for (const name of Object.keys(env)) {
      const value = saved[name];
      if (value === undefined) Reflect.deleteProperty(process.env, name);
      else Object.assign(process.env, { [name]: value });
    }
  }
};

describe('app.config for the stand build', () => {
  // Empty: the variable is left out of the build profile.
  it.each(['', 'http://api.stand.example', 'http://localhost:8000'])(
    'refuses API address "%s"',
    (url) => {
      expect(() => {
        loadConfig({ EAS_BUILD_PROFILE: 'stand', EXPO_PUBLIC_API_URL: url });
      }).toThrow('EXPO_PUBLIC_API_URL');
    },
  );

  it('accepts an https API address', () => {
    expect(() => {
      loadConfig({ EAS_BUILD_PROFILE: 'stand', EXPO_PUBLIC_API_URL: 'https://api.stand.example' });
    }).not.toThrow();
  });

  it('leaves the other builds alone', () => {
    expect(() => {
      loadConfig({ EAS_BUILD_PROFILE: 'development', EXPO_PUBLIC_API_URL: '' });
    }).not.toThrow();
  });
});
