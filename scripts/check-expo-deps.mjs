// Checks the Expo packages of apps/mobile against the set the installed SDK expects (ADR-0008).
// `expo install --check` fails on every patch Expo publishes, which turned main and every PR red
// for a release nobody here made. A package off by a patch still belongs to the SDK, so a PR
// fails only on a minor or major mismatch; `--strict` fails on patches too, for the weekly run.
// Run from apps/mobile: `npm run check:deps` or `npm run check:deps:strict`.
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { stripVTControlCharacters } from 'node:util';

const strict = process.argv.includes('--strict');

function runExpoCheck() {
  const require = createRequire(import.meta.url);
  const cli = require.resolve('expo/bin/cli', { paths: [process.cwd()] });
  // Run the JS entry point directly on Windows too. Dotenv logs would otherwise precede the JSON.
  return spawnSync(process.execPath, [cli, 'install', '--check', '--json'], {
    encoding: 'utf8',
    env: { ...process.env, EXPO_NO_DOTENV: '1' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
}

function readReport(text) {
  try {
    const report = JSON.parse(text);
    const readable =
      Array.isArray(report?.dependencies) &&
      report.upToDate === (report.dependencies.length === 0) &&
      report.dependencies.every(
        (dep) =>
          typeof dep?.packageName === 'string' &&
          dep.packageName.trim().length > 0 &&
          typeof dep.actualVersion === 'string' &&
          typeof dep.expectedVersionOrRange === 'string',
      );
    return readable ? report : null;
  } catch {
    return null;
  }
}

// Only accept a single SemVer base, optionally preceded by one supported range operator.
function baseVersion(spec, isRange = false) {
  const version = spec.trim();
  const base = isRange ? version.replace(/^(?:[~^]|>=)\s*/, '') : version;
  const match =
    /^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/.exec(
      base,
    );
  if (!match) return null;
  const [, major, minor, patch, prerelease] = match;
  if (prerelease?.split('.').some((id) => /^0\d+$/.test(id))) return null;
  return { major, minor, patch, prerelease };
}

const expo = process.argv.includes('--stdin') ? null : runExpoCheck();
if (expo?.stderr) process.stderr.write(expo.stderr);
const stdout = expo ? expo.stdout : readFileSync(0, 'utf8');
// Expo prints this line to stdout when it disables networking, even in JSON mode.
const newline = stdout?.indexOf('\n') ?? -1;
const autoOffline =
  newline >= 0 &&
  stripVTControlCharacters(stdout.slice(0, newline)).trimEnd() === 'Networking has been disabled';
const report = readReport(autoOffline ? stdout.slice(newline + 1) : stdout);
// Expo exits with 1 after printing a valid report of mismatches. Other exits are CLI failures.
if (report === null || (expo && expo.status !== (report.upToDate ? 0 : 1))) {
  console.error('FAIL  `expo install --check --json` gave no report it could read');
  process.exit(1);
}

// Expo can produce a valid report from its bundled map without checking online recommendations.
if (
  strict &&
  (autoOffline ||
    /Dependency validation is unreliable in offline-mode|Unable to reach well-known versions endpoint|Dependency validation might be unreliable when using canary SDK versions/.test(
      expo?.stderr ?? '',
    ))
) {
  console.error('INCONCLUSIVE  Expo SDK recommendations were not checked online');
  process.exit(2);
}

const blocking = [];
const patches = [];
for (const dep of report.dependencies) {
  const line = `${dep.packageName}@${dep.actualVersion} - expected ${dep.expectedVersionOrRange}`;
  const actual = baseVersion(dep.actualVersion);
  const expected = baseVersion(dep.expectedVersionOrRange, true);
  const patchMismatch =
    actual &&
    expected &&
    !actual.prerelease &&
    !expected.prerelease &&
    actual.major === expected.major &&
    actual.minor === expected.minor &&
    actual.patch !== expected.patch;
  (patchMismatch && !strict ? patches : blocking).push(line);
}

for (const line of patches) {
  console.warn(`WARN  ${line} (off the SDK by a patch, the weekly strict run reports it)`);
}
for (const line of blocking) {
  console.error(`FAIL  ${line}`);
}
if (blocking.length > 0) {
  console.error('Fix with `npx expo install --fix` in apps/mobile (ADR-0008).');
  process.exit(1);
}
console.log(
  patches.length > 0
    ? `ok    ${patches.length} package(s) off the SDK by a patch, none off its minor version`
    : 'ok    Expo packages match the SDK',
);
