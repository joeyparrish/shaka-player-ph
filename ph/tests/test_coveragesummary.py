import datetime
import json
import pytest
from unittest.mock import MagicMock
from ph import gh
from ph.coveragesummary import CoverageSummary


@pytest.fixture(autouse=True)
def configure_gh(tmp_path):
    gh.configure(
        burst_limit=100,
        rate_limit_per_hour=4000,
        cache_folder=str(tmp_path),
        debug=False)
    yield


def _make_run(run_id, fetch_return=None):
    run = MagicMock()
    run.run_id = run_id
    run.start_time = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    run.event = "schedule"
    run.fetch_artifact.return_value = fetch_return
    return run


def test_coverage_summary_cache_hit_skips_fetch():
    run = _make_run(12345)
    gh.disk_cache.store("coverage-summary:12345", 0.75,
                        ttl_minutes=gh.LONG_TTL_MINUTES)

    results = CoverageSummary.get_all([run])

    run.fetch_artifact.assert_not_called()
    assert len(results) == 1
    assert results[0].line_coverage == 0.75
    assert results[0].start_time == run.start_time
    assert results[0].event == "schedule"


def test_coverage_summary_cache_miss_stores_result():
    coverage_json = json.dumps(
        {"total": {"lines": {"covered": 75, "total": 100}}}
    ).encode()
    run = _make_run(12345, fetch_return=coverage_json)

    results = CoverageSummary.get_all([run])

    run.fetch_artifact.assert_called_once_with("coverage", "coverage.json")
    assert len(results) == 1
    assert results[0].line_coverage == 0.75
    cached = gh.disk_cache.get("coverage-summary:12345")
    assert cached == 0.75


def test_coverage_summary_float_round_trip():
    run = _make_run(99999)
    gh.disk_cache.store("coverage-summary:99999", 1/3,
                        ttl_minutes=gh.LONG_TTL_MINUTES)

    results = CoverageSummary.get_all([run])

    assert len(results) == 1
    assert results[0].line_coverage == pytest.approx(1/3)
