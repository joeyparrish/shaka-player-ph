# Optimization Design: API Quota and Wall-Clock Time

Date: 2026-04-10

## Goals

Reduce GitHub API quota consumption and wall-clock runtime of `ph/update-all.sh`,
which currently runs `main.py` three times (90d, 30d, 7d) and collects project
health metrics from the GitHub API.

Both API quota and wall-clock time are targets.

`CLAUDE.md` must be updated to reflect any architectural changes as each step
is completed.

---

## Background: What the Current Cache Already Handles

The disk cache (120-minute TTL, keyed by URL) already makes many 30d and 7d
requests free after the 90d run, because they share the same URLs:

- PR listings (`/repos/{repo}/pulls?state=closed&page=N`) -- same URL, cached
- Release listings (`/repos/{repo}/releases?page=N`) -- same URL, cached
- Artifact content (keyed by artifact ID in URL) -- cached if run overlaps
- PR commit data (keyed by SHA in URL) -- cached

What the cache does NOT currently handle:

- Workflow run listings -- date range is baked into the URL
  (`?created>=DATE`), so 90d/30d/7d are three separate cache entries
- CDN requests in `Release.load_end_time()` -- uses `requests.get()` directly,
  bypasses the disk cache entirely; also downloads full binary just for headers

---

## Measurement Protocol

Before making any changes, collect a baseline:

1. Clear the local disk cache.
2. Run `update-all.sh` and capture total API calls and wall time per period
   from stderr (`"Made N GH API calls over M minutes"`).
3. Record: 90d calls, 30d calls, 7d calls, total wall time.

Post-change measurements use the same method (cold cache).

### Thresholds for Keeping a Change

- **API calls:** keep if total drops by >15% vs baseline
- **Wall-clock time:** keep if total drops by >10% vs baseline
- Either improvement alone is sufficient to keep a change
- A change that improves one metric and regresses the other requires explicit
  sign-off before merging

---

## Step 1: Baseline (Completed)

Two cold CI runs and one local cold+warm pair measured:

**CI cold runs (two runs, averaged):**

| Period | API calls | Wall time |
|--------|-----------|-----------|
| 90d    | ~3395     | ~28.5 min |
| 30d    | ~19       | ~1.9 min  |
| 7d     | 7         | ~0.5 min  |

**Local cold run:**

| Period | API calls | Wall time | User time |
|--------|-----------|-----------|-----------|
| 90d    | 3404      | 33.4 min  | 11.7 min  |
| 30d    | 18        | 2.2 min   | 1.9 min   |
| 7d     | 7         | 0.6 min   | 0.5 min   |

**Local warm run (immediately after cold):**

| Period | API calls | Wall time | User time |
|--------|-----------|-----------|-----------|
| 90d    | 44        | 6.0 min   | 5.2 min   |
| 30d    | 18        | 2.2 min   | 1.9 min   |
| 7d     | 7         | 0.6 min   | 0.5 min   |

**Key observations:**

- The warm 90d run is the target state: after long-TTL caching + CI
  persistence, every daily CI run will look like this. 33 min → 6 min is a
  5.6x improvement achievable with no changes to collection logic.
- The 44 warm API calls are almost certainly workflow run listing pages (date
  string baked into URL = cache miss every run). At ~400ms each, ~18 seconds --
  not the bottleneck.
- Warm 90d wall time (6:00) ≈ user time (5:10): almost entirely CPU-bound.
  Processing cached ZIPs (ZipFile extraction + CoverageDetails set operations)
  dominates. Step 4 (ZIP optimization) targets this.
- 30d and 7d times are **identical warm vs cold**. The cache makes no
  difference for these periods -- their bottleneck is CDN requests
  (`requests.get()` fetching full binaries, uncached, not counted in API calls)
  and processing. Step 3a addresses this directly.

30d + 7d together = 25 API calls = 0.73% of the 90d cost. Well below the 25%
threshold. **Approach A is skipped.**

---

## Step 3: Post-Implementation Measurement (Completed)

