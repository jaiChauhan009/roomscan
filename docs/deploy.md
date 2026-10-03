# Deploying roomscan

Two pieces, hosted separately:

| piece | what | where |
|---|---|---|
| front end | `web/`: static HTML / JS / CSS, no build step | **Vercel** (free Hobby plan) |
| back end | `server/`: FastAPI + the engine, CPU only, ~0.8 GB of model weights baked into a ~3 GB image | **Hugging Face Spaces**, Docker SDK, free CPU Basic (2 vCPU, 16 GB RAM, port 7860) |

Alternatives for the back end: Railway, Render, Fly.io (same Dockerfile, see the end of
`server/README.md`), or, for a demo, this laptop behind a Cloudflare Tunnel (section D).

The browser talks to the API directly (cross-origin), so the two are joined by two settings:
the API URL in `web/env.js`, and the front end's origin in the server's `ROOMSCAN_CORS`.

---

## A. Front end on Vercel

1. Sign in at https://vercel.com with GitHub.
2. **Add New... > Project**. Under *Import Git Repository* pick `jaiChauhan009/roomscan`
   (if it is not listed: *Adjust GitHub App Permissions* and grant the repository).
3. On *Configure Project*:
   * **Project Name**: `roomscan` (gives `https://roomscan.vercel.app` if free; otherwise note
     the name Vercel assigns).
   * **Framework Preset**: *Other*.
   * **Root Directory**: click *Edit*, choose `web`, *Continue*.
   * **Build and Output Settings**: leave *Build Command* empty / off, *Output Directory*
     empty (it serves `web/` as is), *Install Command* empty / off.
   * No environment variables (a static site cannot read them; the API URL is in `web/env.js`).
4. **Deploy**. When done, open the URL: the capture page loads (it will say the API is
   unreachable until section C).

`web/vercel.json` already sets: `index.html` at `/`, clean URLs, `nosniff` and a referrer
policy, short caching for `js/` and `styles.css` (5 min, revalidated in the background), a
day for `img/` and the favicon, and no caching for `env.js` and the HTML, so a redeploy is
seen at once. Every push to `main` redeploys; other branches get preview URLs.

## B. Back end on a Hugging Face Space

A Space is a git repository that Hugging Face builds from `./Dockerfile` at its root, and
whose `README.md` must start with YAML front matter (`sdk: docker`, `app_port: 7860`).
Ours lives in `deploy/hf-space/README.md`; the image is `server/Dockerfile`.

1. Sign in at https://huggingface.co. **New > Space** (https://huggingface.co/new-space):
   * **Owner**: you; **Space name**: `roomscan`.
   * **Select the Space SDK**: *Docker*, template *Blank*.
   * **Space hardware**: *CPU basic, 2 vCPU, 16 GB, FREE*.
   * **Visibility**: *Public* (a private Space needs an HF token on every request, which the
     browser front end does not send).
   * **Create Space**.
2. Create a token: avatar > **Settings > Access Tokens > Create new token**, type *Write*
   (or fine-grained with write access to this Space). Copy it.
3. Push the server from a clone of this repository (Git Bash / macOS / Linux):

   ```
   export HF_TOKEN=hf_xxx
   sh deploy/hf-space/push.sh <hf-user>/roomscan
   ```

   The script assembles the Space repo in a temp directory (only `pyproject.toml`, `uv.lock`,
   `src/`, `scripts/`, `server/`, `.dockerignore`, plus `server/Dockerfile` as `Dockerfile`
   and `deploy/hf-space/README.md` as `README.md`) and force-pushes it as one commit. It
   archives the committed `HEAD`, so commit first.

   Doing it by hand instead: `git clone https://huggingface.co/spaces/<hf-user>/roomscan`,
   copy those files in, `cp server/Dockerfile Dockerfile`, `cp deploy/hf-space/README.md
   README.md`, commit, `git push` (user = your HF name, password = the token).

   Automatically on every push to GitHub `main`: copy `deploy/hf-space/github-action.yml`
   to `.github/workflows/hf-space.yml`, add the repository secret `HF_TOKEN` and the variable
   `HF_SPACE` = `<hf-user>/roomscan` (GitHub > Settings > Secrets and variables > Actions).
