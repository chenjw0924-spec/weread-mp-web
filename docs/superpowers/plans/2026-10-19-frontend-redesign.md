# Frontend redesign implementation plan

Goal: apply `docs/wereaddesign/index.html` and `product-design.md` to the existing FastAPI application.

Scope: existing HTML/CSS/vanilla JavaScript and the task progress payload; retain password protection and memory-only credentials. The supplied design is approved by the user's implementation request. This is an in-place page refactor, not a replacement application.

- [x] Add meaningful regression tests for selection/filter state, reconnection, task progress, and image warnings. Preserve the existing login behavior tests.
- [x] Reuse the reference tokens and layout for the header, three steps, centered connection card, account sidebar, article filters, selection dock, and download dialog. Use local licensed Bootstrap icons instead of prototype emoji. Remove all prototype data and navigation.
- [x] Keep QR login explicitly unavailable because no real QR authorization backend exists; manual Cookie input lives only in an opt-in advanced section. Do not generate a fake QR code or simulate login success.
- [x] Bind each view to real APIs, protect against stale asynchronous list responses, keep selections on reconnection, add accessible modal focus management, and stop polling on errors/terminal jobs.
- [x] Expose actual per-article job states and image warnings, with retry and download feedback. Show only dates and metadata returned by the server.
- [x] Run Python/Node tests and Docker build; exercise real UI through an isolated synthetic-data service and inspect 375/768/1440 layouts. Keep fixture data out of production.
- [x] Record visual QA evidence and limitations in `design-qa.md`. File-protocol reference capture is blocked by browser policy; do not claim a screenshot comparison without both captures.

Visual source comparison remains blocked by browser file URL policy; details in `design-qa.md`. QR authorization remains an explicit backend capability gap.
