# Optimization: Long-TTL Caching & Related Improvements

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce GitHub API quota and wall-clock time by implementing per-entry TTL caching, CDN header caching via HEAD requests, CommitLog disk caching, artifact ZIP optimization, and startup rate-limit awareness.

**Architecture:** Three independently measurable phases from the spec (Steps 3, 4, 5). Step 3 builds the caching foundation and CI persistence; Step 4 eliminates ZIP decompression on cache hits; Step 5 adds startup rate-limit awareness. Each phase ends with a cold-cache measurement compared to the baseline (90d: ~3404 calls / 33.4 min, warm: 44 calls / 6.0 min).

**Tech Stack:** Python 3, pytest, unittest.mock, requests (HEAD only), existing gh CLI via subprocess.

---

## File Map

| File | Change |
|---|---|
| `ph/requirements.txt` | Add `requests`, `pytest` |
| `ph/tests/__init__.py` | Create (empty) |
| `ph/tests/test_diskcache.py` | New test file |
| `ph/tests/test_gh.py` | New test file |
| `ph/tests/test_commitlog.py` | New test file |
| `ph/tests/test_workflowrun.py` | New test file |
| `ph/ph/diskcache.py` | Per-entry `expires_at`; key verification in `get()` |
| `ph/ph/gh.py` | `LONG_TTL_MINUTES`; `http_head()`; long-TTL routing in `_api_base`, `api_single`, `api_multiple`, `api_raw` |
| `ph/ph/release.py` | Replace `requests.get()` with `gh.http_head()` |
| `ph/ph/commitlog.py` | Add disk cache calls; long TTL for tag refs |
| `ph/ph/workflowrun.py` | `fetch_artifact()` caches extracted bytes, not ZIPs |
| `ph/ph/ratelimit.py` | No change in Step 3/4; threading lock added if Step 6 runs |
| `ph/main.py` | Query rate limit before `gh.configure()`; pass clamped burst |
| `.github/workflows/deploy.yaml` | `actions/cache` restore/save around metrics step |
| `CLAUDE.md` | Update after each phase |

---

## --- PHASE 1: Step 3 — Long-TTL Caching ---

---

### Task 1: Test infrastructure and DiskCache per-entry TTL

**Files:**
- Modify: `ph/requirements.txt`
- Create: `ph/tests/__init__.py`
- Create: `ph/tests/test_diskcache.py`
- Modify: `ph/ph/diskcache.py`

- [ ] **Step 1: Add pytest and requests to requirements**

Edit `ph/requirements.txt`:
```
python-dateutil >= 2.8.2
requests >= 2.28.0
pytest >= 7.0.0
```

- [ ] **Step 2: Create test package**

```bash
mkdir -p /path/to/repo/ph/tests
touch /path/to/repo/ph/tests/__init__.py
```

- [ ] **Step 3: Write failing tests for per-entry TTL**

