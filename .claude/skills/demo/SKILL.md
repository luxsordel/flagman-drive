---
name: demo
description: Build and launch the current version of the apps (admin panel, client app on web or phone, optionally the API with the car emulator) and open them so the user can click through. Use when the user says "/demo", "покажи приложение", "запусти демо", "хочу потыкать", "show me the app".
---

# Demo the current version

Talk to the user in Russian (see «Язык» in CLAUDE.md), even though this skill is written in English.

Shows the code of the current checkout (this branch with its uncommitted changes). If the user wants
the version from `main`, say so and suggest running `/demo` in a session on `main` instead of switching
branches here.

Argument: what to show — `admin`, `mobile`, `phone`, `api`, `all`. Without one: `admin` + `mobile`
(both work without Docker and without a backend). The app configurations are in `.claude/launch.json`.

1. Dependencies. If `node_modules` is missing, or `package-lock.json` is newer than
   `node_modules/.package-lock.json`, run `npm ci` from the repo root (about a minute; tell the user).
2. Launch.
   - Desktop app (the `preview_start` tool is available): `preview_start` with `admin` and/or `mobile`.
     Show the client app at phone size: `resize_window` with `preset: "mobile"` on its tab.
     The first Metro bundle takes 10–30 s; if the page is blank, wait and check `preview_logs`.
   - Terminal CLI (no browser pane): start the same commands from `.claude/launch.json` with
     `run_in_background` and give the user the URLs: admin http://127.0.0.1:5173, client http://localhost:8081.
   - If a port is taken, it is probably an earlier demo or a dev server the user runs: ask before stopping it.
3. `phone` — the client app in Expo Go on a real phone. The QR code is printed only in an interactive
   terminal, so stop the `mobile` preview (port 8081) and run `npm run start --workspace @flagman/mobile`
   in a Terminal panel tab (`run_in_terminal`; in the CLI — ask the user to run it). Tell the user: Expo Go
   on Android scans the QR code, on iOS — the camera; phone and computer on the same network, otherwise
   `npx expo start --tunnel` from `apps/mobile`.
4. `api` — backend + car emulator in Docker. Only if Docker is running (`docker info`) and an env file exists:
   `.env` in this checkout or, in a worktree, `.env` of the main checkout (the first path in
   `git worktree list`). Check existence only with `test -f`; never read, print or copy `.env`.
   - `docker compose --env-file <env> -f infra/docker-compose.yml up -d --build`
   - `docker compose --env-file <env> -f infra/docker-compose.yml run --rm api alembic upgrade head`
   - open Swagger http://127.0.0.1:8000/docs and the emulator health http://127.0.0.1:8100/health.
     The Compose project is shared by all checkouts: this replaces containers started from another branch —
     warn the user first if `docker compose -f infra/docker-compose.yml ps` shows them running.
     No Docker or no `.env` — say what is missing and show the rest.
5. Check that it works before handing over: screenshot each page, look at the console
   (`read_console_messages` with `onlyErrors`) and server logs. A build or runtime error is a finding:
   show it to the user, do not fix it as part of the demo.
6. Give a short tour, in Russian:
   - what is on the screen now — read the routes (`apps/admin/src/routes.tsx`, `apps/admin/src/navigation.ts`,
     `apps/mobile/src/app/`) rather than guessing;
   - what changed recently: `git log --oneline -10` (on a task branch also `git log --oneline origin/main..HEAD`);
   - 3–5 concrete things to try (e.g. "введите номер +375 29 …", "переключите язык и тему");
   - what is not there yet and is still a stub (sections without content, no requests to the API, no SMS).
7. Leave the servers running while the user clicks around. When they say they are done ("хватит",
   "останови демо"): `preview_stop` for each server (or stop the background tasks), and only for `api` —
   `docker compose --env-file <env> -f infra/docker-compose.yml stop` (never `down -v`: it deletes the data).
