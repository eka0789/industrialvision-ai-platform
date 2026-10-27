# Production TLS Deployment Guide

IndustrialVision AI terminates TLS at the unprivileged Nginx edge container.
Plain HTTP (`8080`) only returns a `301 Moved Permanently` redirect to HTTPS (`8443`).

## 1. Directory Structure

Certificates must be placed in `deploy/tls/` before running `docker compose up`:

```text
deploy/tls/
├── server.crt   # Full chain certificate
└── server.key   # Private key (read-only for nginx, 0600)
```

The `deploy/tls/*.crt` and `deploy/tls/*.key` files are intentionally excluded from git.

## 2. Generating Certificates

### Option A: Internal Enterprise PKI / Active Directory Certificate Services (Recommended)
Generate a Certificate Signing Request (CSR) with the factory hostnames/IPs in the SAN (Subject Alternative Names):
```bash
openssl req -new -newkey rsa:4096 -nodes \
  -keyout deploy/tls/server.key \
  -out deploy/tls/server.csr \
  -subj "/CN=qc-station.factory.local" \
  -addext "subjectAltName=DNS:qc-station.factory.local,IP:192.168.10.50"
```
Submit `server.csr` to your enterprise CA and save the returned certificate chain as `deploy/tls/server.crt`.

### Option B: Self-Signed Certificate (Isolated Factory LAN / Testing)
If the inspection cell operates on an air-gapped network without an enterprise CA:
```bash
openssl req -x509 -newkey rsa:4096 -sha256 -days 730 -nodes \
  -keyout deploy/tls/server.key \
  -out deploy/tls/server.crt \
  -subj "/CN=industrialvision-edge" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:192.168.1.100"
```
Set strict file permissions:
```bash
chmod 600 deploy/tls/server.key
chmod 644 deploy/tls/server.crt
```

## 3. Starting the Stack

```bash
docker compose up -d --build
```

Access the application at `https://<host>:8443/`.

## 4. Security Controls Enforced at the Edge

- **TLS Protocols:** TLSv1.2 and TLSv1.3 only; SSLv3/TLSv1.0/TLSv1.1 disabled.
- **HSTS:** `Strict-Transport-Security: max-age=31536000; includeSubDomains`.
- **Content-Security-Policy (CSP):** `default-src 'self'`. All scripts, styles, and fonts must originate from the application container. No external CDN dependencies.
- **WebSocket Protocol Isolation:** Bearer tokens travel in `Sec-WebSocket-Protocol: bearer.<jwt>` rather than the URL. Nginx access logs deliberately redact the query string (`iv_redacted` format).
- **Rate Limiting:** `limit_req` applies a 5 requests/minute budget to `/api/v1/auth/login` to thwart credential brute-force attacks.
