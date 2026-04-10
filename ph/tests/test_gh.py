import json
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


def test_api_multiple_sha_commit_uses_long_ttl(tmp_path):
    # Base URL without paging params; api_multiple() will add them
    base_url = "/repos/owner/repo/commits/abcdef1234567890abcdef1234567890abcdef12/files"
    page1 = json.dumps([{"filename": "lib/player.js", "patch": ""}])
    page2 = json.dumps([])
    with patch("ph.shell.run_command", side_effect=[page1, page2]):
        gh.api_multiple(base_url)
    import time, hashlib, os
    # The stored key will be base_url + "?page_size=100&page=1"
    stored_url = base_url + "?page_size=100&page=1"
    sha = hashlib.sha256(stored_url.encode("utf8")).hexdigest()
    path = os.path.join(str(tmp_path), sha + ".json")
    with open(path) as f:
        data = json.load(f)
    assert data["expires_at"] > time.time() + 86400 * 99


def test_api_single_completed_run_uses_long_ttl(tmp_path):
    url = "/repos/owner/repo/actions/runs/99999999/attempts/1"
    run_data = {"id": 99999999, "conclusion": "success", "head_sha": "abc"}
    with patch("ph.shell.run_command", return_value=json.dumps(run_data)):
        gh.api_single(url)
    import time, hashlib, os
    sha = hashlib.sha256(url.encode("utf8")).hexdigest()
    path = os.path.join(str(tmp_path), sha + ".json")
    with open(path) as f:
        data = json.load(f)
    assert data["expires_at"] > time.time() + 86400 * 99


def test_api_single_in_progress_run_uses_default_ttl(tmp_path):
    url = "/repos/owner/repo/actions/runs/88888888/attempts/1"
    run_data = {"id": 88888888, "conclusion": None, "head_sha": "def"}
    with patch("ph.shell.run_command", return_value=json.dumps(run_data)):
        gh.api_single(url)
    import time, hashlib, os
    sha = hashlib.sha256(url.encode("utf8")).hexdigest()
    path = os.path.join(str(tmp_path), sha + ".json")
    with open(path) as f:
        data = json.load(f)
    # Default TTL is 120 minutes; expires_at should be ~2 hours from now
    assert data["expires_at"] < time.time() + 86400 * 99


def test_http_head_caches_headers(tmp_path):
    url = "https://ajax.googleapis.com/ajax/libs/shaka-player/4.3.5/shaka-player.compiled.js"
    fake_headers = {"last-modified": "Wed, 01 Jan 2025 00:00:00 GMT", "content-type": "application/javascript"}
    mock_response = MagicMock()
    mock_response.headers = fake_headers
    with patch("requests.head", return_value=mock_response) as mock_head:
        result1 = gh.http_head(url)
        result2 = gh.http_head(url)  # second call should hit cache
    assert result1["last-modified"] == "Wed, 01 Jan 2025 00:00:00 GMT"
    assert result2["last-modified"] == "Wed, 01 Jan 2025 00:00:00 GMT"
    assert mock_head.call_count == 1  # only one real HTTP request


def test_http_head_uses_long_ttl(tmp_path):
    url = "https://ajax.googleapis.com/ajax/libs/shaka-player/4.3.5/shaka-player.compiled.js"
    mock_response = MagicMock()
    mock_response.headers = {"last-modified": "Wed, 01 Jan 2025 00:00:00 GMT"}
    with patch("requests.head", return_value=mock_response):
        gh.http_head(url)
    import time, hashlib, os
    sha = hashlib.sha256(url.encode("utf8")).hexdigest()
    path = os.path.join(str(tmp_path), sha + ".json")
    with open(path) as f:
        data = json.load(f)
    assert data["expires_at"] > time.time() + 86400 * 99
