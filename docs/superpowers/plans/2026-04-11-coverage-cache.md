# Coverage Output Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cache the computed output of coverage parsing so warm runs skip artifact fetching and Istanbul JSON parsing entirely.

**Architecture:** Two call sites get cache wrappers. `CoverageSummary.get_all()` caches a `line_coverage` float per run under `coverage-summary:{run_id}`. `PullRequest._load_incremental_coverage()` caches `num_covered_lines`, `num_instrumented_lines`, and `incremental_coverage` per PR under `incremental-coverage:{run_id}`. Both use `LONG_TTL_MINUTES` (100 days). `CoverageDetails` and `CoverageSummary` constructors are unchanged.

**Tech Stack:** Python 3, `json` (stdlib), `gh.disk_cache` (existing `DiskCache` instance in `ph/ph/gh.py`).

---

### Task 1: Profile CoverageDetails (no commit)

**Files:**
- Modify temporarily: `ph/ph/coveragedetails.py`

Profile the split between `json.loads()` and the set-operation loop to confirm where the time goes. Remove the instrumentation before moving on -- this task produces no commit.

- [ ] **Step 1: Add timing instrumentation to `CoverageDetails.__init__`**

In `ph/ph/coveragedetails.py`, modify `__init__` to add timing around the two phases:

```python
  def __init__(self, file_data):
    import sys, time
    t0 = time.perf_counter()
    json_data = json.loads(file_data)
    t1 = time.perf_counter()

    self.files = {}

    # ... (all existing code unchanged) ...

    # At the very end of __init__, after the executed_lines loop:
    t2 = time.perf_counter()
    print(f"CoverageDetails: json={t1-t0:.3f}s sets={t2-t1:.3f}s "
          f"total={t2-t0:.3f}s bytes={len(file_data)}",
          file=sys.stderr)
```

The `t2` print goes after the last `self.files[path] = ...` assignment (line 128 in the current file).

- [ ] **Step 2: Run on a warm cache and record the split**

```bash
cd /path/to/shaka-player-ph
./ph/update-all.sh 2>&1 | grep CoverageDetails | head -5
```

Record the typical `json=` and `sets=` values. Expected: one line per coverage-details.json processed (~285 lines for 90d).

- [ ] **Step 3: Remove instrumentation**

Revert `ph/ph/coveragedetails.py` to its original state -- remove the `import sys, time`, `t0`, `t1`, `t2` lines and the print statement. Do **not** commit.

---

### Task 2: CoverageSummary cache -- tests

**Files:**
- Create: `ph/tests/test_coveragesummary.py`

- [ ] **Step 1: Write the tests**

Create `ph/tests/test_coveragesummary.py` with this full content:

```python
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
    gh.disk_cache.store("coverage-summary:12345", json.dumps(0.75),
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
    assert json.loads(cached) == 0.75


def test_coverage_summary_float_round_trip():
    key = "coverage-summary:99999"
    value = 0.8423
    gh.disk_cache.store(key, json.dumps(value), ttl_minutes=gh.LONG_TTL_MINUTES)
    assert json.loads(gh.disk_cache.get(key)) == value
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd ph && python -m pytest tests/test_coveragesummary.py -v
```

Expected: `test_coverage_summary_cache_hit_skips_fetch` and `test_coverage_summary_cache_miss_stores_result` fail (cache logic not yet implemented); `test_coverage_summary_float_round_trip` passes (only tests DiskCache).

---

### Task 3: CoverageSummary cache -- implementation

**Files:**
- Modify: `ph/ph/coveragesummary.py`

- [ ] **Step 1: Add `gh` import, `from_line_coverage` classmethod, and cache logic in `get_all`**

Replace the entire content of `ph/ph/coveragesummary.py` with:

