# IndustrialVision AI — TLS Certificate Directory
# Place your server.crt and server.key here before running docker compose.
# These files are intentionally excluded from version control (.gitignore).
# See docs/deployment-tls.md for how to obtain or generate certificates.

# For development/testing only — generate a self-signed certificate:
#
#   openssl req -x509 -newkey rsa:4096 -sha256 -days 365 -nodes \
#     -keyout deploy/tls/server.key -out deploy/tls/server.crt \
#     -subj "/CN=industrialvision-local"
#
# For production use a certificate from your internal CA or a public CA.
