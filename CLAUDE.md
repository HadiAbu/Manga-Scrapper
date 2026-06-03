# CLAUDE.md — Manga Search (scalable-scraper)

## What this is

A manga search application that scrapes manga metadata from the Jikan API (MyAnimeList),
indexes it into OpenSearch, and serves it through a FastAPI backend behind an Nginx
reverse proxy / load balancer. No traditional database — OpenSearch is the only data store.

## Architecture

```
Internet :80
    │
    ▼
Nginx (LB + static server)          ← only public port
  /auth/*    → proxy_pass keycloak:8080   (Keycloak OIDC)
  /api/*     → proxy_pass api:8000        (requires valid JWT)
  /dashboards/ → proxy_pass dashboards:5601 (internal tool)
  /          → serves web/ (HTML/JS/CSS)
    │
    ├──────────────────────────────┐
    ▼                              ▼
FastAPI (api)                   Keycloak 24 (OIDC IdP)
  GET /api/search?q=&page=&genre=   realm: manga
  GET /api/manga/{mal_id}           client: manga-app (public + PKCE)
  GET /api/genres                   PostgreSQL backend
  GET /api/health  ← no auth        ▲
  All others require Bearer JWT     │
    │                          Keycloak DB (postgres:16)
    ▼
OpenSearch 2.x (single node)   ← internal only, no public port
  index: manga
    ▲
Scraper (cron, runs 3am daily) ← no HTTP, internal only
  fetches Jikan API v4
  bulk indexes into OpenSearch
```

## Build status

- [x] docker-compose.yml — all services defined (Keycloak + DB added)
- [x] nginx/nginx.conf — LB + static + API + Keycloak + Dashboards proxy
- [x] api/app/main.py — FastAPI app with CORS + JWKS prefetch on startup
- [x] api/app/auth.py — JWT validation against Keycloak JWKS
- [x] api/app/search.py — all endpoints; search/genres/manga require auth
- [x] scraper/scraper.py — Jikan pagination + OpenSearch bulk indexing
- [x] scraper/crontab — runs daily at 3am
- [x] web/index.html — search UI + auth overlay + logout button
- [x] web/style.css — dark theme, responsive card grid, auth overlay styles
- [x] web/app.js — Keycloak PKCE login, authFetch with auto token refresh
- [x] keycloak/realm-export.json — manga realm, manga-app client, test user
- [x] .env / .env.example — Keycloak admin + DB passwords
- [ ] Bookmarks / favorites — planned for later
- [ ] HTTPS / TLS termination — production hardening step

## How to run

```bash
# 1. Copy secrets file (edit passwords before production use)
cp .env.example .env

# 2. Build and start everything
#    First run: ~3-4 min (OpenSearch + Keycloak both need time to boot)
docker compose up --build

# 3. Seed manga data (run once after first boot)
docker compose run --rm scraper python scraper.py

# 4. Open the app — you'll be redirected to Keycloak login
#    Test credentials: testuser / password
```

Services once running:
- **App**: http://localhost — redirects to Keycloak login on first visit
- **Keycloak admin console**: http://localhost/auth/admin (admin / value from .env)
- **OpenSearch Dashboards**: http://localhost/dashboards/ (no direct port)
- **API** (authenticated): goes through Nginx at `/api/...`

```bash
# Scale API to 3 instances; Nginx upstream load-balances automatically
docker compose up --scale api=3

# Stop and wipe all data (resets OpenSearch index + Keycloak DB)
docker compose down -v
```

## Cron job explained

The scraper container runs `cron -f` as its main process (stays alive between runs).
Schedule is in `scraper/crontab`:

```
0 3 * * * root cd /app && python scraper.py >> /var/log/cron.log 2>&1
```

Format: `minute hour day month weekday user command`
`0 3 * * *` = every day at 3:00 AM.

Env vars (OPENSEARCH_URL etc.) are passed to cron via `entrypoint.sh` writing them to
`/etc/environment`, which cron sources before each job.

To run outside of the schedule:
```bash
docker compose run --rm scraper python scraper.py
```

## Configuration (env vars)

| Var | Default | Used by |
|-----|---------|---------|
| `OPENSEARCH_URL` | `http://opensearch:9200` | api, scraper |
| `MAX_PAGES` | `200` (= 5000 manga) | scraper |
| `KEYCLOAK_INTERNAL_URL` | `http://keycloak:8080/auth` | api (JWKS fetch) |
| `KEYCLOAK_PUBLIC_URL` | `http://localhost/auth` | api (issuer validation) |
| `KEYCLOAK_REALM` | `manga` | api |
| `KEYCLOAK_ADMIN_PASSWORD` | *(from .env)* | keycloak |
| `KEYCLOAK_DB_PASSWORD` | *(from .env)* | keycloak, keycloak-db |

Secrets live in `.env` (git-ignored). Copy `.env.example` and set real values before deploying.

## Data source

