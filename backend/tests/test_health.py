"""Public health endpoint metadata and database readiness."""

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.api.v1.routers import health as health_router
from app.core.config import settings
from app.main import create_app
from app.services import event_bus as event_bus_module


class HealthEndpointTests(unittest.TestCase):
    def test_public_health_preserves_version_metadata(self):
        original = health_router._port_open
        self.addCleanup(setattr, health_router, '_port_open', original)
        health_router._port_open = lambda *_args, **_kwargs: True
        # APP_ENV=test keeps the demo live feed lifespan startup disabled so
        # health checks never write fixture rows into the working database.
        with patch.object(settings, 'APP_ENV', 'test'), TestClient(create_app()) as client:
            response = client.get('/health')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['version'], '0.1.0')
        self.assertEqual(body['database'], 'ok')
        self.assertEqual(body['status'], 'ok')

    def test_health_reports_event_bus_mode(self):
        original = health_router._port_open
        self.addCleanup(setattr, health_router, '_port_open', original)
        health_router._port_open = lambda *_args, **_kwargs: True
        # Default dev/test config: no REDIS_URL -> explicit in-process memory mode.
        self.assertEqual(event_bus_module.event_bus.mode, 'memory')
        with patch.object(settings, 'APP_ENV', 'test'), TestClient(create_app()) as client:
            body = client.get('/health').json()
        self.assertEqual(body['event_bus'], {'mode': 'memory', 'connected': True, 'configured': False})

    def test_health_is_degraded_when_configured_bus_is_disconnected(self):
        original = health_router._port_open
        self.addCleanup(setattr, health_router, '_port_open', original)
        original_bus = health_router.event_bus
        self.addCleanup(setattr, health_router, 'event_bus', original_bus)

        class DisconnectedBus:
            mode = 'redis'
            connected = False

        health_router.event_bus = DisconnectedBus()
        health_router._port_open = lambda *_args, **_kwargs: True
        with patch.object(settings, 'APP_ENV', 'test'), TestClient(create_app()) as client:
            body = client.get('/health').json()
        self.assertEqual(body['event_bus']['connected'], False)
        self.assertEqual(body['status'], 'degraded')


if __name__ == '__main__':
    unittest.main()
