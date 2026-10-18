# WeRead MP Web Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a locally runnable, independently deployable website that uses the operator's authenticated WeRead session to list followed-account articles and archive selected articles to Markdown ZIP files.

**Architecture:** FastAPI serves a native frontend and same-origin API. Application auth, WeRead cookies, list snapshots, archive jobs, and temporary ZIPs live in one process's memory; Docker Compose therefore runs exactly one worker and mounts no persistent data volume.

**Tech Stack:** Python 3.10+, FastAPI, Uvicorn, BeautifulSoup4, Markdownify, pytest, httpx, Docker Compose, native HTML/CSS/JavaScript.

**Spec:** `docs/superpowers/specs/2026-10-19-weread-mp-web-design.md`

## Global Constraints

- WeRead cookies remain in process memory only and never appear in logs, browser storage, ZIPs, or persisted files.
- APP_PASSWORD is required; secure, HttpOnly, SameSite=Strict sessions gate every non-health route.
- Run one Uvicorn worker; session and job state is process-local.
- Stop list pagination on upstream terminal signals, empty responses, repeated cursors, or configured hard limits; never evade captcha.
- Request only WeRead and expected WeRead media origins; preserve remote image links for failed local image downloads.
- Every saved article is a Markdown file with valid quoted YAML Front Matter; selected output is a ZIP with optional images.
- No automated test may use a real user Cookie.
- Run unit/API/frontend tests, Docker build, container smoke tests, and a local deployment before completion.

---

## File Structure

- `app/config.py`: required environment configuration, limits, and cookie/security settings.
- `app/auth.py`: password login, expiring in-memory sessions, login throttling, and connection state.
- `app/weread.py`: WeRead HTTP client, cookie validation, shelf accounts, paginated article lists, and error classification.
- `app/content.py`: article HTML parsing, normal text-message extraction, image extraction, safe YAML Front Matter, and Markdown rendering.
- `app/archive.py`: optional media retrieval, ZIP creation, and temporary-artifact cleanup.
- `app/jobs.py`: bounded in-memory archive tasks, progress snapshots, failure lists, and retry orchestration.
- `app/main.py`: FastAPI routes, authentication dependencies, secure static app serving, and lifecycle cleanup.
- `app/templates/index.html`, `app/static/app.css`, `app/static/app.js`: responsive Chinese self-hosted operator UI.
- `tests/`: unittestable service/API tests using fake HTTP responses and synthetic WeRead payloads.
- `Dockerfile`, `compose.yaml`, `.env.example`, `.dockerignore`: reproducible single-worker container deployment.
- `README.md`: Chinese setup, cookie acquisition, HTTPS/proxy, backup/privacy, and troubleshooting instructions.
- `requirements.txt`, `requirements-dev.txt`, `pytest.ini`: pinned runtime and test dependencies.

## Task 1: App skeleton, configuration, and session authentication

**Files:** create the package skeleton, configuration/auth modules, dependencies, and `tests/test_auth.py`.

**Interfaces:**
- `Settings.from_env() -> Settings` requires `APP_PASSWORD`, parses `COOKIE_SECURE`, session TTL, job/list limits, and artifact TTL.
- `SessionStore.login(password, now) -> Session | None`; `get(token, now) -> Session | None`; `logout(token) -> None`; session has `connected_cookie: str | None`, `accounts: dict[str, AccountSnapshot]`, and `jobs: dict[str, ArchiveJob]`.
- `SessionStore.connect_cookie(token, cookie, verifier) -> ConnectionResult` stores credentials only after a successful verifier; `disconnect(token) -> None` clears credentials and account snapshots.

- [ ] Write failing tests for missing APP_PASSWORD, successful/incorrect password, expiration, logout, and failed/successful cookie validation.
- [ ] Run `pytest tests/test_auth.py -q`; confirm failures identify missing settings/session APIs.
- [ ] Implement configuration and an in-memory session store with random opaque tokens, constant-time password comparison, expiry pruning, and per-IP login throttling.
- [ ] Re-run `pytest tests/test_auth.py -q`; all auth tests pass and no test writes credentials to disk.