```python
# Shaka Player Project Health Metrics
# Copyright 2023 Google LLC
# SPDX-License-Identifier: Apache-2.0

import json

from . import gh


class CoverageSummary(object):
  def __init__(self, start_time, event, file_data):
    self.start_time = start_time
    self.event = event
    json_data = json.loads(file_data)

    total_data = json_data["total"]
    lines_covered = total_data["lines"]["covered"]
    lines_total = total_data["lines"]["total"]
    if lines_total:
      line_coverage = lines_covered / lines_total
    else:
      line_coverage = 1

    self.line_coverage = line_coverage

  @classmethod
  def from_line_coverage(cls, start_time, event, line_coverage):
    obj = cls.__new__(cls)
    obj.start_time = start_time
    obj.event = event
    obj.line_coverage = line_coverage
    return obj

  def serializable(self):
    return {
      "start": self.start_time.timestamp(),
      "event": self.event,
      "line_coverage": self.line_coverage,
    }

  @staticmethod
  def get_all(coverage_runs):
    results = []

    for run in coverage_runs:
      key = "coverage-summary:{}".format(run.run_id)
      cached = gh.disk_cache.get(key)
      if cached is not None:
        summary = CoverageSummary.from_line_coverage(
            run.start_time, run.event, json.loads(cached))
        results.append(summary)
        continue

      file_data = run.fetch_artifact("coverage", "coverage.json")
      if file_data is None:
        continue
      summary = CoverageSummary(run.start_time, run.event, file_data)
      gh.disk_cache.store(key, json.dumps(summary.line_coverage),
                          ttl_minutes=gh.LONG_TTL_MINUTES)
      results.append(summary)

    return sorted(results, key=lambda r: r.start_time)
```

- [ ] **Step 2: Run the CoverageSummary tests**

```bash
cd ph && python -m pytest tests/test_coveragesummary.py -v
```

Expected: all 3 pass.

- [ ] **Step 3: Run the full test suite**

```bash
cd ph && python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add ph/ph/coveragesummary.py ph/tests/test_coveragesummary.py
git commit -m "Cache CoverageSummary line_coverage per run (100d TTL)"
```

---

### Task 4: PR incremental coverage cache -- tests

**Files:**
- Create: `ph/tests/test_pullrequest.py`

- [ ] **Step 1: Write the tests**

Create `ph/tests/test_pullrequest.py` with this full content:

```python
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

    run.fetch_artifact.assert_called_once_with("coverage", "coverage-details.json")
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
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd ph && python -m pytest tests/test_pullrequest.py -v
```

Expected: `test_incremental_coverage_cache_hit_skips_fetch` and `test_incremental_coverage_cache_miss_stores_result` fail; `test_incremental_coverage_zero_instrumented_lines` may fail or pass depending on guard behavior.

---

### Task 5: PR incremental coverage cache -- implementation

**Files:**
- Modify: `ph/ph/pullrequest.py`

- [ ] **Step 1: Add `import json` and cache logic to `_load_incremental_coverage`**

In `ph/ph/pullrequest.py`, add `import json` at line 5 (after `import dateutil.parser`):

```python
import dateutil.parser
import json
```

Then replace `_load_incremental_coverage` (lines 80-117) with:

```python
  def _load_incremental_coverage(self, runs):
    if self.num_covered_lines is not None:
      # Already loaded.
      return

    run = self._matching_workflow_run(runs)
    if run is None:
      # No matching run.
      return

    key = "incremental-coverage:{}".format(run.run_id)
    cached = gh.disk_cache.get(key)
    if cached is not None:
      data = json.loads(cached)
      self.num_covered_lines = data["covered"]
      self.num_instrumented_lines = data["instrumented"]
      self.incremental_coverage = data["incremental"]
      return

    file_data = run.fetch_artifact("coverage", "coverage-details.json")
    if file_data is None:
      # No coverage details available.
      return

    coverage_details = CoverageDetails(file_data)

    self.num_covered_lines = 0
    self.num_instrumented_lines = 0

    for path in self.changes:
      if path in coverage_details.files:
        changed_lines = self.changes[path]
        instrumented_lines = coverage_details.files[path]["instrumented"]
        executed_lines = coverage_details.files[path]["executed"]

        for line in changed_lines:
          # Only count the instrumented lines, not whitespace or comments.
          if line in instrumented_lines:
            self.num_instrumented_lines += 1
            if line in executed_lines:
              self.num_covered_lines += 1

    if self.num_instrumented_lines == 0:
      self.incremental_coverage = None
    else:
      self.incremental_coverage = (
          self.num_covered_lines / self.num_instrumented_lines)

    gh.disk_cache.store(key,
        json.dumps({"covered": self.num_covered_lines,
                    "instrumented": self.num_instrumented_lines,
                    "incremental": self.incremental_coverage}),
        ttl_minutes=gh.LONG_TTL_MINUTES)
```

- [ ] **Step 2: Run the PR tests**

```bash
cd ph && python -m pytest tests/test_pullrequest.py -v
```

Expected: all 3 pass.

- [ ] **Step 3: Run the full test suite**