Create `ph/tests/test_diskcache.py`:
```python
import time
import pytest
from ph.diskcache import DiskCache


def test_store_and_get_default_ttl(tmp_path):
    cache = DiskCache(str(tmp_path), expiration_minutes=120)
    cache.store("key1", "value1")
    assert cache.get("key1") == "value1"


def test_get_returns_none_for_expired_entry(tmp_path):
    cache = DiskCache(str(tmp_path), expiration_minutes=120)
    cache.store("key1", "value1", ttl_minutes=0)
    # ttl_minutes=0 means expires immediately
    time.sleep(0.01)
    assert cache.get("key1") is None


def test_long_ttl_survives_default_expiry(tmp_path):
    cache = DiskCache(str(tmp_path), expiration_minutes=0)
    cache.store("key1", "value1", ttl_minutes=144000)
    # Default TTL is 0 (expired immediately), but this entry has long TTL
    assert cache.get("key1") == "value1"


def test_bytes_round_trip(tmp_path):
    cache = DiskCache(str(tmp_path), expiration_minutes=120)
    cache.store("key1", b"\x00\x01\x02", ttl_minutes=120)
    assert cache.get("key1") == b"\x00\x01\x02"


def test_key_mismatch_returns_none(tmp_path):
    """Simulate a SHA256 collision by writing a cache file with a different key."""
    import json, hashlib, os
    cache = DiskCache(str(tmp_path), expiration_minutes=120)
    # Store under key1, but manually write a file that claims to be key2
    real_key = "key1"
    sha = hashlib.sha256(real_key.encode("utf8")).hexdigest()
    path = os.path.join(str(tmp_path), sha + ".json")
    with open(path, "w") as f:
        json.dump({
            "time": time.time(),
            "expires_at": time.time() + 7200,
            "key": "key2",  # wrong key
            "text": "value1",
        }, f)
    assert cache.get("key1") is None


def test_backward_compat_entry_without_expires_at(tmp_path):
    """Entries written before this change (no expires_at) use default TTL."""
    import json, hashlib, os
    cache = DiskCache(str(tmp_path), expiration_minutes=120)
    real_key = "key1"
    sha = hashlib.sha256(real_key.encode("utf8")).hexdigest()
    path = os.path.join(str(tmp_path), sha + ".json")
    with open(path, "w") as f:
        json.dump({
            "time": time.time(),
            "key": real_key,
            "text": "value1",
        }, f)
    assert cache.get("key1") == "value1"


def test_backward_compat_expired_entry_without_expires_at(tmp_path):
    """Old entries past the default TTL are treated as expired."""
    import json, hashlib, os
    cache = DiskCache(str(tmp_path), expiration_minutes=120)
    real_key = "key1"
    sha = hashlib.sha256(real_key.encode("utf8")).hexdigest()
    path = os.path.join(str(tmp_path), sha + ".json")
    with open(path, "w") as f:
        json.dump({
            "time": time.time() - 7201,  # 120 min + 1 sec ago
            "key": real_key,
            "text": "value1",
        }, f)
    assert cache.get("key1") is None


def test_prune_removes_expired_entry(tmp_path):
    cache = DiskCache(str(tmp_path), expiration_minutes=120)
    cache.store("key1", "value1", ttl_minutes=0)
    time.sleep(0.01)
    # Manually trigger prune (normally runs at startup)
    cache._prune_cache()
    import os
    assert len(os.listdir(str(tmp_path))) == 0


def test_prune_keeps_long_ttl_entry(tmp_path):
    cache = DiskCache(str(tmp_path), expiration_minutes=0)
    cache.store("key1", "value1", ttl_minutes=144000)
    cache._prune_cache()
    assert cache.get("key1") == "value1"
```

- [ ] **Step 4: Run tests to verify they fail**

```bash
cd ph && python -m pytest tests/test_diskcache.py -v
```

Expected: most tests fail because `store()` doesn't accept `ttl_minutes` yet.

- [ ] **Step 5: Implement per-entry TTL in DiskCache**

