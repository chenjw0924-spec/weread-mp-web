# 微信读书文章归档：界面实施与验收

Date: 2026-10-19

## Result

final result: blocked

The implemented pages and tested flows are usable. Exact visual fidelity acceptance remains blocked because the in-app browser rejected the supplied local `file:` reference URL. No alternate route was used to circumvent that restriction. A rendered source capture and a normalized source/implementation comparison are therefore unavailable. This report does not claim that source-to-implementation visual QA passed.

## Sources and implementation evidence

- Source visual truth: `docs/wereaddesign/index.html`; supporting specification: `docs/wereaddesign/product-design.md`. User-provided sources are unchanged.
- Application: `app/templates/index.html`, `app/static/app.css`, `app/static/app.js`, `app/static/archive-ui.js`.
- Browser acceptance: isolated synthetic backend at `http://127.0.0.1:18766/`; production UI at `http://127.0.0.1:8765/`.
- Captures: `/tmp/weread-design-evidence/`. Screenshots show synthetic content explicitly prefixed “界面测试”, never production sample articles.
- `download-1440.png`: 1440 × 1000, completed download with two successes, one failure, image warning.
- `articles-final-375.png`: 375 × 812 CSS viewport (content screenshot excludes scrollbar); `confirm-375.png`, `expired-375.png`: 375 × 812 dialog states. Earlier screenshots taken during viewport transition were superseded.
- `articles-final-768.png`: 768 × 1024 CSS viewport; tablet single-column layout. `articles-final-1440.png`: 1440 × 1000 CSS viewport, selected article rows and dock.
- `connection-final-1440.png` is named historically but its observed viewport is 746 × 830; it is an additional production-page capture, not 1440px evidence.
- Additional final captures are stored in the same evidence directory.
- Density: browser screenshots at CSS pixel resolution (1×); source pixel dimensions unavailable. Source/implementation normalization and side-by-side comparison were not possible. No full-view or focused-region comparison is claimed.

## Fidelity surfaces inspected from source code and rendered implementation

- Fonts: retained supplied display/body/mono stacks, scale and hierarchy. Rendering uses locally available system fallbacks; no remote font loading. Exact source wrapping comparison pending.
- Spacing/layout: reused supplied 1120px shell, 300px sidebar, 560px connection card, 620px dialog, 16px card radius, 11px buttons, sticky header and fixed dock.
- Colors: retained #0c714b / #06452f greens, #f7f9f6 canvas, #dce5df lines and semantic error/warning states.
- Assets: no raster artwork required by this interface; local MIT-licensed Bootstrap icons replace prototype emoji and symbol icons. No fabricated QR code, article thumbnails or image counts. Text initials follow the supplied account-row design.
- Copy: removed demo scene navigation and all simulated data/timers. Actual server dates, article counts, per-item outcomes and warnings drive the interface. Cookie details are confined to an explicit, collapsed advanced section.

## Findings and iteration history

1. [P2, fixed] 375px download dock buttons wrapped and squeezed the selection count. Evidence: `articles-375.png`. Fix: prevent action labels from wrapping, reserve action width, hide secondary hint at phone width; `articles-375-fixed.png` shows usable one-line controls.
2. [P1, fixed] Reconnecting during an export stopped progress polling permanently. Fix: resume polling retained active jobs after connection restores. Actual event-handler regression covers this.
3. [P1, fixed] Switching accounts while export creation was pending could assign retry to the wrong account. Fix: snapshot account, selected IDs and image option before the asynchronous request. Actual event-handler regression covers this.
4. [P1, fixed] Export-time credential expiry became a generic failure with no reconnection guidance. Fix: preserve a safe `connection_expired` task flag and show retained-selection reconnection UI. API and UI regressions cover this.
5. [P1, fixed] Retrying failures used the original stale authentication callback. Fix: retry binds the current verified connection. API regression verifies old credentials fail and renewed credentials succeed.
6. [P2, fixed] Retrying could obscure earlier successfully generated files. Fix: retain separate download controls for prior successful packages; a dock result button reopens the latest results.
7. [P1, fixed] Disconnect during pending job creation could resurrect a deleted task. Fix: ignore responses from prior connection generations; actual handler regression verifies the pending response is discarded.
8. [x] Real QR authorization is implemented with WeRead's current web flow (`POST /web/login/getuid`, `POST /web/login/getinfo`, `POST /web/login/weblogin`). The browser receives a server-generated `/web/confirm?pf=2&uid=...` QR SVG, polls an in-memory session task, and never receives the resulting Cookie. Accounts that require phone double verification enter an in-memory `otp_required` state; the 4-digit code is posted to the same login request with the original login fingerprint and is never logged, stored, or returned. Expiration, invalid/expired code, failure, cancellation, and successful account handoff are covered by API and browser behavior tests. Successful scanning still requires the operator's phone confirmation.
9. [P2, blocked acceptance] Source screenshot comparison is unavailable due to browser URL policy. Pixel/visual fidelity acceptance remains pending.

## Functional and accessibility verification

- Browser: wrong-password visible error; correct password enters connection page; real QR start/status success handoff; manual connection; account selection; title filtering; filtered select-all retains hidden selections; true empty and no-match states; per-article real task progress; success/failure counts; image fallback warning; failed-item retry; actual ZIP download and contents inspected; upstream expiry retains three selections and restores them after reconnect.
- 375 / 768 / 1440 layouts inspected. Observed `scrollWidth` does not exceed viewport width. The 375px dock issue was fixed and recaptured.
- Native dialog: Tab reaches image checkbox; Escape closes the confirmation and restores focus to the initiating download button. Expiry dialog requires the reconnection action. Semantic labels/live regions and reduced-motion rules present.
- Browser console checked: no application JavaScript errors in the tested flow.
- Python suite: 53 passing tests, with one upstream Starlette/httpx deprecation warning. Includes 20 Node behavior tests invoked by pytest.
- Node syntax checks pass. Docker image builds; final smoke verification recorded in task response.
- No real WeRead credentials were used for synthetic flow tests. The production WeRead UID endpoint returned a fresh UID during protocol smoke testing; real phone confirmation and real-account article download were not repeated in this refactor.

## Follow-up acceptance

- Capture the supplied design in a browser context that permits the source and run normalized visual comparison; exact visual QA remains pending.
- Keep the manual Cookie form as an explicit diagnostic fallback for accounts where the upstream QR protocol is temporarily unavailable.
