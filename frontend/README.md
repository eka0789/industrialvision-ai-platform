# IndustrialVision AI — Operator Console

React + TypeScript + Vite frontend for the FastAPI inspection backend. The interface uses Indonesian operator copy, locally bundled fonts, role-aware navigation, and real API data—no dashboard mock dataset.

## Included journeys

- JWT login, session restoration, logout, and role-aware guards.
- Operational overview derived from camera and inspection APIs; sampled quality metrics are explicitly labeled.
- Image inspection upload, real inference results, decision reasons, detections, and evidence retrieval.
- Filterable/paginated inspection history and detail with JSON export.
- Camera administration and administrative connection state.
- Model registry, activation/deactivation, metadata, and evaluation history.
- Administrator user creation.

Live camera preview is intentionally not presented as available. Image upload is the supported browser inspection workflow. A model must have a compatible artifact and inference configuration before it can produce a decision.

## Local development

From the repository root:

```bash
# Install dependencies
python -m pip install -r backend/requirements.txt
npm --prefix frontend ci

# Prepare the database and create the first admin (interactive password prompt)
cd backend
alembic upgrade head
python -m app.cli create-admin --username YOUR_USERNAME

# Terminal 1: API on http://127.0.0.1:8000
uvicorn app.main:app --host 127.0.0.1 --port 8000

# Terminal 2: frontend on http://127.0.0.1:5173
cd ../frontend
npm run dev
```

Vite forwards frontend calls under `/api/v1` to the corresponding unprefixed FastAPI routes. This prevents Vite's JSON pretty-print middleware from treating business routes as frontend files. Set `VITE_PROXY_TARGET` to change the backend origin. Use `VITE_API_BASE_URL` only when intentionally calling a separate API origin; its default is `/api/v1`.

The frontend does not create an administrator, camera, or model automatically. After login, an admin can add an **Unggah gambar** station. An admin or AI engineer can register an existing artifact under the backend's `MODEL_ROOT`; the form does not upload model weights.

## Verification

```bash
npm run lint
npm run build
```

The API provides protected image evidence at `GET /inspections/{id}/evidence`; the frontend fetches it as a bearer-authenticated blob. No storage path is exposed as a public static URL.

## Production proxy

`nginx.conf` preserves `/api` documentation routes, rewrites `/api/v1/*` to the backend's existing routes, and retains SPA fallback and WebSocket forwarding. See the repository operations guide for deployment prerequisites and model/runtime limitations.