Replace `ph/ph/diskcache.py` entirely:
```python
import base64
import hashlib
import json
import os
import time
import sys

class DiskCache(object):
  """Cache some arbitrary data on disk."""

  def __init__(self, cache_folder, expiration_minutes):
    self.cache_folder = cache_folder
    self.expiration_minutes = expiration_minutes
    os.makedirs(self.cache_folder, mode=0o755, exist_ok=True)
    self._prune_cache()

  def _prune_cache(self):
    now = time.time()
    for name in os.listdir(self.cache_folder):
      path = os.path.join(self.cache_folder, name)
      self._prune_file_if_expired(path, now)

  def _prune_file_if_expired(self, path, now):
    try:
      with open(path, "r") as f:
        data = json.load(f)
      expires_at = data.get(
          "expires_at",
          data["time"] + self.expiration_minutes * 60)
      if expires_at < now:
        os.unlink(path)
    except Exception as e:
      print("Exception pruning cache file {}: {}".format(path, e),
            file=sys.stderr)
      self._delete_corrupt_file(path)

  def _delete_corrupt_file(self, path):
    try:
      os.unlink(path)
    except:
      pass

  def _path_for_key(self, key):
    sha = hashlib.sha256(key.encode("utf8")).hexdigest()
    return os.path.join(self.cache_folder, sha + ".json")

  def get(self, key):
    """Returns data if it exists and is valid, or None."""
    path = self._path_for_key(key)
    try:
      with open(path, "r") as f:
        stored = json.load(f)

      if stored.get("key") != key:
        return None

      expires_at = stored.get(
          "expires_at",
          stored["time"] + self.expiration_minutes * 60)
      if time.time() >= expires_at:
        return None

      if "text" in stored:
        return stored["text"]
      else:
        return base64.b64decode(stored["bytes"])
    except FileNotFoundError:
      return None
    except Exception as e:
      print("Exception loading cache file {}: {}".format(path, e),
            file=sys.stderr)
      self._delete_corrupt_file(path)
      return None

  def store(self, key, data, ttl_minutes=None):
    """Stores data in the cache."""
    if ttl_minutes is None:
      ttl_minutes = self.expiration_minutes
    path = self._path_for_key(key)
    try:
      with open(path, "w") as f:
        stored = {
          "time": time.time(),
          "expires_at": time.time() + ttl_minutes * 60,
          "key": key,
        }
        if type(data) is str:
          stored["text"] = data
        elif type(data) is bytes:
          stored["bytes"] = base64.b64encode(data).decode("utf8")
        else:
          raise RuntimeError("Unexpected data type in cache: {}".format(
                             type(data)))
        json.dump(stored, f)
    except Exception as e:
      print("Exception storing cache file {}: {}".format(path, e),
            file=sys.stderr)
      self._delete_corrupt_file(path)
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd ph && python -m pytest tests/test_diskcache.py -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add ph/requirements.txt ph/tests/__init__.py ph/tests/test_diskcache.py ph/ph/diskcache.py
git commit -m "Add per-entry TTL and key verification to DiskCache" -m "store() accepts ttl_minutes; expires_at stored per-entry. get() checks key match (collision guard) and per-entry expiry. Backward-compatible with old entries lacking expires_at. Prune uses per-entry expires_at." -m "Co-Authored-By: Claude Code (Claude Sonnet 4.6) <noreply@anthropic.com>"
```

---

### Task 2: gh.py — long-TTL routing and http_head()

**Files:**
- Create: `ph/tests/test_gh.py`
- Modify: `ph/ph/gh.py`

- [ ] **Step 1: Write failing tests**

Create `ph/tests/test_gh.py`:
```python
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
    sha_url = "/repos/owner/repo/commits/abcdef1234567890abcdef1234567890abcdef12/files?page_size=100&page=1"
    fake_response = json.dumps([{"filename": "lib/player.js", "patch": ""}])
    with patch("ph.shell.run_command", return_value=fake_response):
        gh.api_multiple(sha_url)
    import time, hashlib, os
    sha = hashlib.sha256(sha_url.encode("utf8")).hexdigest()
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ph && python -m pytest tests/test_gh.py -v
```

Expected: failures on missing `http_head`, missing `ttl_minutes` routing.

- [ ] **Step 3: Implement long-TTL routing and http_head() in gh.py**