## Task 2: WeRead client and history list

**Files:** create `app/weread.py` and `tests/test_weread.py`.

**Interfaces:**
- `WeReadClient.request_json(path, params, cookie) -> dict` maps HTTP, `-2010`, and `-2041` to typed safe errors without echoing credentials.
- `WeReadClient.validate_cookie(cookie) -> list[Account]` calls `/web/shelf/sync` and requires a valid WeRead shelf response.
- `WeReadClient.list_articles(cookie, book_id, max_articles) -> ArticleListResult` uses `offset=0`, expands `reviews/subReviews/review/mpInfo`, deduplicates by `originalId`, advances by the minimum `createTime`, pauses between pages, and stops on `clearAll`, empty pages, non-advancing/repeated cursors, or caps. Result includes stop reason and returned date range.

- [ ] Write failing fixtures for shelf filtering, 77-style nested entries, duplicate IDs, multiple cursors, `clearAll`, empty page, repeated cursor, expired cookie, and captcha.
- [ ] Run `pytest tests/test_weread.py -q`; confirm failures are missing client behavior.
- [ ] Implement a standard-library HTTP client with connect/read timeout, expected WeRead host, bounded pagination, randomized 1.5–3 second page delay, and typed error handling.
- [ ] Re-run the focused tests and verify all branches stop within the requested page/article caps.

## Task 3: Content parsing and Markdown archive format

**Files:** create `app/content.py` and `tests/test_content.py`.

**Interfaces:**
- `parse_article_html(html: str, listed: ListedArticle) -> ParsedArticle` extracts canonical URL without `chksm`, metadata, nested `#js_content`, or a text-page `content_noencode` script value.
- `ParsedArticle` has `title`, `published_at`, `account`, `source_url`, `account_id`, `article_id`, `markdown_body`, and `image_urls`.
- `render_markdown(article, local_images: Mapping[str, str]) -> str` emits JSON-quoted YAML scalars in YAML Front Matter, Markdown body, and local images where downloads succeeded; failed image downloads retain absolute remote image references.
- `parse_picture_pages(html) -> list[ImageRef]` handles the upstream JavaScript image-list shape without executing remote script data.

- [ ] Write tests first for nested divs, text-message escaped `content_noencode`, HTML entity titles, `chksm` removal, inline images, image-message URLs, failed-image fallback, and YAML-special characters.
- [ ] Run `pytest tests/test_content.py -q`; confirm parser/renderer tests fail before implementation.
- [ ] Implement parsing with BeautifulSoup plus a safe non-executing JavaScript string/list parser; reject truncated or empty article content with actionable errors.
- [ ] Re-run tests and parse generated front matter with a YAML parser in tests to ensure title/account values round-trip.

## Task 4: Archive jobs, ZIP files, and retries

**Files:** create `app/archive.py`, `app/jobs.py`, and `tests/test_archive.py`.

**Interfaces:**
- `ArchiveService.archive(articles, fetch_article, download_images, progress_callback) -> ArchiveResult` processes articles sequentially, bounds response/image sizes, packages one Markdown file per article and an `images/` folder, and records per-article failure reasons.
- `JobRegistry.start(session, article_ids, include_images) -> ArchiveJob` validates selected IDs against the session's article snapshot and schedules one bounded worker task per session.
- `JobRegistry.get(session, job_id) -> ArchiveJobSnapshot`; `retry_failed(session, job_id) -> ArchiveJob` queues only failed article IDs.
- `ArchiveResult.zip_path` is temporary, cleaned on download and on expiry; jobs and ZIPs have explicit count/size/TTL bounds.

