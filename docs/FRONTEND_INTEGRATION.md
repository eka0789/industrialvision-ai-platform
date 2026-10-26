# Frontend integration and visual direction

## Supported browser workflow

The operator console connects to the existing FastAPI authentication, health, camera, inspection, model registry, and administrator APIs. Requests use `/api/v1` at the browser edge. Vite and Nginx rewrite that prefix to the backend's existing unprefixed routes; backend route consumers are therefore not forced to migrate.

The end-to-end image workflow is:

1. Log in and restore the authenticated user with `/auth/me`.
2. Register an active camera (recommended source: `upload`) and a compatible active model.
3. Upload JPEG, PNG, or WebP to `POST /inspections/image`.
4. Display the persisted decision, rationale, latency, detections, and model metadata.
5. Retrieve the original evidence with the protected `/inspections/{id}/evidence` endpoint and render bounding boxes against actual rendered image dimensions.
6. Read and filter paginated history; download an individual record as JSON.

There is no invented live preview, fabricated aggregate time series, or implied model-readiness claim. Sampled quality ratios are explicitly labeled as samples, not fleet-wide quality metrics. Connection state is administrative, not a verified camera heartbeat. Browser video upload and live-camera inspection remain outside this integration because the backend flows are not yet unified.

## Visual direction

**Industrial instrument panel / restrained editorial.** The dominant materials are graphite and warm off-white, with a single lime signal color. Registration marks, inspection coordinates, compact technical labels, and a decision tape provide the recognizable visual anchor. Typography uses locally bundled Space Grotesk, Manrope, and IBM Plex Mono. The design avoids purple gradients, glassmorphism, decorative blobs, generic purple icon tiles, and non-functional motion.

The intended first action is clear on every page: start an inspection, inspect a record, configure a station, or manage a model. Loading, empty, API error, validation error, and persisted inference-error states are separate. Mobile navigation, keyboard focus, native modal focus containment, and reduced-motion preferences are supported.

## API adjustments included

- Protected inspection-evidence response with server-owned storage-path resolution, media allowlist, private caching, and `nosniff`.
- Database health connection closes its SQLAlchemy session and connection correctly; version metadata is not secret-redacted.
- Runtime requirements include the OpenCV/Pillow/ONNX dependencies imported at application startup and a PostgreSQL driver for the existing Compose deployment.

Real production model performance, camera hardware, and GPU inference still require validation on the target deployment; this UI integration does not assert those are validated.