4. On the Space page the status goes *Building* (the first build takes ~10-20 min: CPU torch,
   open3d, and the model weights download) then *Running*. *Logs* shows the build and the
   server. Check it: `https://<hf-user>-roomscan.hf.space/api/health` returns
   `{"ok": true, "version": ...}`; `/docs` is the interactive API.
   The direct URL is `https://<owner>-<space-name>.hf.space`, lowercase, with `_` and `.`
   turned into `-` (also shown under *... > Embed this Space*).
5. **Settings > Variables and secrets > New variable** (the Space restarts on change):

   | name | value |
   |---|---|
   | `ROOMSCAN_CORS` | `https://roomscan.vercel.app` (your Vercel origin, see C) |
   | `ROOMSCAN_RESULT_TTL_DAYS` | `2` (the image default; raise it only with persistent storage) |
   | `ROOMSCAN_MAX_FILE_MB` | optional, default `2048` |

**Storage is ephemeral on the free tier.** `DATA_DIR` is `/data`, which without paid
persistent storage is plain container disk: every project, upload and result is lost when the
Space restarts (a variable change, a new push, a crash, or the sleep after inactivity). Users
must download their results (`result.xlsx`, `plan.png`) when the job finishes. With
**Settings > Persistent storage** (paid) Hugging Face mounts a disk at `/data` and results
survive restarts for `ROOMSCAN_RESULT_TTL_DAYS`. The image runs as uid 1000 as Spaces
require, with `HOME=/home/user` writable and the weights in a read-only `HF_HOME=/opt/hf`
(the container runs offline: `HF_HUB_OFFLINE=1`).

## C. Connecting them

1. **API URL in the front end**: edit `web/env.js` on `main`:

   ```js
   window.ROOMSCAN_API = window.ROOMSCAN_API || "https://<hf-user>-roomscan.hf.space";
   ```

   Commit and push; Vercel redeploys in under a minute. Order of precedence in
   `web/config.js`: `?api=` in the page URL (remembered in the browser) > the remembered value
   > `env.js` > `http://localhost:8000`. So `https://roomscan.vercel.app/?api=https://other-host`
   points one browser at another server without a redeploy, and `?api=reset` goes back to
   `env.js`. (Note: a browser that was once given `?api=` keeps it until `?api=reset`.)
2. **CORS on the server**: `ROOMSCAN_CORS` = the exact front-end origin: scheme + host, no
   path, no trailing slash. Several are comma separated, e.g.
   `https://roomscan.vercel.app,http://localhost:5173`. The default `*` allows any origin
   (fine for a demo, and needed if you use Vercel preview URLs, which change per deploy).
   A wrong value shows in the browser console as *blocked by CORS policy* with the API
   otherwise reachable.
3. **HTTPS**: Vercel serves only https, so the API must be https too (browsers block
   `http://` calls from an https page as mixed content, except `http://localhost` in Chrome and
   Firefox). Spaces, Railway, Render, Fly and Cloudflare tunnels all give https URLs.
4. Test: open the Vercel URL, the API status should be green; create a project, add a small
   photo space, upload, *Check*, *Run*.

## D. Free demo alternative: this laptop + Cloudflare Tunnel

No image build, no cloud CPU limits, results stay on the laptop. The laptop must stay on and
awake while people use it.

1. Install `cloudflared`: `winget install --id Cloudflare.cloudflared` (Windows),
   `brew install cloudflared` (macOS).
2. Start the server (see `server/README.md`), allowing the front end's origin:

   ```
   set ROOMSCAN_CORS=https://roomscan.vercel.app        (PowerShell: $env:ROOMSCAN_CORS="...")
   uv run --extra server uvicorn server.app:app --port 8000
   ```
3. In a second terminal: `cloudflared tunnel --url http://localhost:8000`. It prints a
   `https://<random-words>.trycloudflare.com` URL (no account needed).
4. Open `https://roomscan.vercel.app/?api=https://<random-words>.trycloudflare.com`, or put
   that URL in `web/env.js`.

