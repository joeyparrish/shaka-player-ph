# Shaka Player Project Health Metrics
# Copyright 2023 Google LLC
# SPDX-License-Identifier: Apache-2.0

import json

from . import diskcache
from . import ratelimit
from . import shell


rate_limiter = None
disk_cache = None


def configure(burst_limit, rate_limit_per_hour, cache_folder, cache_minutes):
  global rate_limiter
  global disk_cache
  rate_limiter = ratelimit.RateLimit(burst_limit, rate_limit_per_hour)
  disk_cache = diskcache.DiskCache(cache_folder, cache_minutes)

def _api_base(url_or_full_path, is_text):
  global rate_limiter
  global disk_cache

  data = disk_cache.get(url_or_full_path)
  if data is not None:
    return data

  rate_limiter.wait()
  args = ["gh", "api", url_or_full_path]
  data = shell.run_command(args, text=is_text)

  disk_cache.store(url_or_full_path, data)

  return data

def api_raw(url_or_path):
  return _api_base(url_or_path, is_text=False)

def api_single(url_or_path):
  output = _api_base(url_or_path, is_text=True)
  return json.loads(output)

def api_multiple(url_or_path, subkey=None):
  # Handle pagination explicitly at our level instead of letting the CLI do it,
  # so we can manage paging with respect to API rate limits.  We also
  # explicitly set a page size of 100 (maximum) to reduce the number of calls
  # compared to the default (30).
  if "?" in url_or_path:
    url_or_path += "&page_size=100"
  else:
    url_or_path += "?page_size=100"

  page_number = 1  # Page numbers start at 1, not 0.
  results = []
  while True:
    next_page_url = url_or_path + "&page={}".format(page_number)
    output = _api_base(next_page_url, is_text=True)

    next_page = json.loads(output)
    if subkey is not None:
      next_page = next_page[subkey]

    assert type(next_page) is list
    if len(next_page) == 0:
      break

    results.extend(next_page)
    page_number += 1

  return results