Local cold + warm run after Step 3 (long-TTL caching) and Step 3f (CI cache
persistence) were implemented:

**Local cold run:**

| Period | API calls | Wall time | User time |
|--------|-----------|-----------|-----------|
| 90d    | 3404      | 31.2 min  | 11.7 min  |
| 30d    | 18        | 2.2 min   | 1.9 min   |
| 7d     | 7         | 0.6 min   | 0.5 min   |

**Local warm run (immediately after cold):**

| Period | API calls | Wall time | User time |
|--------|-----------|-----------|-----------|
| 90d    | 44        | 5.9 min   | 5.1 min   |
| 30d    | 18        | 2.2 min   | 1.9 min   |
| 7d     | 7         | 0.6 min   | 0.5 min   |

**Key observations:**

- Cold 90d wall time improved slightly (31.2 vs 33.4 min, -6.6%) -- probably
  from `http_head()` being faster than `requests.get()` for release CDN calls.
  Just below the 10% threshold on its own.
- 30d and 7d are unchanged -- these were always CPU-bound (user time ≈ wall
  time). CDN GET-vs-HEAD made no measurable difference; I/O was never the
  bottleneck for those periods. Step 3a's hypothesis was incorrect.
- API call counts unchanged -- as expected; long-TTL entries only help on
  second and subsequent runs.
- The primary benefit of Step 3 is CI persistence: daily CI runs will look
  like the warm run (6 min) instead of cold (31 min). This can't be confirmed
  until CI runs after the first cache-priming run.
- Warm 90d is still 5.9 min (user 5.1 min): CPU-bound ZIP decompression
  dominates. Step 4 targets this.

---

## Step 2: Approach A (Skipped)

The existing cache already makes 30d and 7d runs nearly free in API terms.
After Step 3, CDN caching will reduce the 30d/7d wall-clock cost. The
remaining overhead (two extra Python process launches + processing) is not
worth the structural complexity of single-process multi-period.

**Revisit only if:** post-Step-3 measurements show 30d + 7d still accounting
for a surprising share of total wall time.

---

## Step 3: Long-TTL Caching

Unconditional improvement regardless of baseline outcome.

### Cache entry design

Replace the global TTL applied uniformly at prune time with a per-entry
`expires_at` absolute timestamp stored inside each cache file:

```json
{ "time": 1234567890, "expires_at": 1243167890, "key": "...", "text": "..." }
```

`DiskCache.store()` gains an optional `ttl_minutes` parameter. If omitted, the
existing default (120 min, from the constructor) is used. Pruning checks each
entry's own `expires_at` against `now` -- no global cutoff needed.

`DiskCache.get()` also checks `expires_at` rather than relying on the global
default, so the right TTL is enforced at read time as well.

`DiskCache.get()` also verifies the stored `key` field matches the input key.
The `key` field is already written by `store()` but never checked on read.
A mismatch (SHA256 collision, however unlikely) is treated as a cache miss.

Backward compatibility: entries lacking `expires_at` fall back to the
constructor default.

### TTL tiers

| TTL | Applied to |
|-----|-----------|
| 120 min (default) | List pages that grow over time: workflow run listings, release listings, PR listings |
| 100 days (long) | Immutable or final objects: see subsections below |

**Why 100 days:** the longest query window is 90 days. No resource older than
90 days is ever requested. A 100-day cap keeps the cache bounded -- entries age
out naturally once they fall outside any query window -- while providing a
comfortable margin. No resource type needs a longer TTL than this.

### 3a. CDN Header Caching

`Release.load_end_time()` currently calls `requests.get(url)`, downloading the
full compiled JS binary just to read `Last-Modified`. Changes:

- Switch to `requests.head(url)` -- headers only, no binary body
- Route through a new `gh.http_head(url)` wrapper that uses `gh.disk_cache`
- Cache the `Last-Modified` date string with the long TTL (release files never
  change after publication)
- `requests` import moves from `release.py` to `gh.py`

