// Runs the stand's Caddyfile in the Caddy image the stand is built from, with a stub in place of
// the API, and checks what reaches the logs and the response headers. Docker must be running.
// Run from anywhere: `node scripts/check-caddy.mjs`.
//
// STAND_DOMAIN=localhost makes every site a *.localhost name, which Caddy serves with its own local
// CA instead of ACME, so the real config runs unchanged and nothing reaches Let's Encrypt.
// Docker is spawned directly with argument arrays: no shell, so it behaves the same from PowerShell
// and Git Bash, and Git Bash cannot rewrite the container paths.
import { spawnSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { request } from 'node:https';
import { setTimeout as sleep } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';

const root = new URL('..', import.meta.url);
const caddyfile = fileURLToPath(new URL('infra/stand/web/Caddyfile', root));
const dockerfile = readFileSync(new URL('infra/stand/web/Dockerfile', root), 'utf8');
const image = /^FROM (caddy:\S+)/m.exec(dockerfile)?.[1];
if (image === undefined) fail('No `FROM caddy:<tag>` line in infra/stand/web/Dockerfile');

const name = `check-caddy-${process.pid}`;
const edge = `${name}-edge`;
const upstream = `${name}-upstream`;
// Values of the parameters the stand really sees: a client search, a card binding session and the
// signature of a link to a document photo. Any trace of the marker in a log line is a leak.
const marker = `leak${randomBytes(6).toString('hex')}`;
const query = ['search', 'session', 'X-Amz-Signature', 'code', 'token', 'cursor']
  .map((key) => `${key}=${marker}`)
  .join('&');

// The api host and the admin API reach the stub; s3 has no upstream at all, so its 502 covers the
// error path, and admin and app have no built files, so their 404s cover the file server. Caddy
// writes those error responses itself, and they must carry the same headers as any other answer.
const pageCsp = "frame-ancestors 'none'";
const cases = [
  { host: 'api.localhost', path: '/rentals/current', status: 200 },
  { host: 'admin.localhost', path: '/admin/clients', status: 200 },
  { host: 'admin.localhost', path: '/clients', status: 404, csp: pageCsp },
  // s3 serves only re-encoded photos, which need nothing from a CSP: it denies everything.
  {
    host: 's3.localhost',
    path: '/documents/photo.jpg',
    status: 502,
    csp: "default-src 'none'; frame-ancestors 'none'",
    exactCsp: true,
  },
  { host: 'app.localhost', path: '/cards/bind', status: 404, csp: pageCsp },
];

function docker(args, { check = true } = {}) {
  const result = spawnSync('docker', args, { encoding: 'utf8' });
  if (result.error !== undefined) fail(`Cannot run docker: ${result.error.message}`);
  if (check && result.status !== 0) {
    fail(`docker ${args.join(' ')} failed:\n${result.stderr}`);
  }
  return result;
}

function fail(message) {
  console.error(`FAIL  ${message}`);
  process.exitCode = 1;
  throw new Error(message);
}

function get(port, ca, host, path) {
  return new Promise((resolve, reject) => {
    const req = request(
      { host: '127.0.0.1', port, path, servername: host, headers: { host }, ca, timeout: 5000 },
      (res) => {
        res.resume();
        res.on('end', () => resolve(res));
      },
    );
    req.on('timeout', () => req.destroy(new Error('timeout')));
    req.on('error', reject);
    req.end();
  });
}

async function run() {
  docker(['network', 'create', name]);
  docker([
    'run',
    '--detach',
    '--name',
    upstream,
    '--network',
    name,
    '--network-alias',
    'api',
    image,
    'caddy',
    'respond',
    '--listen',
    ':8000',
    // The edge must drop this header (-Server in the Caddyfile), even when the API sends one.
    '--header',
    'Server: upstream',
    // The API sets these itself (core/middleware.py): the edge must replace them, not add a copy.
    '--header',
    'Strict-Transport-Security: max-age=63072000; includeSubDomains',
    '--header',
    'X-Content-Type-Options: nosniff',
    '--header',
    'Referrer-Policy: no-referrer',
    'ok',
  ]);
  docker([
    'run',
    '--detach',
    '--name',
    edge,
    '--network',
    name,
    '--publish',
    '127.0.0.1::443',
    '--env',
    'STAND_DOMAIN=localhost',
    '--env',
    'ACME_EMAIL=check@example.invalid',
    '--mount',
    `type=bind,source=${caddyfile},target=/etc/caddy/Caddyfile,readonly`,
    image,
  ]);
  const port = Number(docker(['port', edge, '443/tcp']).stdout.trim().split(':').pop());

  // The local CA and the certificates appear a moment after start.
  let ca;
  for (let attempt = 0; ca === undefined; attempt += 1) {
    if (attempt === 60) fail(`Caddy did not start:\n${docker(['logs', edge]).stderr}`);
    await sleep(500);
    const result = docker(['exec', edge, 'cat', '/data/caddy/pki/authorities/local/root.crt'], {
      check: false,
    });
    if (result.status !== 0) continue;
    try {
      await get(port, result.stdout, cases[0].host, cases[0].path);
      ca = result.stdout;
    } catch {
      // The certificate for the site is not issued yet.
    }
  }

  const problems = [];
  const expected = {
    'strict-transport-security': 'max-age=31536000; includeSubDomains',
    'x-content-type-options': 'nosniff',
    'referrer-policy': 'no-referrer',
  };
  for (const { host, path, status, csp, exactCsp } of cases) {
    const res = await get(port, ca, host, `${path}?${query}`);
    const where = `${host}${path}`;
    if (res.statusCode !== status) problems.push(`${where}: status ${res.statusCode}`);
    for (const [header, value] of Object.entries(expected)) {
      if (res.headers[header] !== value) {
        problems.push(`${where}: ${header} is ${res.headers[header]}`);
      }
    }
    if (res.headers.server !== undefined) {
      problems.push(`${where}: Server header ${res.headers.server}`);
    }
    if (csp !== undefined) {
      const actual = res.headers['content-security-policy'];
      const matches = exactCsp ? actual === csp : actual?.includes(csp) === true;
      if (!matches) problems.push(`${where}: content-security-policy is ${actual}`);
    }
  }

  // Caddy logs to stderr; a short pause lets the last access line arrive.
  await sleep(500);
  const logs = docker(['logs', edge]);
  const lines = `${logs.stdout}\n${logs.stderr}`.split('\n').filter((line) => line.trim() !== '');
  for (const line of lines) {
    if (line.includes(marker)) problems.push(`query value in the log: ${line}`);
  }

  const entries = lines
    .filter((line) => line.startsWith('{'))
    .map((line) => JSON.parse(line))
    .filter((entry) => entry.request !== undefined);
  for (const host of new Set(cases.map((item) => item.host))) {
    if (!entries.some((e) => e.request.host === host && e.logger.startsWith('http.log.access'))) {
      problems.push(`${host}: no access log line`);
    }
  }
  // Caddy logs an error its error route handled at debug level only, so the route puts the cause
  // on the access line (log_append); without it a dead s3 upstream would leave no trace.
  const s3Access = entries.find(
    (e) => e.request.host === 's3.localhost' && e.logger.startsWith('http.log.access'),
  );
  if (typeof s3Access?.error !== 'string' || s3Access.error === '') {
    problems.push('s3.localhost: the access line has no error cause');
  }
  for (const { logger, request: logged, resp_headers: respHeaders } of entries) {
    const where = `${logger} ${logged.host}`;
    // The path stays: it is what the logs are for.
    const paths = cases.filter((item) => item.host === logged.host).map((item) => item.path);
    if (!paths.includes(logged.uri)) problems.push(`${where}: logged uri ${logged.uri}`);
    for (const field of ['remote_ip', 'client_ip', 'headers']) {
      if (field in logged) problems.push(`${where}: request>${field} is logged`);
    }
    if (respHeaders !== undefined) problems.push(`${where}: resp_headers are logged`);
  }

  if (problems.length > 0) fail(`${problems.join('\n      ')}`);
  console.log(
    `ok    ${image}: ${cases.length} requests, headers on errors too, paths logged, query strings dropped`,
  );
}

try {
  await run();
} catch (error) {
  if (process.exitCode !== 1) {
    console.error(`FAIL  ${error.message}`);
    process.exitCode = 1;
  }
} finally {
  docker(['rm', '--force', edge, upstream], { check: false });
  docker(['network', 'rm', name], { check: false });
}
