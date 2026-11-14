"""Prometheus exposition, middleware counters, and provisioning file checks."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import create_app
from app.services.metrics import (
    DECISIONS,
    HTTP_REQUESTS,
    WS_CONNECTIONS,
    record_decision,
    render_metrics,
    ws_closed,
    ws_opened,
)

_REPO = Path(__file__).resolve().parents[2]


class MetricsEndpointTests(unittest.TestCase):
    def test_render_contains_namespaced_series(self):
        body = render_metrics().decode()
        for name in (
            "iv_http_requests_total",
            "iv_http_request_duration_seconds",
            "iv_inspection_latency_seconds",
            "iv_inspection_decisions_total",
            "iv_websocket_connections",
            "iv_event_bus_connected",
            "iv_worker_degraded",
            "iv_tasks_dead_lettered",
            "iv_database_ok",
        ):
            self.assertIn(name, body)

    def test_counter_increments_are_observed(self):
        before = (
            HTTP_REQUESTS.labels(method="GET", endpoint="/health", status="200")._value.get(),
        )
        record_decision("PASS", latency_seconds=0.12, camera_id=3)
        after_http = HTTP_REQUESTS.labels(method="GET", endpoint="/health", status="200")._value.get()
        self.assertEqual(after_http, before[0])  # untouched by decision recording
        body = render_metrics().decode()
        self.assertRegex(body, r'iv_inspection_decisions_total\{decision="PASS"\} \d+')
        self.assertRegex(body, r'iv_inspection_latency_seconds_count\{camera_id="3"\} \d+')

    def test_ws_gauge_tracks_open_and_close(self):
        ws_opened("test_endpoint")
        self.assertEqual(WS_CONNECTIONS.labels(endpoint="test_endpoint")._value.get(), 1)
        ws_closed("test_endpoint")
        self.assertEqual(WS_CONNECTIONS.labels(endpoint="test_endpoint")._value.get(), 0)

    def test_middleware_counts_requests(self):
        with patch.object(settings, "APP_ENV", "test"), TestClient(create_app()) as client:
            client.get("/health")
            client.get("/definitely-not-a-route")
        body = render_metrics().decode()
        self.assertIn('iv_http_requests_total{endpoint="/health",method="GET",status="200"}', body)
        self.assertIn('iv_http_requests_total{endpoint="/definitely-not-a-route",method="GET",status="404"}', body)

    def test_metrics_endpoint_served(self):
        with patch.object(settings, "APP_ENV", "test"), TestClient(create_app()) as client:
            response = client.get("/metrics")
        self.assertEqual(response.status_code, 200)
        self.assertIn("iv_", response.text)
        self.assertTrue(response.text.startswith("#"))


class ProvisioningFileTests(unittest.TestCase):
    """The Compose-provisioned configs must parse before any container runs."""

    def test_prometheus_config_parses(self):
        data = yaml.safe_load((_REPO / "deploy" / "prometheus" / "prometheus.yml").read_text(encoding="utf-8"))
        jobs = {job["job_name"] for job in data["scrape_configs"]}
        self.assertIn("industrialvision-backend", jobs)
        self.assertIn(data["rule_files"][0], "/etc/prometheus/alerts.yml")

    def test_alert_rules_parse_and_cover_required_alerts(self):
        data = yaml.safe_load((_REPO / "deploy" / "prometheus" / "alerts.yml").read_text(encoding="utf-8"))
        alerts = {
            rule["alert"]
            for group in data["groups"]
            for rule in group["rules"]
        }
        for required in ("DatabaseDown", "EventBusDisconnected", "WorkerDegraded", "HighHTTPTerrorRate"):
            self.assertIn(required, alerts)

    def test_grafana_dashboard_json_parses(self):
        data = json.loads((_REPO / "deploy" / "grafana" / "dashboards" / "industrialvision.json").read_text(encoding="utf-8"))
        self.assertEqual(data["title"], "IndustrialVision — Platform Overview")
        queries = [
            t["expr"]
            for panel in data["panels"]
            for t in panel.get("targets", [])
        ]
        self.assertTrue(any("iv_inspection_decisions_total" in q for q in queries))
        self.assertTrue(any("iv_websocket_connections" in q for q in queries))

    def test_grafana_datasource_points_at_prometheus(self):
        data = yaml.safe_load(
            (_REPO / "deploy" / "grafana" / "provisioning" / "datasources" / "datasource.yml").read_text(encoding="utf-8")
        )
        self.assertEqual(data["datasources"][0]["url"], "http://prometheus:9090")


if __name__ == "__main__":
    unittest.main()