Cache entries are tiny (URL key + date string). Safe for CI cache rollover.

### 3b. Completed Workflow Run Metadata

Workflow run list pages are cached at the page level, which is too coarse for
long-TTL caching (a page might contain in-progress runs). But individual run
lookups via `WorkflowRun.load_by_url()` use `gh.api_single()` and return a
single run object. These are the right target.

- In `gh.api_single()`, after parsing JSON, inspect for `conclusion` field
- If present and non-null, store with long TTL
- List pages (`gh.api_multiple()`) keep the default TTL unchanged
- This is content-aware (checks response body), not URL-based

### 3c. PR Commit Data

`/repos/{repo}/commits/{sha}/files` -- SHA in URL means content is immutable.

- In `gh.api_multiple()`, detect SHA-keyed commit URLs by pattern
  (`/commits/[0-9a-f]{40}`)
- Store with long TTL unconditionally

### 3d. Artifact Listings

The artifact listing (`/actions/runs/{id}/artifacts`) is final once a run
completes -- no new artifacts can be added to a finished run. This listing is
fetched via `gh.api_multiple()` inside `fetch_artifact()` and currently gets
the default 120-minute TTL.

- Store artifact listings with long TTL when the parent run has a non-null
  `conclusion`, consistent with 3b

### 3e. CommitLog Data

`CommitLog.get_all()` currently bypasses the disk cache entirely (runs
`git fetch` and `git log` via subprocess).

- Add disk cache calls in `CommitLog.get_all()`
- Cache key: `"commitlog:{repo}:{ref}"`
- **Tag ref** (e.g. `v4.3.5`): `git log` walks backward from a fixed point in
  immutable history. Content never changes. Store with long TTL.
- **Branch ref** (e.g. `v4.3.x`): new commits can land on live branches.
  Store with default TTL.

The branch-based lookup exists as an `lru_cache` optimization (multiple
releases on one branch share a single `git fetch`). With disk caching making
tag lookups cheap between runs, the branch optimization matters less -- but it
is preserved for within-process deduplication.

---

## Step 3f: CI Cache Persistence

Once long-TTL entries exist, persist the cache between daily CI runs using
`actions/cache`. Without long-TTL entries, the cache is fully pruned at startup
(all entries are stale after 24 hours), making CI persistence worthless.

Add to `deploy.yaml`, wrapping the "Update metrics" step:

```yaml
- name: Restore PH cache
  uses: actions/cache/restore@v4
  with:
    path: ~/.cache/shaka-player-ph
    key: ph-cache-${{ github.run_id }}
    restore-keys: ph-cache-

- name: Update metrics
  run: ./ph/update-all.sh
  env:
    GH_TOKEN: ${{ secrets.PH_GITHUB_TOKEN }}

- name: Save PH cache
  uses: actions/cache/save@v4
  if: always()
  with:
    path: ~/.cache/shaka-player-ph
    key: ph-cache-${{ github.run_id }}
```

Using separate restore/save actions (rather than the combined `cache` action)
ensures the cache is saved even if the metrics step fails.

The run-specific key with `ph-cache-` restore prefix means each run saves a
fresh snapshot while always restoring the most recent prior one. GitHub
auto-evicts caches not accessed for 7 days; since this runs daily, eviction
is not a concern. The 100-day TTL cap keeps total cache size bounded as
long-TTL entries age out naturally.

---

## Step 4: Artifact ZIP Optimization (Attempted, Reverted)

Implemented and measured. **Reverted -- does not meet the 10% threshold.**

**What was tried:**
- v1: Cache extracted file bytes directly (long TTL); skip caching the ZIP.
  Cache size: 3.2 GB (extracted JSON uncompressed, base64-encoded).
- v2: Same, but store as UTF-8 text (no base64) and skip ZIP caching entirely.
  Cache size: 2.5 GB.

**Measurements (warm 90d, baseline 5m56s):**
- v1 hot: 5m55s (-0.2%) -- no improvement
- v2 hot: 6m04s (+1.4%) -- slightly slower (noise)