**Jikan API v4** — unofficial MyAnimeList wrapper, free, no auth required.
- Endpoint: `GET https://api.jikan.moe/v4/manga?page=N&limit=25&order_by=score&sort=desc`
- Rate limit: ~3 req/sec. Scraper sleeps 0.4s between requests (2.5 req/sec).
- Fields: title, title_english, synopsis, score, scored_by, cover images, genres,
  status, volumes, chapters, authors.

## OpenSearch index schema (`manga`)

| Field | Type | Notes |
|-------|------|-------|
| `mal_id` | integer | document `_id` — re-runs are idempotent |
| `title` | text + keyword | boosted x3 in search |
| `title_english` | text | boosted x2 in search |
| `synopsis` | text | full-text search |
| `score` | float | MAL community rating |
| `scored_by` | integer | number of raters |
| `cover_url` | keyword (not indexed) | MAL CDN URL |
| `large_cover_url` | keyword (not indexed) | larger version |
| `genres` | keyword[] | used for genre filter aggregation |
| `status` | keyword | e.g. "Finished", "Publishing" |
| `volumes` / `chapters` | integer | |
| `authors` | keyword[] | |
| `indexed_at` | date | last scrape timestamp |

## Decisions log

| Decision | Choice | Reason |
|----------|--------|--------|
| Search engine | OpenSearch 2.x | Open-source (Elasticsearch 8 uses SSPL for cloud) |
| Data source | Jikan API v4 | Free, no auth, full MAL metadata + scores |
| Load balancer | Nginx upstream | Requested; Docker DNS handles multi-instance scaling |
| Frontend | Plain HTML/JS/CSS | Requested; no build step, minimal dependencies |
| Scraper schedule | Daily at 3am | Manga data is slow-changing; low API pressure |
| Image storage | MAL CDN URLs (no download) | Simpler; revisit if MAL blocks hotlinking |

## Open questions / future work

- **Database**: PostgreSQL will be added later for user accounts, bookmarks, etc.
  OpenSearch stays as the search layer.
- **HTTPS**: HTTP only for now. For production add Let's Encrypt via certbot or
  Nginx `ssl_certificate` directives.
- **Image hotlinking**: If MAL CDN blocks hotlinks, will need a local image proxy
  or background download step in the scraper.
- **Scraper dedup**: `mal_id` is used as OpenSearch `_id`, so re-runs upsert — no
  duplicate documents.

## Development workflow

1. Make changes in the relevant service directory (`api/`, `scraper/`, `web/`, `nginx/`).
2. Rebuild only the affected service: `docker compose up --build <service>`.
3. For API changes, test all four endpoints before considering the change done:
   - `GET /api/health`
   - `GET /api/genres`
   - `GET /api/search?q=naruto`
   - `GET /api/manga/<mal_id>`
4. For scraper changes, run a manual scrape and confirm documents appear in OpenSearch Dashboards (`http://localhost:5601`).
5. For frontend changes, test in a real browser — the UI has no automated tests.
6. Commit message format: `<service>: <short imperative description>` (e.g. `api: add score range filter`).

## Code style & conventions

- **Python** (api, scraper): follow PEP 8; use type hints on all function signatures; keep functions under 40 lines.
- **JavaScript** (web/): vanilla ES2020+, no bundler, no framework. Keep DOM manipulation in `app.js` only.
- **Nginx config**: one `location` block per concern; comment any non-obvious directive.
- **Docker**: pin image tags (e.g. `opensearchproject/opensearch:2.13.0`), not `latest`.
- Do not add `print`/`console.log` debug statements to committed code.
- Do not introduce new Python dependencies without updating `requirements.txt` and explaining the addition.

## Testing

There is no automated test suite yet. Until one is added:

- Manually exercise every changed API endpoint and confirm the HTTP status and response shape match the schema table above.
- After any scraper change, inspect at least one indexed document in OpenSearch Dashboards to confirm field mapping is intact.
- After any Nginx config change, verify both the static-file route (`/`) and the API proxy route (`/api/health`) return `200`.

## Sensitive files — do not delete or overwrite without explicit confirmation

The following files are critical to the running system. **Never delete, truncate, or blindly overwrite them.**
Always read the current content and make targeted edits:

| File | Why it is sensitive |
|------|---------------------|
| `docker-compose.yml` | Defines all services, volumes, and networks — wrong edits break the entire stack |
| `nginx/nginx.conf` | Load-balancer and proxy rules — mistakes take down the public endpoint |
| `scraper/entrypoint.sh` | Injects env vars into cron's environment — removing it breaks all scheduled runs |
| `scraper/crontab` | Defines the scrape schedule — deleting it stops data updates |
| `api/requirements.txt` | Pinned Python deps for the API image — changes affect reproducibility |
| `scraper/requirements.txt` | Pinned Python deps for the scraper image — same concern |
| `.env` (if present) | Contains secrets (API keys, passwords) — never commit or expose |
| `CLAUDE.md` | Project instructions for Claude — do not delete |
| `README.md` | Public-facing documentation |

See `.claude/settings.json` for the corresponding machine-enforced deny rules.
