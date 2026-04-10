import json
import time
import hashlib
import os
import pytest
from unittest.mock import patch, call
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


FAKE_GIT_LOG = "1700000000 tag: v4.3.5\n1699900000 \n1699800000 tag: v4.3.4\n"


def _cache_path(tmp_path, key):
    sha = hashlib.sha256(key.encode("utf8")).hexdigest()
    return os.path.join(str(tmp_path), sha + ".json")


def test_commitlog_tag_ref_is_cached(tmp_path):
    from ph.commitlog import CommitLog
    CommitLog.get_all.cache_clear()
    with patch("ph.shell.run_command", return_value=FAKE_GIT_LOG):
        CommitLog.get_all("owner/repo", "v4.3.5", None)
    key = "commitlog:owner/repo:v4.3.5"
    path = _cache_path(tmp_path, key)
    assert os.path.exists(path)
    with open(path) as f:
        data = json.load(f)
    assert data["expires_at"] > time.time() + 86400 * 99


def test_commitlog_branch_ref_uses_default_ttl(tmp_path):
    from ph.commitlog import CommitLog
    CommitLog.get_all.cache_clear()
    with patch("ph.shell.run_command", return_value=FAKE_GIT_LOG):
        CommitLog.get_all("owner/repo", "v4.3.x", None)
    key = "commitlog:owner/repo:v4.3.x"
    path = _cache_path(tmp_path, key)
    assert os.path.exists(path)
    with open(path) as f:
        data = json.load(f)
    # Default TTL ~120 min; not the long 100-day TTL
    assert data["expires_at"] < time.time() + 86400 * 99


def test_commitlog_cache_hit_skips_git(tmp_path):
    from ph.commitlog import CommitLog
    CommitLog.get_all.cache_clear()
    # Populate cache
    with patch("ph.shell.run_command", return_value=FAKE_GIT_LOG) as mock_cmd:
        CommitLog.get_all("owner/repo", "v4.3.5", None)
        first_call_count = mock_cmd.call_count

    CommitLog.get_all.cache_clear()
    with patch("ph.shell.run_command", return_value=FAKE_GIT_LOG) as mock_cmd:
        CommitLog.get_all("owner/repo", "v4.3.5", None)
        # Cache hit: git commands should not be called
        assert mock_cmd.call_count == 0


def test_commitlog_parses_correctly(tmp_path):
    from ph.commitlog import CommitLog
    CommitLog.get_all.cache_clear()
    with patch("ph.shell.run_command", return_value=FAKE_GIT_LOG):
        logs = CommitLog.get_all("owner/repo", "v4.3.5", None)
    assert len(logs) == 3
    assert logs[0].tags == ["v4.3.5"]
    assert logs[1].tags == []
    assert logs[2].tags == ["v4.3.4"]