Replace `ph/ph/gh.py` entirely:
```python
import json
import re
import sys

import requests as requests_lib

from . import shell
from .diskcache import DiskCache
from .ratelimit import RateLimit


LONG_TTL_MINUTES = 144_000  # 100 days

rate_limiter = None
disk_cache = None
debug_api = False


def configure(burst_limit, rate_limit_per_hour, cache_folder, cache_minutes,
              debug):
  global rate_limiter
  global disk_cache
  global debug_api

  rate_limiter = RateLimit(burst_limit, rate_limit_per_hour)
  disk_cache = DiskCache(cache_folder, cache_minutes)
  debug_api = debug


def http_head(url):
  """Fetch HTTP headers via HEAD request. Caches with long TTL."""
  cached = disk_cache.get(url)
  if cached is not None:
    return json.loads(cached)

  response = requests_lib.head(url)
  headers = dict(response.headers)
  disk_cache.store(url, json.dumps(headers), ttl_minutes=LONG_TTL_MINUTES)
  return headers


def _ttl_for_url(url):
  """Return LONG_TTL_MINUTES for URLs whose content is immutable, else None."""
  if re.search(r'/commits/[0-9a-f]{40}', url):
    return LONG_TTL_MINUTES
  return None


def _api_base(url_or_full_path, is_text, ttl_minutes=None):
  global rate_limiter
  global disk_cache
  global debug_api

  data = disk_cache.get(url_or_full_path)

  if debug_api:
    if data is None:
      print("CACHE MISS: {}".format(url_or_full_path), file=sys.stderr)
    else:
      print("CACHE HIT: {}".format(url_or_full_path), file=sys.stderr)

  if data is not None:
    return data

  rate_limiter.wait()
  args = ["gh", "api", url_or_full_path]
  data = shell.run_command(args, text=is_text)
  disk_cache.store(url_or_full_path, data, ttl_minutes=ttl_minutes)

  return data


def api_raw(url_or_path):
  # Artifact ZIPs are not cached with long TTL here; fetch_artifact() caches
  # the extracted file bytes directly instead (see workflowrun.py).
  return _api_base(url_or_path, is_text=False)


def api_single(url_or_path):
  # Check URL-based TTL first
  ttl = _ttl_for_url(url_or_path)

  # For cache misses we may detect TTL from content
  cached = disk_cache.get(url_or_path)
  if cached is not None:
    if debug_api:
      print("CACHE HIT: {}".format(url_or_path), file=sys.stderr)
    return json.loads(cached)

  if debug_api:
    print("CACHE MISS: {}".format(url_or_path), file=sys.stderr)

  rate_limiter.wait()
  raw = shell.run_command(["gh", "api", url_or_path], text=True)
  parsed = json.loads(raw)

  # Use long TTL for completed workflow runs (content-based detection)
  if ttl is None and isinstance(parsed, dict) and parsed.get("conclusion") is not None:
    ttl = LONG_TTL_MINUTES

  disk_cache.store(url_or_path, raw, ttl_minutes=ttl)
  return parsed


def api_multiple(url_or_path, subkey=None, stop_predicate=None,
                 ttl_minutes=None):
  if "?" in url_or_path:
    url_or_path += "&page_size=100"
  else:
    url_or_path += "?page_size=100"

  # Detect URL-based long TTL (e.g. commit SHAs); caller-supplied ttl_minutes
  # takes precedence if provided.
  url_ttl = _ttl_for_url(url_or_path)
  effective_ttl = ttl_minutes if ttl_minutes is not None else url_ttl

  page_number = 1
  results = []
  while True:
    next_page_url = url_or_path + "&page={}".format(page_number)
    output = _api_base(next_page_url, is_text=True, ttl_minutes=effective_ttl)

    next_page = json.loads(output)
    if subkey is not None:
      next_page = next_page[subkey]

    assert type(next_page) is list
    if len(next_page) == 0:
      break

    results.extend(next_page)
    if stop_predicate is not None and stop_predicate(results):
      break
    page_number += 1

  return results
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd ph && python -m pytest tests/test_gh.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add ph/ph/gh.py ph/tests/test_gh.py
git commit -m "Add long-TTL routing and http_head() to gh.py" -m "LONG_TTL_MINUTES=144000 (100 days). api_raw() uses long TTL for artifact archives. api_single() uses long TTL for completed workflow runs (content-based) and commit SHAs. api_multiple() uses long TTL for commit SHAs; accepts caller-supplied ttl_minutes. http_head() caches response headers via HEAD request with long TTL." -m "Co-Authored-By: Claude Code (Claude Sonnet 4.6) <noreply@anthropic.com>"
```

