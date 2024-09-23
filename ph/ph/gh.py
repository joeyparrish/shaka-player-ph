# Shaka Player Project Health Metrics
# Copyright 2023 Google LLC
# SPDX-License-Identifier: Apache-2.0

import json

from . import ratelimit
from . import shell

# Suffixes "h", "m", "s", etc.  Passed to GH CLI.  Should be long enough that
# we never request the same thing twice in a workflow run.
CACHE_TIME = "12h"

# Rate limit for the GitHub API.  There is a limit of 5,000 requests per hour
# for the whole user account.  We don't want to use up all of them, since the
# account does other things on other repos, too.
MAX_REQUESTS_PER_HOUR = 3000

rate_limiter = ratelimit.RateLimit(MAX_REQUESTS_PER_HOUR)


def _api_base(url_or_full_path, text):
  rate_limiter.wait()
  args = ["gh", "api", "--cache", CACHE_TIME, url_or_full_path]
  return shell.run_command(args, text=text)

def api_raw(url_or_path):
  return _api_base(url_or_path, text=False)

def api_single(url_or_path):
  output = _api_base(url_or_path, text=True)
  return json.loads(output)

def api_multiple(url_or_path, subkey=None):
  # Handle pagination explicitly at our level instead of letting the CLI do it,
  # so we can manage paging with respect to API rate limits.  We also
  # explicitly set a page size of 100 (maximum) to reduce the number of calls
  # compared to the default (30).
  if '?' in url_or_path:
    url_or_path += '&page_size=100'
  else:
    url_or_path += '?page_size=100'

  page_number = 1  # Page numbers start at 1, not 0.
  results = []
  while True:
    next_page_url = url_or_path + '&page={}'.format(page_number)
    output = _api_base(next_page_url, text=True)

    next_page = json.loads(output)
    if subkey is not None:
      next_page = next_page[subkey]

    assert type(next_page) is list
    if len(next_page) == 0:
      break

    results.extend(next_page)
    page_number += 1

  return results