Limits: the quick-tunnel URL changes every time `cloudflared` restarts (a named tunnel on a
free Cloudflare account keeps a fixed hostname); Cloudflare caps a single request body at
100 MB on free plans, so a large video file upload fails through the tunnel (photos and LiDAR
files are uploaded one file per request and stay well below it).

## E. Costs and limits

| | cost | limits |
|---|---|---|
| Vercel Hobby | free | personal / non-commercial use; 100 GB bandwidth / month |
| HF Spaces CPU basic | free | 2 vCPU, 16 GB RAM, 50 GB ephemeral disk; **sleeps after 48 h without traffic** (the next visit wakes it in ~1-2 min, data lost); one job at a time |
| HF persistent storage | paid, monthly (see huggingface.co/pricing) | keeps `/data` across restarts |
| HF CPU upgrade (8 vCPU, 32 GB) | paid, per hour | ~3x faster jobs |
| Cloudflare quick tunnel | free | random URL, 100 MB per request, laptop must stay on |
| Railway / Render / Fly | paid for >= 4 GB RAM instances | need a volume at `/data` |

Processing time on the free 2 vCPU Space (one job at a time; others queue):

| capture | time |
|---|---|
| LiDAR scan (Stray Scanner) | 2-3 min |
| photos (one room, ~20-40 photos) | 4-8 min |
| video (1-2 min clip) | ~10+ min |

Memory: a job peaks at ~4 GB (8 GB for long videos); 16 GB is comfortable.

## F. Security notes

* **There is no authentication.** Anyone who has (or guesses) the API URL can create
  projects, upload files of up to `ROOMSCAN_MAX_FILE_MB` each, run jobs (tying up the single
  worker) and read any job whose id they know. Job and project ids are random, so results are
  not listable, but treat the deployment as a demo: do not upload captures of private homes
  you are not allowed to share.
* `ROOMSCAN_CORS` restricts which web pages can call the API from a browser; it does not stop
  scripts or `curl`.
* Keep `ROOMSCAN_MAX_FILE_MB` and `ROOMSCAN_RESULT_TTL_DAYS` low on a public instance; the
  disk is the only quota.
* Never put tokens in `web/env.js`: everything in `web/` is public.
* Follow-up (not implemented): a shared secret `ROOMSCAN_TOKEN`; when set, the server
  rejects `/api/*` requests (except `/api/health`) without `Authorization: Bearer <token>`,
  and the front end asks for the token once and keeps it in `localStorage` (not in
  `env.js`). Rate limiting per IP would be the next step.

## G. Back end on an Oracle Cloud "Always Free" ARM VM (free, always on)

Hugging Face now requires PRO for Docker Spaces. Oracle's Always Free tier gives an Ampere
(ARM) VM of up to 4 OCPU / 24 GB RAM at no cost, never sleeping. Every dependency has an
aarch64 wheel (open3d 0.20 needs glibc >= 2.35: the image's Debian bookworm has 2.36).

1. Create the VM: **Compute > Instances > Create instance**. Image **Canonical Ubuntu 24.04**
   (or 22.04), shape **Ampere VM.Standard.A1.Flex, 4 OCPU, 24 GB**, a public IPv4 address,
   **Save private key**. ("Out of capacity" is common: retry later, or another availability
   domain.)
2. Open the ports: the instance's subnet **Security List > Add Ingress Rules**: source
   `0.0.0.0/0`, TCP, destination ports `80,443`.
3. `ssh -i <key> ubuntu@<public-ip> 'bash -s' < deploy/oracle/setup.sh`. It builds the image
   on the VM (~15-25 min), runs it with automatic restart, and serves HTTPS at
   `https://<ip-with-dashes>.sslip.io` through Caddy. Re-run it to update.
4. Point `web/env.js` at that URL. Email: add the SMTP variables to `/etc/roomscan.env` on the
   VM and re-run the script.

Idle reclamation: Oracle may reclaim an Always Free VM whose CPU stays under ~20 % for 7 days.
Upgrading the account to Pay As You Go (still free within the Always Free limits) stops that.
