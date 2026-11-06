// Fails on high and critical npm advisories, except the ones listed in .github/npm-audit-allowlist.json.
// Each exception names one advisory and expires on `reviewBy`, so an unfixed issue resurfaces instead of
// being silenced forever. Run from the repository root: `npm run audit`.
//
// The report comes on stdin (`npm audit --json | node scripts/npm-audit.mjs`) instead of a child
// process: on Windows npm is a .cmd script that cannot be spawned without a shell.
import { readFileSync } from 'node:fs';

const blocking = new Set(['high', 'critical']);
// docs/03-security.md: an exception lives at most three months before someone has to look at it again.
const maxReviewDays = 92;
const dayMs = 24 * 60 * 60 * 1000;
const today = new Date().toISOString().slice(0, 10);
const latestReviewBy = new Date(Date.parse(today) + maxReviewDays * dayMs)
  .toISOString()
  .slice(0, 10);

// A malformed entry fails even while its advisory is not reported: a missing reason or a far reviewBy
// would otherwise only surface on the day the exception starts silencing a real advisory.
function loadAllowlist(path) {
  const entries = JSON.parse(readFileSync(path, 'utf8'));
  if (!Array.isArray(entries)) {
    console.error('FAIL  .github/npm-audit-allowlist.json must be a JSON array of exceptions');
    process.exit(1);
  }
  const errors = [];
  const allowlist = new Map();
  entries.forEach((entry, index) => {
    const where = `allowlist entry ${index + 1}`;
    if (typeof entry !== 'object' || entry === null || Array.isArray(entry)) {
      errors.push(`${where} must be an object`);
      return;
    }
    const { id, package: name, reason, reviewBy } = entry;
    const label = typeof id === 'string' ? `${where} (${id})` : where;
    if (typeof id !== 'string' || !/^GHSA(-[0-9a-z]{4}){3}$/.test(id)) {
      errors.push(`${label}: id must be a GitHub advisory id like GHSA-xxxx-xxxx-xxxx`);
    } else if (allowlist.has(id)) {
      errors.push(`${label}: advisory is listed twice`);
    }
    if (typeof name !== 'string' || name.trim() === '') {
      errors.push(`${label}: package must name the vulnerable npm package`);
    }
    if (typeof reason !== 'string' || reason.trim() === '') {
      errors.push(`${label}: reason must say why the advisory does not affect us`);
    }
    // The round trip rejects dates like 2026-02-30 that Date.parse would roll over into March.
    const isDate =
      typeof reviewBy === 'string' &&
      /^\d{4}-\d{2}-\d{2}$/.test(reviewBy) &&
      !Number.isNaN(Date.parse(reviewBy)) &&
      new Date(Date.parse(reviewBy)).toISOString().slice(0, 10) === reviewBy;
    if (!isDate) {
      errors.push(`${label}: reviewBy must be a date in YYYY-MM-DD form`);
    } else if (reviewBy > latestReviewBy) {
      errors.push(
        `${label}: reviewBy must be at most ${maxReviewDays} days ahead (${latestReviewBy}), got ${reviewBy}`,
      );
    }
    if (typeof id === 'string') allowlist.set(id, entry);
  });
  if (errors.length > 0) {
    for (const error of errors) console.error(`FAIL  ${error}`);
    process.exit(1);
  }
  return allowlist;
}

const allowlist = loadAllowlist(new URL('../.github/npm-audit-allowlist.json', import.meta.url));

let input = '';
for await (const chunk of process.stdin) input += chunk;
let report;
try {
  report = JSON.parse(input);
} catch {
  console.error('Expected `npm audit --json` output on stdin, got:\n', input.slice(0, 2000));
  process.exit(1);
}

// A failed npm audit still prints valid JSON, e.g. {"message": ..., "error": {...}} when the registry
// is unreachable. It lists no vulnerabilities, so without this check an audit that never ran would pass.
const totals = report?.metadata?.vulnerabilities;
if (
  report?.error !== undefined ||
  typeof report?.vulnerabilities !== 'object' ||
  report.vulnerabilities === null ||
  typeof totals !== 'object' ||
  totals === null
) {
  console.error('npm audit output is not an npm audit report:\n', input.slice(0, 2000));
  process.exit(1);
}

const advisories = new Map();
for (const vulnerability of Object.values(report.vulnerabilities)) {
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
// The totals and the advisories come from the same report; if they disagree, the report format has
// changed under this parser, and trusting the empty list would hide the vulnerable packages.
if ((totals.high ?? 0) + (totals.critical ?? 0) > 0 && advisories.size === 0) {
  console.error(
    `FAIL  npm audit counts ${totals.high ?? 0} high and ${totals.critical ?? 0} critical packages, ` +
      'but none could be read from the report',
  );
  failed = true;
}
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

// A stale exception would silently allow the advisory again if it ever came back, so it fails too.
for (const id of allowlist.keys()) {
  if (!advisories.has(id)) {
    console.error(`FAIL  ${id} is no longer reported: remove it from the allowlist`);
    failed = true;
  }
}

if (failed) process.exit(1);
console.log('npm audit: no unapproved high or critical advisories');
