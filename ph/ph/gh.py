# Shaka Player Project Health Metrics
# Copyright 2023 Google LLC
# SPDX-License-Identifier: Apache-2.0

import json
import re
import sys

import requests as requests_lib

from . import shell
from .diskcache import DiskCache
from .ratelimit import RateLimit


SHORT_TTL_MINUTES = 120  # 2 hours
LONG_TTL_MINUTES = 144_000  # 100 days

rate_limiter = None
disk_cache = None
debug_api = False


def get_rate_limit_remaining():
  """Query actual remaining GitHub API quota. Does not consume quota."""
  raw = shell.run_command(["gh", "api", "/rate_limit"], text=True)
  data = json.loads(raw)
  core = data["resources"]["core"]
  return core["remaining"], core["reset"]


def configure(burst_limit, rate_limit_per_hour, cache_folder, debug):
  global rate_limiter
  global disk_cache
  global debug_api

  rate_limiter = RateLimit(burst_limit, rate_limit_per_hour)
  disk_cache = DiskCache(cache_folder)
  debug_api = debug


def http_head(url):
  """Fetch HTTP headers via HEAD request. Caches with long TTL."""
  cached = disk_cache.get(url)
  if cached is not None:
    return json.loads(cached)

  response = requests_lib.head(url)
  headers = {k.lower(): v for k, v in response.headers.items()}
  disk_cache.store(url, json.dumps(headers), ttl_minutes=LONG_TTL_MINUTES)
  return headers


def _is_url_immutable(url):
  return re.search(r'/commits/[0-9a-f]{40}', url)


def _api_base(url_or_full_path, is_text, ttl_minutes, cache=True):
  global rate_limiter
  global disk_cache
  global debug_api

  if cache:
    data = disk_cache.get(url_or_full_path)

    if debug_api:
      if data is None:
        print("CACHE MISS: {}".format(url_or_full_path), file=sys.stderr)
      else:
        print("CACHE HIT: {}".format(url_or_full_path), file=sys.stderr)

    if data is not None:
      return data
  elif debug_api:
    print("CACHE SKIP: {}".format(url_or_full_path), file=sys.stderr)

  rate_limiter.wait()
  args = ["gh", "api", url_or_full_path]
  data = shell.run_command(args, text=is_text)

  if cache:
    disk_cache.store(url_or_full_path, data, ttl_minutes=ttl_minutes)

  return data


def api_raw(url_or_path, cache=True):
  return _api_base(url_or_path,
      is_text=False, ttl_minutes=SHORT_TTL_MINUTES, cache=cache)


def api_single(url_or_path, is_immutable_cb=None):
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

  is_immutable = _is_url_immutable(url_or_path)
  if is_immutable_cb is not None:
    is_immutable = is_immutable_cb(parsed)

  ttl_minutes = LONG_TTL_MINUTES if is_immutable else SHORT_TTL_MINUTES

  disk_cache.store(url_or_path, raw, ttl_minutes=ttl_minutes)
  return parsed


def api_multiple(url_or_path, subkey=None, stop_predicate=None):
  if "?" in url_or_path:
    url_or_path += "&page_size=100"
  else:
    url_or_path += "?page_size=100"

  is_immutable = _is_url_immutable(url_or_path)
  ttl_minutes = LONG_TTL_MINUTES if is_immutable else SHORT_TTL_MINUTES

  page_number = 1
  results = []
  while True:
    next_page_url = url_or_path + "&page={}".format(page_number)
    output = _api_base(next_page_url, is_text=True, ttl_minutes=ttl_minutes)

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
