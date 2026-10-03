# roomscan documentation

Start with the [project report](report.md) for what was built, how it was tested and the results.
Then read the [architecture](architecture.md) for how it works, field by field.

## By part of the system

| Part | Folder | Read |
|---|---|---|
| Engine (photos / video / LiDAR → plan) | [`../src/roomscan/`](../src/roomscan) | [architecture.md](architecture.md) §5-7, [tech_report.md](tech_report.md) |
| Back end (API, jobs, email) | [`../server/`](../server/README.md) | [server/README.md](../server/README.md), [architecture.md](architecture.md) §4 |
| Web front end | [`../web/`](../web/README.md) | [web/README.md](../web/README.md), [architecture.md](architecture.md) §8 |
| iOS capture app (RoomPlan live scan) | [`../ios/`](../ios) on branch `ios-app` | `ios/README.md`, `docs/ios_app.md` |
| Benchmark and fix loop | [`../bench/`](../bench/README.md), [`../fixloop/`](../fixloop/README.md) | [bench/README.md](../bench/README.md), [fixloop/README.md](../fixloop/README.md) |
| Deployment | [`../deploy/`](../deploy) | [deploy.md](deploy.md) |

## All documents

### Results and the brief
- [report.md](report.md): project report covering testing, scores, deployment, limitations and optimization.
- [compliance_matrix.md](compliance_matrix.md): every requirement → file → artifact → status.
- [tech_report.md](tech_report.md): the technical report (max 6 pages).
- [device_matrix.md](device_matrix.md): which tier runs on which device, and how accurate it is.

### How it works
- [architecture.md](architecture.md): folders, inputs, API, stages and gates, output schema, front end, deployment.
- [design_qa.md](design_qa.md): design decisions and why.
- [worklog.md](worklog.md): the build, stage by stage.

### Capturing
- [capture_protocol.md](capture_protocol.md): the one-page capture route for non-engineers.
- [scale_marker.md](scale_marker.md): the optional printed A4 marker for exact scale.
- [iphone_session.md](iphone_session.md): the iPhone session plan (head-to-head, staged damage, repeat scans).
- [tape_form_own_flat.md](tape_form_own_flat.md): the tape-measure form for ground truth.

### Data and operations
- [raw_data.md](raw_data.md): raw benchmark data, its sources and checksums.
- [deploy.md](deploy.md): Vercel front end and the back end on a VM (Google Cloud / Oracle) or Hugging Face.

## Live system

- Web app: https://roomscan-web-rose.vercel.app
- API: https://34-14-174-240.sslip.io (`/api/health`, `/docs`)
