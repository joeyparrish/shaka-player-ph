# Coverage Output Cache Design

Date: 2026-04-11

## Goal

Cache the computed output of coverage processing so that expensive artifact
fetching and parsing is skipped on warm runs.

On a warm 90-day run, 285 `coverage-details.json` files and 70 `coverage.json`
files are read from disk cache and parsed. Coverage processing accounts for
~84% of warm 90d runtime (~5 of 6 minutes). Caching computed outputs eliminates
that cost on subsequent runs.

---

## Background

`update-all.sh` runs `main.py` three times (90d, 30d, 7d). The disk cache
persists raw artifact bytes across runs and across daily CI invocations, so
the bytes are already available without a network call on warm runs. The
bottleneck is downstream of the bytes: Python JSON parsing and set operations
in `CoverageDetails.__init__()`.

Two callers construct coverage objects from artifact data:

- `CoverageSummary.get_all()` in `coveragesummary.py` -- one `coverage.json`
  per coverage workflow run (~70 files in 90d). Parsing is lightweight (two
  numbers from a totals object); caching is included for completeness.
- `PullRequest._load_incremental_coverage()` in `pullrequest.py` -- one
  `coverage-details.json` per merged PR with a matching workflow run (~285
  files in 90d). Parsing is expensive: Istanbul JSON with full source maps,
  followed by set operations to compute instrumented/executed line sets,
  followed by intersection with the PR's changed lines.

---

## Profiling Step (First Task)

Before implementing caching, instrument `CoverageDetails.__init__()` to split
the measured cost between `json.loads()` and the set-operation loop:

```python
import time
t0 = time.perf_counter()
json_data = json.loads(file_data)
t1 = time.perf_counter()
# ... set operations ...
t2 = time.perf_counter()
print(f"CoverageDetails: parse={t1-t0:.3f}s sets={t2-t1:.3f}s", file=sys.stderr)
```

Run once on a warm cache, capture the split, then remove the instrumentation
before committing caching logic. The result confirms where the win comes from
and informs whether both phases are worth bypassing.

---

## Architecture

No changes to `CoverageDetails` or `CoverageSummary` constructors. One
classmethod added to `CoverageSummary`. Caching lives in the two call sites
that already hold `run.run_id`.

Both call sites do an explicit check/call/store inline -- there is no shared
helper, because neither call site benefits from one: the CoverageSummary call
site needs `start_time` and `event` from `run` (not in the cache), and the
incremental coverage call site's serialization is non-trivial.

---

## Cache Entries

| Object | Key | Serialized format | TTL |
|---|---|---|---|
| `CoverageSummary.line_coverage` | `coverage-summary:{run_id}` | JSON float, e.g. `0.8423` | 100 days |
| PR incremental coverage | `incremental-coverage:{run_id}` | JSON object, e.g. `{"covered": 12, "instrumented": 15}` | 100 days |

### PR incremental coverage serialization

The final output of `_load_incremental_coverage` is two ints:
`num_covered_lines` and `num_instrumented_lines`. `incremental_coverage` is
derived from those two and is not stored separately.

Cached as: `json.dumps({"covered": num_covered_lines, "instrumented": num_instrumented_lines})`

On load: reconstruct both ints, then recompute `incremental_coverage` using
the same logic as the live path (`covered / instrumented` if instrumented > 0,
else `None`).

Only cache when the artifact was found and processed (i.e. when
`num_instrumented_lines` is not None). If no matching run or no artifact
exists, don't cache -- allow a retry next run in case it becomes available.

### TTL rationale

100 days (`LONG_TTL_MINUTES`), same as other completed-run data. A workflow
run's coverage output is immutable once the run completes.

---

## Call Site Changes

### `CoverageSummary.get_all()`

Add a classmethod to `CoverageSummary` for reconstruction from a cached float:

