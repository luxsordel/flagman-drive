#!/usr/bin/env bash
# Creates the database roles of docs/adr/0031 and hands the schema to its owner:
#   - FLAGMAN_OWNER_USER owns the schema and runs the migrations; no superuser, no CREATEROLE;
#   - flagman_app (no login) is what the migrations grant table rights to;
#   - FLAGMAN_APP_USER logs in for the API and the worker and only inherits flagman_app.
# Idempotent, so it runs both from /docker-entrypoint-initdb.d on an empty volume and again on an
# existing one (`compose exec postgres flagman-bootstrap-roles`):
# then it also rotates the passwords and takes back tables a `pg_restore --no-owner` gave the
# superuser. Runs as the image's superuser over the local socket.
#
# The image sources a script in initdb.d that is not executable; the subshell keeps `set -u` and
# `exit` from leaking into its entrypoint then.
bootstrap_roles() (
  set -euo pipefail

  : "${POSTGRES_USER:?the image superuser is not set}"
  : "${POSTGRES_DB:?POSTGRES_DB is not set}"
  : "${FLAGMAN_OWNER_USER:?set POSTGRES_OWNER_USER in the env file}"
  : "${FLAGMAN_OWNER_PASSWORD:?set POSTGRES_OWNER_PASSWORD in the env file}"
  : "${FLAGMAN_APP_USER:?set POSTGRES_USER in the env file}"
  : "${FLAGMAN_APP_PASSWORD:?set POSTGRES_PASSWORD in the env file}"

  # The ALTER ROLE below strips superuser rights: with the bootstrap superuser's name in either
  # variable the database would lose its only superuser.
  local name
  for name in "$FLAGMAN_OWNER_USER" "$FLAGMAN_APP_USER"; do
    case "$name" in
      "$POSTGRES_USER" | postgres | flagman_app)
        echo "error: role name '$name' is reserved; pick other POSTGRES_OWNER_USER / POSTGRES_USER" >&2
        exit 1
        ;;
    esac
  done
  if [[ "$FLAGMAN_OWNER_USER" == "$FLAGMAN_APP_USER" ]]; then
    echo "error: POSTGRES_OWNER_USER and POSTGRES_USER must be different roles" >&2
    exit 1
  fi

  # Values come in through \getenv rather than the command line, so the passwords do not show in
  # the process list, and are quoted by format() with %I / %L.
  psql --no-psqlrc --quiet --set ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
\getenv owner FLAGMAN_OWNER_USER
\getenv owner_password FLAGMAN_OWNER_PASSWORD
\getenv app FLAGMAN_APP_USER
\getenv app_password FLAGMAN_APP_PASSWORD
\getenv db POSTGRES_DB

BEGIN;

SELECT 'CREATE ROLE flagman_app NOLOGIN'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'flagman_app')
\gexec
SELECT format('CREATE ROLE %I', :'owner')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'owner')
\gexec
SELECT format('CREATE ROLE %I', :'app')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'app')
\gexec

-- Attributes are set every run, so a role made by hand earlier ends up the same.
ALTER ROLE flagman_app NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
SELECT format(
  'ALTER ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
  :'owner', :'owner_password')
\gexec
SELECT format(
  'ALTER ROLE %I LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
  :'app', :'app_password')
\gexec
SELECT format('GRANT flagman_app TO %I', :'app')
WHERE NOT EXISTS (
  SELECT FROM pg_auth_members m
  JOIN pg_roles member ON member.oid = m.member
  JOIN pg_roles grp ON grp.oid = m.roleid
  WHERE member.rolname = :'app' AND grp.rolname = 'flagman_app')
\gexec

-- Only these roles connect; PUBLIC loses the default CONNECT and TEMPORARY.
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'db')
\gexec
SELECT format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO %I', :'db', :'owner')
\gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO flagman_app', :'db')
\gexec

SELECT format('ALTER SCHEMA public OWNER TO %I', :'owner')
\gexec
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO flagman_app;

-- Objects made before the roles existed, or restored with --no-owner, belong to the superuser.
-- Those of extensions (PostGIS) stay where they are. Table rights are the migrations' job.
SELECT set_config('flagman.owner', :'owner', true) AS owner_setting
\gset
DO $$
DECLARE
  owner_name text := current_setting('flagman.owner');
  obj record;
BEGIN
  FOR obj IN
    SELECT c.relkind, format('%I.%I', n.nspname, c.relname) AS name
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
      AND pg_get_userbyid(c.relowner) <> owner_name
      AND NOT EXISTS (
        SELECT FROM pg_depend d
        WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e')
      -- A sequence of a serial or identity column moves together with its table.
      AND NOT (c.relkind = 'S' AND EXISTS (
        SELECT FROM pg_depend d
        WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype IN ('a', 'i')))
  LOOP
    EXECUTE format('ALTER %s %s OWNER TO %I',
      CASE obj.relkind
        WHEN 'S' THEN 'SEQUENCE'
        WHEN 'v' THEN 'VIEW'
        WHEN 'm' THEN 'MATERIALIZED VIEW'
        WHEN 'f' THEN 'FOREIGN TABLE'
        ELSE 'TABLE'
      END,
      obj.name, owner_name);
  END LOOP;

  FOR obj IN
    SELECT p.oid::regprocedure AS name
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public'
      AND pg_get_userbyid(p.proowner) <> owner_name
      AND NOT EXISTS (
        SELECT FROM pg_depend d
        WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e')
  LOOP
    EXECUTE format('ALTER ROUTINE %s OWNER TO %I', obj.name, owner_name);
  END LOOP;

  FOR obj IN
    SELECT t.oid::regtype AS name
    FROM pg_type t
    JOIN pg_namespace n ON n.oid = t.typnamespace
    WHERE n.nspname = 'public'
      AND t.typtype IN ('e', 'd')
      AND pg_get_userbyid(t.typowner) <> owner_name
      AND NOT EXISTS (
        SELECT FROM pg_depend d
        WHERE d.classid = 'pg_type'::regclass AND d.objid = t.oid AND d.deptype = 'e')
  LOOP
    EXECUTE format('ALTER TYPE %s OWNER TO %I', obj.name, owner_name);
  END LOOP;
END
$$;

COMMIT;
SQL
  echo "bootstrap-roles: roles of $POSTGRES_DB are up to date"
)

bootstrap_roles "$@"
