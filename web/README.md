# roomscan web (front end)

The phone-friendly web app: a static site of plain HTML, ES modules and CSS, with no build step.
- Live: **https://roomscan-web-rose.vercel.app** (Vercel, redeployed on every push to `main`).
- It talks to the back end in [`../server/`](../server/README.md).

## What the user does

1. **Email (optional), at the top.** With an address, the job runs in the background and the
   report (sizes with 90 % ranges, plan, spreadsheet) is emailed. Without one, results show on the page only.
2. **Whole-home capture (optional):**
   - up to 5 **videos**;
   - up to 5 **LiDAR scans** (Stray Scanner `.zip`).
3. **Rooms:**
   - a name, in walk order;
   - optional sizes (length / width / height in metres);
   - optional **photos** (2-8 per room).
4. **Check captures:** OK / Check / Retake per room and capture, with one line of advice.
5. **Start computing:**
   - live stages;
   - each run's results appear as soon as that run finishes (photos, then LiDAR, then video);
   - plan, per-room sizes with ranges, damage list;
   - downloads: `result.json`, `result.xlsx`, `plan.png`.
6. **How to capture (1 minute):** a collapsible guide with 5 diagrams (`img/guide/`).

## Files

| File | Role |
|---|---|
| `index.html` | the page: email box, guide, whole-home capture cards, rooms list, check and compute, job, results |
| `styles.css` | layout and theme (mobile first, 360 px and up; light and dark) |
| `env.js` | **deployment setting**: API address. On localhost it uses `http://localhost:8000`; elsewhere `https://34-14-174-240.sslip.io` (the Google Cloud back end) |
| `config.js` | resolves the API base. Precedence: `?api=` in the URL (remembered), then `env.js`, then localhost; `?api=reset` forgets the remembered value |
| `js/app.js` | page logic: projects, rooms, captures, verify, run, job polling, progressive results, email box |
| `js/api.js` | API client (fetch, plus XHR for upload progress) |
| `js/upload.js` | upload queue: 3 files at a time, SHA-256 dedup, retry with backoff, resumes after a reload |
| `js/shrink.js` | shrinks phone JPEGs to 2048 px before upload, keeping the EXIF focal length (about 5× smaller, identical engine input) |
| `js/store.js` | IndexedDB (pending uploads) and localStorage (project id, job id, email) |
| `js/results.js` | renders the results of each finished run |
| `js/sha256.js`, `js/dom.js` | hashing in chunks; small DOM helpers |
| `img/guide/*.svg` | capture-guide diagrams |
| `vercel.json` | Vercel headers and caching (`env.js` and the HTML are never cached) |
| `dev/mock_server.py`, `dev/smoke.html` | a mock API and smoke page for front-end tests (`tests/test_web_static.py`) |

## Behaviour worth knowing

- **Closing the tab:**
  - while files are still uploading, closing the tab **pauses** the upload. The files wait in IndexedDB and resume when the site is reopened in the same browser;
  - once computing has started, the job runs on the server and the tab can be closed.
- **Reopening:** the same browser shows the same project and the job's live stages or finished results.
- **Saved results:** the same captures and settings return the saved result at once. A run that failed is retried instead.

## Run locally

```bash
uv run python -m http.server 5173 --bind 0.0.0.0 -d web     # then open http://localhost:5173
```

The local API must run as well (see [`../server/README.md`](../server/README.md)).

## Deploy (Vercel)

1. Import the GitHub repository and set **Root Directory** to `web`.
2. Choose framework preset **Other**, with no build command.

Every push to `main` redeploys. To change the API address, edit `env.js`. Full guide: [`../docs/deploy.md`](../docs/deploy.md).

## Tests

`tests/test_web_static.py` covers:
- static checks: every module import resolves and the ids used in JS exist in the HTML;
- a headless-Chrome smoke run at 360 px against `dev/mock_server.py`.