---

### Task 3: release.py — use gh.http_head() for CDN requests

**Files:**
- Modify: `ph/ph/release.py`

- [ ] **Step 1: Replace requests.get() with gh.http_head()**

In `ph/ph/release.py`, remove `import requests` and update `load_end_time()`:

```python
  def load_end_time(self):
    bare_version = self.name.replace("v", "")
    url = CDN_URL_TEMPLATE % bare_version
    headers = gh.http_head(url)
    last_modified = headers.get("Last-Modified") or headers.get("last-modified")
    if last_modified is not None:
      self.end_time = dateutil.parser.parse(last_modified)
    else:
      self.end_time = None
```

Remove the `import requests` line at the top of the file.

- [ ] **Step 2: Run a quick smoke test**

```bash
cd ph && python -c "
from ph import gh
from ph.diskcache import DiskCache
from ph.ratelimit import RateLimit
import os
gh.configure(100, 4000, '/tmp/test-cache', 120, False)
from ph.release import Release
print('import OK')
"
```

Expected: prints `import OK` with no errors.

- [ ] **Step 3: Commit**

```bash
git add ph/ph/release.py
git commit -m "release.py: use gh.http_head() instead of requests.get()" -m "Switches from GET (downloads full binary) to HEAD (headers only). CDN Last-Modified responses now cached permanently via gh.py long-TTL logic. Removes direct requests import from release.py." -m "Co-Authored-By: Claude Code (Claude Sonnet 4.6) <noreply@anthropic.com>"
```

---

### Task 4: commitlog.py — add disk cache

**Files:**
- Create: `ph/tests/test_commitlog.py`
- Modify: `ph/ph/commitlog.py`

- [ ] **Step 1: Write failing tests**

Create `ph/tests/test_commitlog.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ph && python -m pytest tests/test_commitlog.py -v
```

Expected: failures because CommitLog doesn't use disk cache yet.

- [ ] **Step 3: Implement disk caching in commitlog.py**

Replace `ph/ph/commitlog.py` entirely:
```python
import functools
import json
import re

from . import gh
from . import shell


_TAG_RE = re.compile(r'^v\d+\.\d+\.\d+$')


def _is_tag_ref(ref):
  return bool(_TAG_RE.match(ref))


class CommitLog(object):
  def __init__(self, timestamp, tags):
    self.timestamp = timestamp
    self.tags = tags

  @staticmethod
  @functools.lru_cache
  def get_all(repo, branch, range_start):
    cache_key = "commitlog:{}:{}".format(repo, branch)
    ttl = gh.LONG_TTL_MINUTES if _is_tag_ref(branch) else None

    cached = gh.disk_cache.get(cache_key)
    if cached is None:
      args = ["git", "fetch", "--tags", "https://github.com/%s" % repo, branch]
      shell.run_command(args)
      args = [
        "git", "log", "--format=%ct %D", "--decorate-refs=tags/*", "FETCH_HEAD",
      ]
      cached = shell.run_command(args)
      gh.disk_cache.store(cache_key, cached, ttl_minutes=ttl)

    lines = cached.strip().split("\n")
    logs = []
    for line in lines:
      timestamp_string, tag_string = (line + " ").split(" ", 1)
      timestamp = int(timestamp_string)
      if range_start is not None and timestamp < range_start.timestamp():
        break

      tags = tag_string.strip().split(", ")
      if len(tags) == 1 and tags[0] == "":
        tags = []
      else:
        tags = list(map(lambda x: x.replace("tag: ", ""), tags))

      logs.append(CommitLog(timestamp, tags))

    return logs
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd ph && python -m pytest tests/test_commitlog.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add ph/ph/commitlog.py ph/tests/test_commitlog.py
git commit -m "commitlog.py: add disk cache with long TTL for tag refs" -m "CommitLog.get_all() now checks disk cache before running git fetch/log. Tag refs (e.g. v4.3.5) cached with 100-day TTL (immutable history). Branch refs (e.g. v4.3.x) use default 120-min TTL. lru_cache preserved for within-process deduplication." -m "Co-Authored-By: Claude Code (Claude Sonnet 4.6) <noreply@anthropic.com>"
```

