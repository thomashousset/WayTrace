# WayTrace Frontend

Single-file frontend served by the FastAPI backend at `/`.

- `index.html`, vanilla JS, no build step, dark theme by default.
- Tabs per extraction category, sortable columns, global search,
  JSON/CSV export.
- Polls `GET /api/jobs/{id}` (or subscribes to the SSE stream) for live
  progress.

To work on the frontend, run the backend (see the root [README](../README.md))
and open <http://localhost:8000>. Changes to `index.html` are picked up
on a browser reload.
