"""Public health endpoint metadata and database readiness."""

import unittest
from fastapi.testclient import TestClient
from app.api.v1.routers import health as health_router
from app.main import create_app


class HealthEndpointTests(unittest.TestCase):
    def test_public_health_preserves_version_metadata(self):
        original = health_router._port_open
        self.addCleanup(setattr, health_router, '_port_open', original)
        health_router._port_open = lambda *_args, **_kwargs: True
        with TestClient(create_app()) as client:
            response = client.get('/health')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['version'], '0.1.0')
        self.assertEqual(body['database'], 'ok')
        self.assertEqual(body['status'], 'ok')


if __name__ == '__main__':
    unittest.main()
