import io
import json
import zipfile
import pytest
from unittest.mock import patch, MagicMock
from ph import gh
from ph.diskcache import DiskCache
from ph.ratelimit import RateLimit


@pytest.fixture(autouse=True)
def configure_gh(tmp_path):
    gh.configure(
        burst_limit=100,
        rate_limit_per_hour=4000,
        cache_folder=str(tmp_path),
        cache_minutes=120,
        debug=False)
    yield


def _make_zip(filename, content):
    """Create a ZIP in memory containing one file."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(filename, content)
    return buf.getvalue()


def _make_run_data():
    return {
        "id": 12345,
        "head_sha": "abc123",
        "event": "schedule",
        "created_at": "2026-01-01T00:00:00Z",
        "run_started_at": "2026-01-01T00:01:00Z",
        "updated_at": "2026-01-01T01:00:00Z",
        "artifacts_url": "https://api.github.com/repos/owner/repo/actions/runs/12345/artifacts",
        "logs_url": "https://api.github.com/repos/owner/repo/actions/runs/12345/logs",
        "html_url": "https://github.com/owner/repo/actions/runs/12345",
        "conclusion": "success",
        "previous_attempt_url": None,
    }


def test_fetch_artifact_returns_file_content(tmp_path):
    from ph.workflowrun import WorkflowRun
    run = WorkflowRun(_make_run_data())
    archive_url = "https://api.github.com/repos/owner/repo/actions/artifacts/99/zip"
    zip_bytes = _make_zip("coverage-details.json", b'{"total": {}}')

    with patch("ph.gh.api_multiple", return_value=[{"name": "coverage", "archive_download_url": archive_url}]):
        with patch("ph.gh.api_raw", return_value=zip_bytes):
            result = run.fetch_artifact("coverage", "coverage-details.json")

    assert result == b'{"total": {}}'


def test_fetch_artifact_caches_extracted_bytes(tmp_path):
    from ph.workflowrun import WorkflowRun
    run = WorkflowRun(_make_run_data())
    archive_url = "https://api.github.com/repos/owner/repo/actions/artifacts/99/zip"
    zip_bytes = _make_zip("coverage-details.json", b'{"total": {}}')

    with patch("ph.gh.api_multiple", return_value=[{"name": "coverage", "archive_download_url": archive_url}]):
        with patch("ph.gh.api_raw", return_value=zip_bytes) as mock_raw:
            run.fetch_artifact("coverage", "coverage-details.json")
            run.fetch_artifact("coverage", "coverage-details.json")  # second call

    # api_raw should only be called once (second call hits cache)
    assert mock_raw.call_count == 1


def test_fetch_artifact_cache_entry_has_long_ttl(tmp_path):
    import time, hashlib, os
    from ph.workflowrun import WorkflowRun
    run = WorkflowRun(_make_run_data())
    archive_url = "https://api.github.com/repos/owner/repo/actions/artifacts/99/zip"
    zip_bytes = _make_zip("coverage-details.json", b'{"total": {}}')
    cache_key = archive_url + "#coverage-details.json"

    with patch("ph.gh.api_multiple", return_value=[{"name": "coverage", "archive_download_url": archive_url}]):
        with patch("ph.gh.api_raw", return_value=zip_bytes):
            run.fetch_artifact("coverage", "coverage-details.json")

    sha = hashlib.sha256(cache_key.encode("utf8")).hexdigest()
    path = os.path.join(str(tmp_path), sha + ".json")
    with open(path) as f:
        data = json.load(f)
    assert data["expires_at"] > time.time() + 86400 * 99
    # Stored as text (not base64 bytes) to avoid encoding overhead
    assert "text" in data
    assert "bytes" not in data


def test_fetch_artifact_returns_none_for_missing_file(tmp_path):
    from ph.workflowrun import WorkflowRun
    run = WorkflowRun(_make_run_data())
    archive_url = "https://api.github.com/repos/owner/repo/actions/artifacts/99/zip"
    zip_bytes = _make_zip("other-file.json", b"{}")

    with patch("ph.gh.api_multiple", return_value=[{"name": "coverage", "archive_download_url": archive_url}]):
        with patch("ph.gh.api_raw", return_value=zip_bytes):
            result = run.fetch_artifact("coverage", "coverage-details.json")

    assert result is None


def test_fetch_artifact_artifact_listing_uses_long_ttl(tmp_path):
    """Artifact listings for completed runs should use long TTL."""
    import time, hashlib, os
    from ph.workflowrun import WorkflowRun
    run = WorkflowRun(_make_run_data())
    archive_url = "https://api.github.com/repos/owner/repo/actions/artifacts/99/zip"
    zip_bytes = _make_zip("coverage-details.json", b'{"total": {}}')

    # api_multiple appends ?page_size=100 then &page=N
    artifacts_page1_url = run.artifacts_url + "?page_size=100&page=1"

    # shell.run_command is called by _api_base for each page
    page1_response = json.dumps(
        {"artifacts": [{"name": "coverage", "archive_download_url": archive_url}]})
    page2_response = json.dumps({"artifacts": []})

    with patch("ph.shell.run_command",
               side_effect=[page1_response, page2_response]):
        with patch("ph.gh.api_raw", return_value=zip_bytes):
            run.fetch_artifact("coverage", "coverage-details.json")

    sha = hashlib.sha256(artifacts_page1_url.encode("utf8")).hexdigest()
    path = os.path.join(str(tmp_path), sha + ".json")
    with open(path) as f:
        data = json.load(f)
    assert data["expires_at"] > time.time() + 86400 * 99
