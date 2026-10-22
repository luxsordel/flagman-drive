// Fails on high and critical npm advisories, except the ones listed in .github/npm-audit-allowlist.json.
// Each exception names one advisory and expires on `reviewBy`, so an unfixed issue resurfaces instead of
// being silenced forever. Run from the repository root: `node scripts/npm-audit.mjs`.
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';

const blocking = new Set(['high', 'critical']);
const allowlistPath = new URL('../.github/npm-audit-allowlist.json', import.meta.url);
const allowlist = new Map(
  JSON.parse(readFileSync(allowlistPath, 'utf8')).map((entry) => [entry.id, entry]),
);
const today = new Date().toISOString().slice(0, 10);

// A fixed command string: `shell` is needed on Windows, where npm is a .cmd script.
const audit = spawnSync('npm audit --json', { encoding: 'utf8', shell: true });
let report;
try {
  report = JSON.parse(audit.stdout);
} catch {
  console.error('npm audit did not return JSON:\n', audit.stderr || audit.stdout);
  process.exit(1);
}

const advisories = new Map();
for (const vulnerability of Object.values(report.vulnerabilities ?? {})) {
  for (const source of vulnerability.via) {
    if (typeof source !== 'object' || !blocking.has(source.severity)) continue;
    const id = source.url.split('/').pop();
    advisories.set(id, {
      id,
      package: source.name,
      severity: source.severity,
      title: source.title,
    });
  }
}

let failed = false;
for (const advisory of advisories.values()) {
  const entry = allowlist.get(advisory.id);
  const line = `${advisory.severity} ${advisory.package} ${advisory.id}: ${advisory.title}`;
  if (entry === undefined || entry.package !== advisory.package) {
    console.error(`FAIL  ${line}`);
    failed = true;
  } else if (entry.reviewBy < today) {
    console.error(
      `FAIL  ${line}\n      allowlist entry expired on ${entry.reviewBy}: re-check and extend or remove it`,
    );
    failed = true;
  } else {
    console.log(`allow ${line} (until ${entry.reviewBy})`);
  }
}

for (const id of allowlist.keys()) {
  if (!advisories.has(id))
    console.warn(`note  ${id} is no longer reported: remove it from the allowlist`);
}

if (failed) process.exit(1);
console.log('npm audit: no unapproved high or critical advisories');
