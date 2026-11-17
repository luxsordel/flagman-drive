// Checks the Expo packages of apps/mobile against the set the installed SDK expects (ADR-0008).
// `expo install --check` fails on every patch Expo publishes, which turned main and every PR red
// for a release nobody here made. A package off by a patch still belongs to the SDK, so a PR
// fails only on a minor or major mismatch; `--strict` fails on patches too, for the weekly run.
//
// The report comes on stdin (`expo install --check --json | node check-expo-deps.mjs`) instead of
// a child process: on Windows npx is a .cmd script that cannot be spawned without a shell.
// Run from apps/mobile: `npm run check:deps` or `npm run check:deps:strict`.
import { readFileSync } from 'node:fs';

const strict = process.argv.includes('--strict');

// With `--json` the CLI writes only the report to stdout (warnings go to stderr), so the whole
// input must be one report. Anything else fails: an empty pipe is a crashed check, not a pass.
function readReport(text) {
  try {
    const report = JSON.parse(text);
    const readable =
      Array.isArray(report?.dependencies) && report.upToDate === (report.dependencies.length === 0);
    return readable ? report : null;
  } catch {
    return null;
  }
}

// `~58.0.25`, `^1.2.3`, `>=0.83.2` and `19.1.0` all name one base version. `*`, a list
// (`^1.0.0 || ^2.0.0`) or a range with two ends names none, so it is reported as unreadable.
function baseVersion(spec) {
  const match = /^(?:[~^]|>=)?v?(\d+)\.(\d+)\.(\d+)(?:-[\w.]+)?$/.exec(String(spec ?? '').trim());
  return match ? match.slice(1, 4).map(Number) : null;
}

const report = readReport(readFileSync(0, 'utf8'));
if (report === null) {
  console.error('FAIL  `expo install --check --json` gave no report it could read');
  process.exit(1);
}

const blocking = [];
const patches = [];
for (const dep of report.dependencies) {
  const line = `${dep.packageName}@${dep.actualVersion} - expected ${dep.expectedVersionOrRange}`;
  const actual = baseVersion(dep.actualVersion);
  const expected = baseVersion(dep.expectedVersionOrRange);
  const samePatchLine =
    actual !== null && expected !== null && actual[0] === expected[0] && actual[1] === expected[1];
  (samePatchLine && !strict ? patches : blocking).push(line);
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
