import datetime
import json
import pytest
from unittest.mock import MagicMock
from ph import gh
from ph.pullrequest import PullRequest


@pytest.fixture(autouse=True)
def configure_gh(tmp_path):
    gh.configure(
        burst_limit=100,
        rate_limit_per_hour=4000,
        cache_folder=str(tmp_path),
        debug=False)
    yield


def _make_pr(head_sha):
    data = {
        "merged_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "number": 1,
        "merge_commit_sha": "deadbeef",
        "head": {"sha": head_sha},
    }
    pr = PullRequest("owner/repo", data)
    pr.changes = {"lib/player.js": [5]}
    return pr


def _make_run(run_id, head_sha, fetch_return=None):
    run = MagicMock()
    run.run_id = run_id
    run.head_sha = head_sha
    run.start_time = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    run.fetch_artifact.return_value = fetch_return
    return run


def _make_coverage_details_json():
    # Minimal Istanbul JSON with one statement at line 5, executed once.
    # _strip_git_dir converts "/repo/lib/player.js" -> "lib/player.js"
    # to match pr.changes keys.
    return json.dumps({
        "/repo/lib/player.js": {
            "statementMap": {
                "0": {
                    "start": {"line": 5, "column": 0},
                    "end": {"line": 5, "column": 10},
                }
            },
            "fnMap": {},
            "s": {"0": 1},
        }
    }).encode()


def test_incremental_coverage_cache_hit_skips_fetch():
    pr = _make_pr(head_sha="abc123")
    run = _make_run(run_id=42, head_sha="abc123")

    gh.disk_cache.store(
        "incremental-coverage:42",
        json.dumps({"covered": 10, "instrumented": 20, "incremental": 0.5}),
        ttl_minutes=gh.LONG_TTL_MINUTES)

    pr._load_incremental_coverage([run])

    run.fetch_artifact.assert_not_called()
    assert pr.num_covered_lines == 10
    assert pr.num_instrumented_lines == 20
    assert pr.incremental_coverage == 0.5


def test_incremental_coverage_cache_miss_stores_result():
    pr = _make_pr(head_sha="abc123")
    run = _make_run(run_id=42, head_sha="abc123",
                    fetch_return=_make_coverage_details_json())

    pr._load_incremental_coverage([run])

    run.fetch_artifact.assert_called_once_with("coverage", "coverage-details.json", cache=False)
    cached = gh.disk_cache.get("incremental-coverage:42")
    assert cached is not None
    data = json.loads(cached)
    assert data["covered"] == 1
    assert data["instrumented"] == 1
    assert data["incremental"] == 1.0


def test_incremental_coverage_zero_instrumented_lines():
    pr = _make_pr(head_sha="abc123")
    run = _make_run(run_id=42, head_sha="abc123")

    gh.disk_cache.store(
        "incremental-coverage:42",
        json.dumps({"covered": 0, "instrumented": 0, "incremental": None}),
        ttl_minutes=gh.LONG_TTL_MINUTES)

    pr._load_incremental_coverage([run])

    run.fetch_artifact.assert_not_called()
    assert pr.num_covered_lines == 0
    assert pr.num_instrumented_lines == 0
    assert pr.incremental_coverage is None
