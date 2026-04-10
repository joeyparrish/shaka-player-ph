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
ph/ph/release.py        -- GitHub releases + CDN Last-Modified headers (via HEAD, cached 100d)
ph/ph/workflowrun.py    -- GitHub Actions workflow runs, artifact/log fetching
ph/ph/coveragesummary.py -- Coverage % from workflow run artifacts
ph/ph/coveragedetails.py -- Per-file, per-line coverage from Istanbul JSON
ph/ph/pullrequest.py    -- Merged PRs + incremental coverage
ph/ph/commitlog.py      -- git fetch + git log; disk-cached (100d for tags, 120min for branches)
ph/ph/base.py           -- Shared helpers: average(), load_and_filter()
ph/ph/shell.py          -- subprocess wrapper
ph/ph/formatters.py     -- Human-readable output formatting
```

## Key Behaviors

- **Disk cache** (`~/.cache/shaka-player-ph/`): per-entry TTL stored as
  `expires_at` in each cache file. Default TTL 120 minutes for list pages.
  Long TTL (100 days) for immutable objects: CDN headers (via HEAD request),
  completed workflow run metadata, PR commit data (SHA-keyed URLs), and
  CommitLog data for tag refs. Backward-compatible with old entries lacking
  `expires_at`. Key stored in each entry for collision detection.
- **Rate limiter**: GitHub gives 5000 calls/hour per personal token, shared
  across all apps using that token -- no separate burst concept. The tool
  manages this with two self-imposed limits: a burst budget (`--burst-limit`,
  default 1500) consumed at full speed, then a sustained throttle
  (`--rate-limit`, default 4000/hour) to preserve quota for other tools. At
  startup, queries `/rate_limit` and clamps burst to `max(0, remaining - 1000)`
  to leave headroom for concurrent usage. Warns to stderr if clamped.
- **`WorkflowRun.get_all()`** and **`CommitLog.get_all()`** use
  `@functools.lru_cache` -- within a single process, duplicate calls are free.
- **`green_workflow` and `coverage_workflow`** both default to
  `selenium-lab-tests.yaml:schedule`, so `green_runs` and `coverage_runs` are
  the same object (lru_cache hit).
- **CI cache persistence**: `deploy.yaml` uses `actions/cache` restore/save
  around the metrics step so long-TTL cache entries persist across daily runs.
  A cold run takes ~31 min; with a warm CI cache it takes ~6 min.

## Performance Profile (warm run)

Coverage parsing accounts for ~84% of warm 90d runtime (~5 of 6 minutes).
It is pure CPU (Python JSON parsing + set operations on CoverageDetails).
The next optimization target is caching the *output* of coverage computation
(e.g. serialized CoverageSummary/CoverageDetails objects) rather than the
raw JSON bytes, so parsing is done once per run and reused across invocations.

## Development Notes

- Requires `gh` CLI authenticated to GitHub.
- Python dependencies: `python-dateutil`, `requests` (see `ph/requirements.txt`).
- Test suite: `cd ph && python -m pytest tests/ -v`
- The `ph/ph/` directory is the Python package; `ph/main.py` is the entry point.
- See `docs/superpowers/specs/2026-04-10-optimization-design.md` for the full
  optimization history and measurement data.