- [ ] Write tests for ZIP contents, image option, image fallback, an article fetch failure beside successful articles, monotonic progress, retrying only failures, cross-session job access denial, and artifact expiry.
- [ ] Run `pytest tests/test_archive.py -q`; verify failures before archive implementation.
- [ ] Implement bounded sequential jobs, media-origin validation, byte caps, progress updates, private temporary storage, and cleanup.
- [ ] Re-run archive tests and inspect ZIP entries to confirm no Cookie, session token, or credential data is present.

## Task 5: FastAPI routes and native operator UI

**Files:** create `app/main.py`, the HTML/CSS/JS files, and `tests/test_api.py` / `tests/test_frontend.py`.

**Interfaces:**
- Routes: `/healthz`; `GET /api/session`; `POST /api/login`; `POST /api/logout`; `POST/DELETE /api/weread/connect`; `GET /api/accounts`; `GET /api/accounts/{book_id}/articles`; `POST /api/jobs`; `GET /api/jobs/{job_id}`; `GET /api/jobs/{job_id}/download`; `POST /api/jobs/{job_id}/retry`.
- Auth cookie is HttpOnly, SameSite=Strict, configured Secure, has Max-Age and no Domain attribute. All API mutations are same-origin JSON and return no-store headers.
- Frontend includes password lock screen; Cookie form, connect/disconnect state; account picker; actual date-range and source-status display; title/summary search; date inputs; select-visible/select-all; include-images toggle; job progress; failures and retry.

- [ ] Write API tests for public/private access, login/logout, connection state without cookie echo, list errors, list filtering, job ownership, and download cleanup.
- [ ] Run `pytest tests/test_api.py -q`; confirm protected-route and route failures before adding routes.
- [ ] Implement routes with request-size bounds, safe errors, per-session state, and static asset serving.
- [ ] Write frontend tests for connect/disconnect, account load, search/date filtering, selection, job progress, empty/captcha/expired/error states, and retry.
- [ ] Run `pytest tests/test_frontend.py -q`; verify UI tests fail before frontend implementation.
- [ ] Implement accessible responsive native UI with semantic form controls and live status regions; keep credentials in the password field only until successful submit and clear the field afterward.
- [ ] Run all API/frontend tests and check keyboard access and layout at 375, 768, and 1440 pixels.

## Task 6: Docker deployment, documentation, and local launch

**Files:** create Docker and compose files, requirements files, ignore files, README, and `tests/test_deployment.py`.

- [ ] Write deployment checks for required APP_PASSWORD, one worker, localhost-only port binding by default, Secure-cookie option, and no persistent cookie/data volume.
- [ ] Run `pytest tests/test_deployment.py -q`; verify expected configuration checks fail before deployment artifacts exist.
- [ ] Add a non-root Python 3.10 slim image with health check, pinned production dependencies, and a single Uvicorn worker; Compose binds `127.0.0.1:8765:8000` and requires an externally supplied APP_PASSWORD.
- [ ] Document Docker build/up/down, local-only `COOKIE_SECURE=false`, HTTPS reverse proxy requirements, manual Cookie acquisition from the user's own WeRead session, process-memory lifecycle, no captcha bypass, and WeRead history limits.
- [ ] Run complete tests, build the container, start the service with a throwaway local test password, smoke-test `/healthz`, login protection, and static UI, then stop the test container.
- [ ] Start the final local deployment with a randomly generated one-run password held only in process configuration, give the user its URL and password, and do not claim public internet deployment without a supplied host/proxy target.

## Plan self-review

- Spec coverage: memory-only credentials and single-process state (Tasks 1, 4, 6); account/article listing and real coverage dates (Task 2); search/filter/selection and UI states (Task 5); Markdown/images/ZIP/retry/progress (Tasks 3–5); access password and expiry (Tasks 1, 5); independent Docker deployment/docs (Task 6); local smoke and running service (Task 6).
- Every service boundary and method signature used by a later task is defined above.
- Automated tests use fake responses and never read the user's live browser Cookie.
