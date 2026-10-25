"""Deployment boundary checks; no tunnel, model, or hosted database required."""

import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

NETLIFY_ORIGIN = "https://campusfizz.netlify.app"
LOCAL_ORIGIN = "http://localhost:5173"
LOCAL_ORIGINS = (
    "http://localhost:5173", "http://localhost:5174",
    "http://127.0.0.1:5173", "http://127.0.0.1:5174",
)
os.environ.update(
    SUPABASE_URL="https://deployment-test.supabase.co",
    SUPABASE_PUBLISHABLE_KEY="test-publishable-key",
    SUPABASE_SERVICE_ROLE_KEY="",
    SMTP_HOST="",
    CORS_ORIGINS=f"{NETLIFY_ORIGIN},{LOCAL_ORIGIN}",
    APP_URL=NETLIFY_ORIGIN,
)

from app.config import Settings
from app.main import app


class DeploymentTests(unittest.TestCase):
    def test_preflight_allows_netlify_and_local_bearer_requests(self):
        with TestClient(app) as client:
            for origin in (NETLIFY_ORIGIN, *LOCAL_ORIGINS):
                with self.subTest(origin=origin):
                    response = client.options(
                        "/api/tickets/1/classify",
                        headers={
                            "Origin": origin,
                            "Access-Control-Request-Method": "POST",
                            "Access-Control-Request-Headers": "authorization,content-type",
                        },
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.headers["access-control-allow-origin"], origin)
                    self.assertEqual(response.headers["access-control-allow-credentials"], "true")
                    allowed = response.headers["access-control-allow-headers"].lower()
                    self.assertIn("authorization", allowed)
                    self.assertIn("content-type", allowed)

    def test_unlisted_origin_is_not_granted_cors_access(self):
        with TestClient(app) as client:
            response = client.options(
                "/api/tickets/1/classify",
                headers={
                    "Origin": "https://unlisted.example",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "authorization,content-type",
                },
            )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_loopback_lookalikes_and_non_http_origins_are_rejected(self):
        for origin in (
            "http://localhost.attacker.example:5174", "http://127.0.0.1.attacker.example:5174",
            "http://localhost:5174@attacker.example", "http://localhost:5174/path",
            "https://localhost:5174", "http://0.0.0.0:5174", "http://[::1]:5174",
        ):
            with self.subTest(origin=origin), TestClient(app) as client:
                response = client.options("/api/tickets/1/classify", headers={
                    "Origin": origin, "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "authorization,content-type",
                })
                self.assertEqual(response.status_code, 400)
                self.assertNotIn("access-control-allow-origin", response.headers)

    def test_local_origin_regex_can_be_disabled_without_wildcard(self):
        cfg = Settings(_env_file=None, cors_allow_localhost=False,
                       supabase_url="https://deployment-test.supabase.co",
                       supabase_publishable_key="test-publishable-key")
        self.assertIsNone(cfg.cors_origin_regex)
        self.assertNotIn("*", cfg.cors_origin_list)

    def test_health_response_includes_allowed_origin(self):
        with TestClient(app) as client:
            response = client.get("/api/health", headers={"Origin": NETLIFY_ORIGIN})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], NETLIFY_ORIGIN)

    def test_deployment_environment_supports_frontend_links_and_local_ollama(self):
        with patch.dict(os.environ, {
            "CORS_ORIGINS": f"{NETLIFY_ORIGIN}, {LOCAL_ORIGIN}",
            "APP_URL": NETLIFY_ORIGIN,
            "OLLAMA_URL": "http://localhost:11434/api/chat",
            "OLLAMA_MODEL": "deepseek-r1:8b",
        }):
            cfg = Settings(
                _env_file=None,
                supabase_url="https://deployment-test.supabase.co",
                supabase_publishable_key="test-publishable-key",
            )
        self.assertEqual(cfg.cors_origin_list, [NETLIFY_ORIGIN, LOCAL_ORIGIN])
        self.assertEqual(cfg.app_url, NETLIFY_ORIGIN)
        self.assertEqual(cfg.ollama_url, "http://localhost:11434/api/chat")
        self.assertEqual(cfg.ollama_model, "deepseek-r1:8b")


if __name__ == "__main__":
    unittest.main()