```python
@classmethod
def from_line_coverage(cls, start_time, event, line_coverage):
    obj = cls.__new__(cls)
    obj.start_time = start_time
    obj.event = event
    obj.line_coverage = line_coverage
    return obj
```

In `get_all()`:

```python
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
```

### `PullRequest._load_incremental_coverage()`

```python
run = self._matching_workflow_run(runs)
if run is None:
    return

key = "incremental-coverage:{}".format(run.run_id)
cached = gh.disk_cache.get(key)
if cached is not None:
    data = json.loads(cached)
    self.num_covered_lines = data["covered"]
    self.num_instrumented_lines = data["instrumented"]
    if self.num_instrumented_lines == 0:
        self.incremental_coverage = None
    else:
        self.incremental_coverage = (
            self.num_covered_lines / self.num_instrumented_lines)
    return

file_data = run.fetch_artifact("coverage", "coverage-details.json")
if file_data is None:
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
                "instrumented": self.num_instrumented_lines}),
    ttl_minutes=gh.LONG_TTL_MINUTES)
```

---

## Error Handling

No new error handling required. `DiskCache.get()` returns `None` on any read
failure; both call sites already handle `None` by falling through to the parse
path. `DiskCache.store()` silently swallows write failures. Cache misses are
always safe.

---

## Testing

### `test_coveragesummary.py`

- `test_coverage_summary_cache_hit_skips_fetch`: pre-populate disk cache with
  a serialized `line_coverage` float; call `get_all()` with a mocked
  `WorkflowRun`; assert `fetch_artifact` is never called.
- `test_coverage_summary_cache_miss_stores_result`: mock `fetch_artifact` to
  return a minimal `coverage.json`; call `get_all()`; assert the float is now
  in disk cache.
- `test_coverage_summary_float_round_trip`: store a known `line_coverage`
  float, reload from cache, assert value is preserved.

### `test_pullrequest.py`

- `test_incremental_coverage_cache_hit_skips_fetch`: pre-populate disk cache
  with `{"covered": 10, "instrumented": 20}`; call `_load_incremental_coverage()`;
  assert `fetch_artifact` is never called and `incremental_coverage == 0.5`.
- `test_incremental_coverage_cache_miss_stores_result`: mock `fetch_artifact`
  to return minimal coverage-details JSON; call `_load_incremental_coverage()`;
  assert the `{"covered": ..., "instrumented": ...}` dict is now in disk cache.
- `test_incremental_coverage_zero_instrumented_lines`: cache hit with
  `{"covered": 0, "instrumented": 0}`; assert `incremental_coverage is None`.

---

## Follow-up: ZIP Cache Eviction

Once the coverage output cache is validated, coverage artifact ZIPs become
one-time-use: we only need the raw bytes for the initial parse; on subsequent
runs we reconstruct from the cached output. Pass `cache=False` to `api_raw()`
when downloading coverage ZIPs in `fetch_artifact()` (or pass it from the
caller in `coveragesummary.py` and `pullrequest.py`).

Measure the effect on cache size. In the Step 4 experiment, extracted coverage
JSON accounted for ~2.5 GB of the cache. Skipping ZIP caching should bring
total cache size back below 100 MB, well within GitHub Actions' 10 GB per-repo
budget.

This task is conditional: only do it if the coverage output cache is working
correctly and the cache size is actually a problem in practice.

---

## File Changes

| File | Change |
|---|---|
| `ph/ph/coveragesummary.py` | Add `from_line_coverage` classmethod; cache `line_coverage` float in `get_all()` |
| `ph/ph/pullrequest.py` | Cache `num_covered_lines` + `num_instrumented_lines` in `_load_incremental_coverage()` |
| `ph/tests/test_coveragesummary.py` | New: cache hit, cache miss, and round-trip tests |
| `ph/tests/test_pullrequest.py` | New or extended: cache hit, cache miss, and zero-instrumented tests |
| `CLAUDE.md` | Update performance profile and architecture notes |
