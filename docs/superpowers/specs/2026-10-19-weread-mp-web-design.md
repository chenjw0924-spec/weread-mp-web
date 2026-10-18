# WeRead MP Web Design

## Goal

Build a self-hosted web interface for viewing article lists from WeRead-followed public accounts and saving selected articles as Markdown with optional local images in a ZIP archive.

## Product boundary

- This is an independent project in `/home/admin/Documents/git_store/weread-mp-web`; it does not modify the Chrome extension or upstream `steptian/weread-mp` repository.
- The service uses WeRead's observed, undocumented web endpoints. It only exposes articles WeRead returns for accounts already followed in the WeRead app. It does not promise complete account history.
- The first release supports one self-hosted operator, one running application process, and one active WeRead connection per browser session.
- It has no user database, account registration, analytics, or remote article storage.

## Architecture

- Python 3.10+, FastAPI, Uvicorn, BeautifulSoup, and Markdownify provide one same-origin application server and API.
- A native HTML/CSS/JavaScript frontend is served by FastAPI; there is no frontend framework or asset build step.
- An in-memory session registry holds authenticated application sessions and their WeRead cookie strings. Cookie values never enter logs, files, job records, ZIP metadata, or browser local storage. Disconnect, session expiration, process restart, or container restart clears them.
- A deployment-wide `APP_PASSWORD` gates the interface. Successful login creates a random HttpOnly, SameSite=Strict session cookie with a configurable Secure attribute and an in-memory expiration. Login attempts are throttled in memory. Protected responses are not cached.
- A WeRead API client owns shelf lookup, article-list pagination, content retrieval, response classification, and polite request delays. It stops on the upstream terminal flag, empty page, repeated cursor, or a hard page cap. Captcha and expired-login errors are surfaced; captcha is never solved or bypassed.
- An archive service validates requested article IDs against the current session's fetched list, retrieves articles sequentially, renders YAML Front Matter and Markdown, optionally downloads images, and retains remote image URLs when an image download fails.
- A bounded in-memory job registry exposes progress and per-article failures. ZIP files are temporary artifacts, not durable article storage, and are deleted after download or expiry. Retry jobs contain failed article IDs only.
- Docker Compose runs exactly one Uvicorn worker. A reverse proxy or tunnel must provide HTTPS when the app is reachable outside localhost.

## User flow

1. Open the self-hosted origin and unlock it with the shared application password.
2. Paste the WeRead Cookie header obtained from the user's own WeRead browser session. The service validates it against the shelf endpoint before retaining it in memory.
3. Select a followed account. The service fetches the list using the observed `createTime` offset and reports the earliest and latest returned dates, article count, and any upstream stopping reason.
4. Search titles and summaries, filter by date, select articles, and choose whether to download images.
5. Start an archive job. The interface shows progress and per-article outcomes, downloads the ZIP, and allows retrying failures.
6. Disconnect to immediately clear the WeRead credentials from the server session.

## API surface

- `GET /api/session`: application authentication and WeRead connection state, never credential values.
- `POST /api/login`, `POST /api/logout`.
- `POST /api/weread/connect`, `DELETE /api/weread/connect`.
- `GET /api/accounts`, `GET /api/accounts/{book_id}/articles`.
- `POST /api/jobs` with selected article IDs and an image option; `GET /api/jobs/{job_id}`; `GET /api/jobs/{job_id}/download`; `POST /api/jobs/{job_id}/retry`.
- `GET /healthz` is a minimal liveness response and reveals no configuration or session data.

## Error and data handling

- Invalid/missing credentials produce a re-login message. WeRead's `-2041` response produces a captcha-required state and asks the operator to complete the normal WeRead challenge in their browser before reconnecting.
- Per-article content and image errors do not discard other successfully archived articles. Failures remain visible in the job and can be retried.
- Every Markdown document has safely quoted YAML Front Matter with title, date, account, source URL, WeRead account ID, and article ID.
- Cookies, application passwords, article contents, and article URLs are not written to application logs. Temporary ZIP artifacts are removed when downloaded or after a short retention period.
- No endpoint accepts arbitrary outbound URLs for server-side retrieval; article and image URLs come from the WeRead API response and are restricted to expected WeRead/media hosts.

## Deployment

- Include a reproducible Dockerfile, `compose.yaml`, `.env.example`, `.dockerignore`, health check, volume-free application state, and Chinese deployment guide.
- The container listens on port 8000; Compose publishes it to localhost port 8765 by default. Operators can put it behind their existing HTTPS proxy for remote access.
- `APP_PASSWORD` is required and has no repository default. `COOKIE_SECURE=true` is the production default; a documented localhost-only override supports plain HTTP local testing.
- Run one worker because sessions, jobs, and cookie storage intentionally live in process memory.

## Verification

- Unit and API tests cover password login, session expiry, cookie validation and clearing, WeRead error classification, account/article pagination, safe Markdown/YAML generation, image URL preservation, job progress, retries, and ZIP contents.
- Frontend tests verify visible connection, loading, success, empty, unsupported/captcha, and failure states and core search/filter/select/job interactions.
- Build the container, run a smoke test against `/healthz` and login, and start the self-hosted service locally. Do not use a real Cookie in automated tests.

## Out of scope

- Public multi-user SaaS, registration, persistent credentials, databases, cloud object storage, scheduled background exports, and remote hosting on a third-party account.
- Circumventing WeRead authentication, captcha, access controls, or upstream history limits.
- Modifying the existing extension or upstream project.