```bash
cd ph && python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add ph/ph/pullrequest.py ph/tests/test_pullrequest.py
git commit -m "Cache incremental coverage output per PR (100d TTL)"
```

---

### Task 6: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update the Performance Profile section**

In `CLAUDE.md`, replace the entire "Performance Profile (warm run)" section:

```markdown
## Performance Profile (warm run)

Coverage parsing accounts for ~84% of warm 90d runtime (~5 of 6 minutes).
It is pure CPU (Python JSON parsing + set operations on CoverageDetails).
The next optimization target is caching the *output* of coverage computation
(e.g. serialized CoverageSummary/CoverageDetails objects) rather than the
raw JSON bytes, so parsing is done once per run and reused across invocations.
```

With:

```markdown
## Performance Profile (warm run)

Before coverage output caching, coverage parsing accounted for ~84% of warm
90d runtime (~5 of 6 minutes). With caching in place, warm runs skip artifact
fetching and Istanbul JSON parsing entirely for both coverage paths:
`CoverageSummary.get_all()` and `PullRequest._load_incremental_coverage()`.
```

- [ ] **Step 2: Update the disk cache bullet in Key Behaviors**

Find this text in the "Key Behaviors" disk cache bullet:

```
  Long TTL (100 days) for immutable objects: CDN headers (via HEAD request),
  completed workflow run metadata, PR commit data (SHA-keyed URLs), and
  CommitLog data for tag refs.
```

Replace with:

```
  Long TTL (100 days) for immutable objects: CDN headers (via HEAD request),
  completed workflow run metadata, PR commit data (SHA-keyed URLs),
  CommitLog data for tag refs, and computed coverage output
  (`coverage-summary:{run_id}`, `incremental-coverage:{run_id}`).
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "Update CLAUDE.md: document coverage output caching"
```

---

### Task 7 (Conditional): Skip caching coverage artifact ZIPs

**Prerequisite:** Only do this if Tasks 2-5 are complete. First check the cache size:

```bash
du -sh ~/.cache/shaka-player-ph/
```

If the cache is already small (under 200 MB), skip this task -- the ZIPs are likely already expired and not the problem. If it's large (hundreds of MB to GB), proceed.

**Files:**
- Modify: `ph/ph/workflowrun.py:56-78`
- Modify: `ph/ph/coveragesummary.py`
- Modify: `ph/ph/pullrequest.py`

**Purpose:** Coverage artifact ZIPs are now one-time-use -- once computed output is cached under `coverage-summary:{run_id}` or `incremental-coverage:{run_id}`, the ZIP is never needed again. Passing `cache=False` prevents storing it.

- [ ] **Step 1: Add `cache` parameter to `WorkflowRun.fetch_artifact`**

In `ph/ph/workflowrun.py`, replace `fetch_artifact` (lines 56-78):

```python
  def fetch_artifact(self, name, filename, cache=True):
    results = gh.api_multiple(self.artifacts_url, "artifacts")

    zip_data = None
    for data in results:
      if data["name"] == name:
        try:
          zip_data = gh.api_raw(data["archive_download_url"], cache=cache)
          break
        except RuntimeError as e:
          print(
            'Failed to fetch artifact for run from {}'.format(self.start_time),
            file=sys.stderr)
          print(e, file=sys.stderr)

    if zip_data is None:
      return None

    with zipfile.ZipFile(io.BytesIO(zip_data), 'r') as f:
      try:
        return f.read(filename)
      except KeyError as e:
        return None
```

- [ ] **Step 2: Pass `cache=False` at coverage call sites**

In `ph/ph/coveragesummary.py`, change:
```python
      file_data = run.fetch_artifact("coverage", "coverage.json")
```
to:
```python
      file_data = run.fetch_artifact("coverage", "coverage.json", cache=False)
```

In `ph/ph/pullrequest.py`, change:
```python
    file_data = run.fetch_artifact("coverage", "coverage-details.json")
```
to:
```python
    file_data = run.fetch_artifact("coverage", "coverage-details.json", cache=False)
```

- [ ] **Step 3: Run the full test suite**

```bash
cd ph && python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 4: Measure cache size after a run**

```bash
./ph/update-all.sh 2>/dev/null && du -sh ~/.cache/shaka-player-ph/
```

Record the new size and compare with the pre-task measurement.

- [ ] **Step 5: Commit**

```bash
git add ph/ph/workflowrun.py ph/ph/coveragesummary.py ph/ph/pullrequest.py
git commit -m "Skip caching coverage ZIPs (output is cached instead)"
```
