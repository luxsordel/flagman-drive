# syntax=docker/dockerfile:1
# The stand's edge: Caddy with the admin panel and the client web app (docs/adr/0021).
# Built from the repository root, where the npm workspaces are:
#   docker build -f infra/stand/web/Dockerfile --build-arg STAND_DOMAIN=example.com .

FROM node:22.21.1-bookworm-slim AS build

WORKDIR /src

# Manifests first, so a code change does not reinstall every package.
COPY package.json package-lock.json ./
COPY packages/api-client/package.json packages/api-client/
COPY packages/design-tokens/package.json packages/design-tokens/
COPY apps/admin/package.json apps/admin/
COPY apps/mobile/package.json apps/mobile/
COPY apps/mobile/scripts apps/mobile/scripts
RUN npm ci --no-audit --no-fund

COPY tsconfig.base.json ./
COPY packages packages
COPY apps/admin apps/admin
COPY apps/mobile apps/mobile

# The web app calls the API cross-origin, so its address is part of the bundle. Public, not a secret.
ARG STAND_DOMAIN
RUN test -n "$STAND_DOMAIN" || { echo "STAND_DOMAIN build arg is required" >&2; exit 1; }
RUN npm run build --workspace @flagman/admin
RUN EXPO_PUBLIC_API_URL="https://api.${STAND_DOMAIN}" npm run build:web --workspace @flagman/mobile


FROM caddy:2.10.2-alpine

# The image's binary carries the bind capability as a file attribute; with every capability dropped
# (infra/stand/compose.yml) such a binary does not start at all. A plain copy has no attribute.
RUN cp /usr/bin/caddy /tmp/caddy && mv /tmp/caddy /usr/bin/caddy \
    && addgroup -S -g 10001 caddy \
    && adduser -S -u 10001 -G caddy -H -s /sbin/nologin caddy \
    && chown caddy:caddy /data /config

COPY infra/stand/web/Caddyfile /etc/caddy/Caddyfile
# Owned by root and read-only for the Caddy user, like the API's code.
COPY --from=build /src/apps/admin/dist /srv/admin
COPY --from=build /src/apps/mobile/dist/web /srv/app

USER caddy