---

### Task 5: CI cache persistence (deploy.yaml)

**Files:**
- Modify: `.github/workflows/deploy.yaml`

- [ ] **Step 1: Add actions/cache restore/save around metrics step**

In `.github/workflows/deploy.yaml`, replace the `Update metrics` step with:

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

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/deploy.yaml
git commit -m "CI: persist PH disk cache between daily runs" -m "Uses actions/cache restore/save (separate actions so cache saves even on failure). Run-specific key with ph-cache- restore prefix always restores most recent prior cache. Long-TTL entries from Step 3 will now persist across daily runs." -m "Co-Authored-By: Claude Code (Claude Sonnet 4.6) <noreply@anthropic.com>"
```

---

### Task 6: Measure Step 3 and update CLAUDE.md

- [ ] **Step 1: Clear cache and run cold baseline**

```bash
rm -rf ~/.cache/shaka-player-ph
cd /path/to/repo/ph && time ./update-all.sh 2>&1 | tee /tmp/step3-cold.txt
```

Record: 90d calls, 30d calls, 7d calls, total wall time.

- [ ] **Step 2: Run warm immediately after**

```bash
cd /path/to/repo/ph && time ./update-all.sh 2>&1 | tee /tmp/step3-warm.txt
```

Record: same fields. Compare 90d warm to baseline warm (was 44 calls / 6.0 min).
Compare 30d to baseline (was 18 calls / 2.2 min) -- CDN HEAD + caching should
show a significant drop.

- [ ] **Step 3: Update CLAUDE.md with new cache architecture**

In `CLAUDE.md`, update the "Key Behaviors" section disk cache entry and add a
note about long-TTL entries:

```markdown
- **Disk cache** (`~/.cache/shaka-player-ph/`): per-entry TTL stored as
  `expires_at` in each cache file. Default TTL 120 minutes for list pages.
  Long TTL (100 days) for immutable objects: CDN headers (via HEAD request),
  completed workflow run metadata, PR commit data (SHA-keyed URLs), artifact
  content, CommitLog data for tag refs.
```

Also update the `release.py` line in the architecture table:
```markdown
ph/ph/release.py        -- GitHub releases + CDN Last-Modified headers (via HEAD, cached 100d)
```

And update the `commitlog.py` line:
```markdown
ph/ph/commitlog.py      -- git fetch + git log; disk-cached (100d for tags, 120min for branches)
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "Update CLAUDE.md: document long-TTL cache architecture" -m "Co-Authored-By: Claude Code (Claude Sonnet 4.6) <noreply@anthropic.com>"
```

---

## --- PHASE 2: Step 4 — ZIP Optimization ---

---

### Task 7: workflowrun.py — cache extracted artifact bytes

**Files:**
- Create: `ph/tests/test_workflowrun.py`
- Modify: `ph/ph/workflowrun.py`

- [ ] **Step 1: Write failing tests**

Create `ph/tests/test_workflowrun.py`:
```python
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


def _make_artifacts_response(archive_url):
    return json.dumps({
        "artifacts": [
            {"name": "coverage", "archive_download_url": archive_url}
        ]
    })


