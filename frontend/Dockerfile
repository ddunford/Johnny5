# syntax=docker/dockerfile:1.9
#
# Johnny 5 — web UI image (the `web` service).
#
# Two stages:
#   1. build   — Node: `npm ci` from the lockfile, then `npm run build`
#                (tsc -b && vite build) → /app/dist (the static SPA bundle).
#   2. runtime — nginx:alpine serving the static build with an SPA fallback
#                (try_files … /index.html) + a tight CSP and security headers.
#                The server block is a template; the nginx entrypoint runs
#                envsubst on ${PUBLIC_DOMAIN} (only) so the CSP tracks the
#                deployed host without leaking nginx's own $variables.
#
# The SPA always talks to a same-origin /api + /ws — in prod Traefik routes
# those to the api service; this image only serves static files.

ARG NODE_VERSION=22
ARG NGINX_VERSION=1.27

# ---------------------------------------------------------------------------
# build — install deps (cached on the lockfile) then produce the bundle.
# ---------------------------------------------------------------------------
FROM node:${NODE_VERSION}-alpine AS build
WORKDIR /app
# Dependency layer keyed on the manifests only, so source edits don't reinstall.
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
# Build the production bundle.
COPY . .
RUN npm run build

# ---------------------------------------------------------------------------
# runtime — lean nginx serving the static bundle. No build tooling shipped.
# ---------------------------------------------------------------------------
FROM nginx:${NGINX_VERSION}-alpine AS runtime
# Server config as a template; the official entrypoint envsubsts it into
# /etc/nginx/conf.d/ at start. NGINX_ENVSUBST_FILTER restricts substitution to
# PUBLIC_DOMAIN so nginx runtime vars ($uri, $cache_control, …) survive intact.
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
ENV PUBLIC_DOMAIN=johnny.demosrv.uk \
    NGINX_ENVSUBST_FILTER=PUBLIC_DOMAIN
EXPOSE 80
HEALTHCHECK --interval=15s --timeout=4s --start-period=5s --retries=5 \
  CMD wget -qO- http://127.0.0.1/healthz >/dev/null 2>&1 || exit 1
# nginx:alpine's entrypoint renders the template then execs nginx.
