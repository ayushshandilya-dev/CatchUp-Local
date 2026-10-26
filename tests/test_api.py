from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

from catchup_local.web.app import create_app


def wait_for_job(client: TestClient, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {"done", "error"}:
            return payload
        time.sleep(0.05)
    raise AssertionError("job did not finish in time")


def test_index_health_and_missing_sample(settings, tmp_path):
    fake = __import__("tests.conftest", fromlist=["FakeOllama"]).FakeOllama()
    app = create_app(settings=settings, client=fake, sample_path=tmp_path / "missing.json")
    with TestClient(app) as client:
        index = client.get("/")
        assert index.status_code == 200
        assert "CatchUp Local" in index.text
        health = client.get("/api/health").json()
        assert health["ollama"]["available"] is True
        assert health["sample_available"] is False
        assert client.get("/api/sample").status_code == 404


def test_precomputed_sample_is_served(settings, tmp_path):
    payload = {"briefing": {"summary": "cached"}, "messages": []}
    sample = tmp_path / "sample.json"
    sample.write_text(json.dumps(payload))
    fake = __import__("tests.conftest", fromlist=["FakeOllama"]).FakeOllama()
    app = create_app(settings=settings, client=fake, sample_path=sample)
    with TestClient(app) as client:
        response = client.get("/api/sample")
        assert response.status_code == 200
        assert response.json()["briefing"]["summary"] == "cached"


def test_upload_runs_a_job_and_returns_result(settings, sample_bytes):
    fake = __import__("tests.conftest", fromlist=["FakeOllama"]).FakeOllama()
    app = create_app(settings=settings, client=fake)
    with TestClient(app) as client:
        response = client.post(
            "/api/analyze",
            files={"file": ("sample_chat.txt", sample_bytes, "text/plain")},
        )
        assert response.status_code == 202
        job = wait_for_job(client, response.json()["job_id"])
        assert job["status"] == "done"
        assert job["result"]["briefing"]["summary"]
        assert job["result"]["parse"]["source_name"] == "sample_chat.txt"
        assert job["result"]["briefing"]["coverage"]["model_calls"] >= 1


def test_uploaded_file_is_not_read_from_disk(settings, sample_bytes):
    fake = __import__("tests.conftest", fromlist=["FakeOllama"]).FakeOllama()
    app = create_app(settings=settings, client=fake)
    with TestClient(app) as client:
        response = client.post(
            "/api/analyze",
            files={"file": ("../../escape.txt", sample_bytes, "text/plain")},
        )
        assert response.status_code == 202
        job = wait_for_job(client, response.json()["job_id"])
        assert job["result"]["parse"]["source_name"] == "escape.txt"


def test_rejects_wrong_extension_and_empty_upload(settings):
    fake = __import__("tests.conftest", fromlist=["FakeOllama"]).FakeOllama()
    app = create_app(settings=settings, client=fake)
    with TestClient(app) as client:
        wrong = client.post(
            "/api/analyze", files={"file": ("chat.csv", b"a,b", "text/csv")}
        )
        assert wrong.status_code == 400
        empty = client.post(
            "/api/analyze", files={"file": ("chat.txt", b"", "text/plain")}
        )
        assert empty.status_code == 400


def test_unknown_job_returns_404(settings):
    fake = __import__("tests.conftest", fromlist=["FakeOllama"]).FakeOllama()
    app = create_app(settings=settings, client=fake)
    with TestClient(app) as client:
        assert client.get("/api/jobs/not-a-job").status_code == 404