def test_fetch_artifact_returns_file_content(tmp_path):
    from ph.workflowrun import WorkflowRun
    run = WorkflowRun(_make_run_data())
    archive_url = "https://api.github.com/repos/owner/repo/actions/artifacts/99/zip"
    zip_bytes = _make_zip("coverage-details.json", b'{"total": {}}')

    with patch("ph.shell.run_command") as mock_cmd:
        mock_cmd.side_effect = [
            # artifacts listing
            json.dumps({"artifacts": [{"name": "coverage", "archive_download_url": archive_url}]}),
            # archive download
            zip_bytes,
        ]
        # api_multiple returns a list; api_raw returns bytes
        # We need to patch at a higher level
        pass

    # Patch gh.api_multiple and gh.api_raw directly
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
    artifacts_page2_url = run.artifacts_url + "?page_size=100&page=2"

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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ph && python -m pytest tests/test_workflowrun.py -v
```

Expected: several failures because `fetch_artifact()` still uses ZIP caching.

- [ ] **Step 3: Implement extracted-byte caching in fetch_artifact()**

In `ph/ph/workflowrun.py`, replace the `fetch_artifact()` method:

```python
  def fetch_artifact(self, name, filename):
    # Use long TTL for artifact listings since completed runs don't gain new
    # artifacts.
    results = gh.api_multiple(self.artifacts_url, "artifacts",
                              ttl_minutes=gh.LONG_TTL_MINUTES)

    for data in results:
      if data["name"] == name:
        archive_url = data["archive_download_url"]
        cache_key = archive_url + "#" + filename

        cached = gh.disk_cache.get(cache_key)
        if cached is not None:
          return cached

        try:
          zip_data = gh.api_raw(archive_url)
        except RuntimeError as e:
          print(
            'Failed to fetch artifact for run from {}'.format(self.start_time),
            file=sys.stderr)
          print(e, file=sys.stderr)
          return None

        if zip_data is None:
          return None

        with zipfile.ZipFile(io.BytesIO(zip_data), 'r') as f:
          try:
            file_bytes = f.read(filename)
          except KeyError:
            return None

        gh.disk_cache.store(cache_key, file_bytes,
                            ttl_minutes=gh.LONG_TTL_MINUTES)
        return file_bytes

    return None
```

Note: `api_raw()` still downloads the ZIP (no caching of the ZIP itself since
we immediately extract and cache the file). The ZIP is discarded after extraction.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd ph && python -m pytest tests/test_workflowrun.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Run full test suite**

```bash
cd ph && python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add ph/ph/workflowrun.py ph/tests/test_workflowrun.py
git commit -m "workflowrun: cache extracted artifact bytes instead of ZIPs" -m "fetch_artifact() now caches the extracted file bytes directly (key: archive_url + '#' + filename, long TTL). Cache hits are plain disk reads with no ZIP decompression. Artifact listings also use long TTL. ZIP is downloaded, extracted, then discarded." -m "Co-Authored-By: Claude Code (Claude Sonnet 4.6) <noreply@anthropic.com>"
```

---

### Task 8: Measure Step 4 and update CLAUDE.md

- [ ] **Step 1: Clear cache and run cold**

```bash
rm -rf ~/.cache/shaka-player-ph
cd /path/to/repo/ph && time ./update-all.sh 2>&1 | tee /tmp/step4-cold.txt
```

- [ ] **Step 2: Run warm immediately after**

```bash
cd /path/to/repo/ph && time ./update-all.sh 2>&1 | tee /tmp/step4-warm.txt
```

Compare warm 90d to Step 3 warm (was 6.0 min). The improvement here is
elimination of ZipFile decompression on cache hits -- expect a reduction in
user time (CPU) for the warm run.

**Keep if:** warm 90d wall time drops by >10% vs Step 3 warm.

- [ ] **Step 3: Update CLAUDE.md**

Update the `workflowrun.py` line in the architecture table:
```markdown
ph/ph/workflowrun.py    -- GitHub Actions workflow runs; artifact bytes cached directly (not ZIPs)
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "Update CLAUDE.md: document artifact byte caching" -m "Co-Authored-By: Claude Code (Claude Sonnet 4.6) <noreply@anthropic.com>"
```

---

## --- PHASE 3: Step 5 — Rate Limit Awareness ---

---

### Task 9: Startup rate limit clamping

**Files:**
- Modify: `ph/ph/gh.py`
- Modify: `ph/main.py`

- [ ] **Step 1: Add get_rate_limit_remaining() to gh.py**

Add to `ph/ph/gh.py` before `configure()`:

```python
def get_rate_limit_remaining():
  """Query actual remaining GitHub API quota. Does not consume quota."""
  import datetime
  raw = shell.run_command(["gh", "api", "/rate_limit"], text=True)
  data = json.loads(raw)
  core = data["resources"]["core"]
  return core["remaining"], core["reset"]
