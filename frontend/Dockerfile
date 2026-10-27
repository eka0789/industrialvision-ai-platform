# Multi-stage build for IndustrialVision AI Frontend
FROM node:22-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

# Production static serving with unprivileged nginx (security hardening)
FROM nginxinc/nginx-unprivileged:alpine-slim AS runner

USER nginx

# Edge config: TLS termination, security headers, rate limits, redacted logs.
COPY --chown=nginx:nginx nginx.conf /etc/nginx/conf.d/default.conf
COPY --chown=nginx:nginx iv-proxy-headers.inc /etc/nginx/conf.d/iv-proxy-headers.inc

# Compiled static assets
COPY --from=builder --chown=nginx:nginx /app/dist /usr/share/nginx/html

# Certificates are mounted at runtime (never baked into the image).
VOLUME ["/etc/nginx/tls"]

EXPOSE 8443

# Health probe over TLS. The self-signed or internal CA certificate is not
# trusted by wget, so verification is skipped for this loopback probe only.
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -q --no-check-certificate -O /dev/null https://127.0.0.1:8443/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
