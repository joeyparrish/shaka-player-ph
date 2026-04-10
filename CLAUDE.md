# shaka-player-ph

Project health (PH) metrics tool for the shaka-project/shaka-player GitHub repo.
Collects data via the GitHub CLI (`gh api`) and outputs JSON for a dashboard.

## Entry Point

`ph/update-all.sh` -- runs `ph/main.py` three times (90d, 30d, 7d) and writes
`ph-90.json`, `ph-30.json`, `ph-7.json` to the repo root.

## Architecture

```
ph/main.py              -- CLI, CollectData, JSON/text output
ph/ph/gh.py             -- GitHub API wrapper with disk cache and rate limiter
ph/ph/diskcache.py      -- File-based cache (SHA256 key -> JSON file)
ph/ph/ratelimit.py      -- Token-bucket rate limiter (burst + sustained)
ph/ph/release.py        -- GitHub releases + CDN Last-Modified headers
ph/ph/workflowrun.py    -- GitHub Actions workflow runs, artifact/log fetching
ph/ph/coveragesummary.py -- Coverage % from workflow run artifacts
ph/ph/coveragedetails.py -- Per-file, per-line coverage from Istanbul JSON
ph/ph/pullrequest.py    -- Merged PRs + incremental coverage
ph/ph/commitlog.py      -- git fetch + git log for tag/commit counting
ph/ph/base.py           -- Shared helpers: average(), load_and_filter()
ph/ph/shell.py          -- subprocess wrapper
ph/ph/formatters.py     -- Human-readable output formatting
```

## Key Behaviors

- **Disk cache** (`~/.cache/shaka-player-ph/`): 120-minute TTL by default.
  Keyed by URL (SHA256 hash). Stores text or base64-encoded bytes.
- **Rate limiter**: burst of 1500 calls, then throttled to 4000/hour.
- **`WorkflowRun.get_all()`** and **`CommitLog.get_all()`** use
  `@functools.lru_cache` -- within a single process, duplicate calls are free.
- **`green_workflow` and `coverage_workflow`** both default to
  `selenium-lab-tests.yaml:schedule`, so `green_runs` and `coverage_runs` are
  the same object (lru_cache hit).
- `Release.load_end_time()` calls `requests.get()` to fetch CDN `Last-Modified`
  headers -- this bypasses the disk cache entirely (known optimization target).
- `CommitLog.get_all()` runs `git fetch` + `git log` via subprocess -- also
  bypasses the disk cache (known optimization target).

## Optimization Plan

See `docs/superpowers/specs/2026-04-10-optimization-design.md` for the full
plan. Summary:

1. **Baseline** -- run cold, record API calls and wall time per period
2. **Approach A** (conditional) -- single-process multi-period; skip if 30d/7d
   are already cheap in the baseline
3. **Permanent caching** (unconditional) -- CDN HEAD requests, completed run
   metadata, PR commit data, artifact content, CommitLog by tag
4. **Approach B** (conditional) -- ThreadPoolExecutor for I/O-bound fetches;
   do if wall-clock time is still a problem after step 3

## Development Notes

- Requires `gh` CLI authenticated to GitHub.
- Python dependencies: `python-dateutil`, `requests` (see `ph/requirements.txt`).
- No test suite currently.
- The `ph/ph/` directory is the Python package; `ph/main.py` is the entry point.