```

- [ ] **Step 2: Update main.py to clamp burst limit at startup**

In `ph/main.py`, replace `CollectData.__init__()`:

```python
  def __init__(self, args):
    remaining, reset_epoch = gh.get_rate_limit_remaining()
    clamped_burst = max(0, min(args.burst_limit, remaining - 1000))
    if clamped_burst < args.burst_limit:
      reset_time = datetime.datetime.fromtimestamp(reset_epoch)
      print(
        "Warning: only {} API calls remaining (limit resets at {}). "
        "Burst limit clamped from {} to {}.".format(
            remaining, reset_time, args.burst_limit, clamped_burst),
        file=sys.stderr)

    gh.configure(clamped_burst, args.rate_limit,
                 args.cache_folder, args.cache_minutes,
                 args.debug)

    now = datetime.datetime.now(datetime.timezone.utc)
    time_range = datetime.timedelta(days=args.days)
    range_start = now - time_range

    self.releases = Release.get_all(args.repo, range_start)

    self.green_runs = WorkflowRun.get_all(
        args.repo, args.green_workflow, range_start)
    self.latency_runs = WorkflowRun.get_all(
        args.repo, args.latency_workflow, range_start)
    self.coverage_runs = WorkflowRun.get_all(
        args.repo, args.coverage_workflow, range_start)
    self.incremental_coverage_runs = WorkflowRun.get_all(
        args.repo, args.incremental_coverage_workflow, range_start)

    self.coverage_summaries = CoverageSummary.get_all(self.coverage_runs)
    self.merged_prs = PullRequest.get_all_merged(args.repo, range_start)

    self.latest_line_coverage = None
    if len(self.coverage_summaries):
      self.latest_line_coverage = self.coverage_summaries[-1].line_coverage

    self.average_incremental_coverage = PullRequest.average_incremental_coverage(
        self.merged_prs, self.incremental_coverage_runs)
```

- [ ] **Step 3: Smoke test**

```bash
cd ph && python -c "
from ph import gh
remaining, reset = gh.get_rate_limit_remaining()
print('Remaining:', remaining, 'Reset:', reset)
"
```

Expected: prints remaining count and a Unix timestamp.

- [ ] **Step 4: Commit**

```bash
git add ph/ph/gh.py ph/main.py
git commit -m "Query GitHub rate limit at startup; clamp burst to remaining-1000" -m "Prevents over-committing quota when other processes (e.g. CI) have already consumed some. 1000-call safety margin guards against concurrent usage. Logs a warning with reset time when clamped. /rate_limit endpoint does not itself consume quota." -m "Co-Authored-By: Claude Code (Claude Sonnet 4.6) <noreply@anthropic.com>"
```

---

### Task 10: Update CLAUDE.md for rate limit awareness

- [ ] **Step 1: Update CLAUDE.md**

Update the rate limiter bullet in "Key Behaviors":
```markdown
- **Rate limiter**: at startup, queries `/rate_limit` and clamps burst to
  `max(0, remaining - 1000)` to avoid over-committing shared quota. Warns
  to stderr if clamped. Sustained rate: 4000 calls/hour.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "Update CLAUDE.md: document rate limit awareness at startup" -m "Co-Authored-By: Claude Code (Claude Sonnet 4.6) <noreply@anthropic.com>"
```