**Root cause finding:** Disabled all coverage processing in `main.py` and
re-ran warm:

| | With coverage | Without | Coverage share |
|---|---|---|---|
| 90d | 6m04s | 0m57s | ~84% |
| 30d | 2m17s | 0m28s | ~80% |
| 7d | 0m43s | 0m16s | ~63% |

Coverage processing is pure CPU (user ≈ wall time), dominated by
`CoverageDetails` JSON parsing and set operations -- roughly 3-4 seconds per
coverage run. Caching raw bytes (Step 4) was the wrong abstraction level:
the bottleneck is downstream of the bytes, in Python parsing and set work.

**Future optimization target:** Cache the *output* of coverage computation
(e.g. `CoverageSummary.line_coverage`, or serialized `CoverageDetails`) so
parsing and set operations are done once per run and reused across invocations.
This would require a new serialization format for coverage objects and a
higher-level cache key (e.g. `"coverage:{run_id}:{filename}"`).

Step 4 also produced a 2.5 GB cache, which would consume 25% of the 10 GB
GitHub Actions per-repo cache budget with no performance benefit.

---

## Step 5: Rate Limit Awareness at Startup

`RateLimit.__init__` currently ignores how much quota has already been consumed
by prior processes using the same token. This caused a local run to fail after
CI had already consumed most of the hourly quota.

At startup, query the actual remaining quota and clamp the burst limit:

```python
actual = gh.api_single("/rate_limit")["resources"]["core"]
burst_limit = max(0, min(args.burst_limit, actual["remaining"] - 1000))
```

The 1000-call safety margin accounts for other tools or processes using the
same token concurrently. If remaining quota is at or below 1000, burst_limit
is clamped to 0 -- the run proceeds but immediately falls into the sustained
rate-limited mode rather than consuming a burst.

If burst_limit is clamped below `args.burst_limit`, log a warning to stderr
with the actual remaining count and the reset timestamp so the user knows why
the run may be slow.

Note: `gh api rate_limit` does not itself consume quota.

This step can be implemented independently of Steps 3, 4, and 6.

---

## Step 6: Decision Point -- Approach B (Parallel I/O)

Re-measure after Steps 3 and 4. If wall-clock time is still a pain point:

- Use `ThreadPoolExecutor` to parallelize CDN HEAD requests (one per release,
  currently serial)
- Parallelize artifact downloads in `CoverageSummary.get_all()` and
  `PullRequest.average_incremental_coverage()`
- The rate limiter (`RateLimit`) will need a threading lock around `num_calls`
  and `start_time` access

**Do Approach B if:** wall-clock time has not improved enough after Steps 3 and 4.

---

## File Change Summary

| File | Change |
|---|---|
**Step 3 (Long-TTL Caching):**

| File | Change |
|---|---|
| `ph/diskcache.py` | Add `ttl_minutes` param to `store()`; store/check `expires_at` and verify `key` on read |
| `ph/gh.py` | Add `http_head(url)`; pass long TTL for immutable resources in `api_raw/single/multiple` |
| `ph/release.py` | Use `gh.http_head()` instead of `requests.get()` |
| `ph/commitlog.py` | Add disk cache calls; long TTL for tag refs, default TTL for branch refs |
| `.github/workflows/deploy.yaml` | Add `actions/cache` restore/save around "Update metrics" step |
| `CLAUDE.md` | Update to reflect caching architecture changes |

**Step 4 (ZIP Optimization):**

| File | Change |
|---|---|
| `ph/workflowrun.py` | Cache extracted file bytes instead of ZIPs in `fetch_artifact()` |
| `CLAUDE.md` | Update to reflect artifact caching change |

**Step 5 (Rate Limit Awareness):**

| File | Change |
|---|---|
| `ph/ratelimit.py` | Query `/rate_limit` at startup; clamp burst to `remaining - 1000`; threading lock (if Step 6) |
| `CLAUDE.md` | Update to reflect rate limit behavior change |
