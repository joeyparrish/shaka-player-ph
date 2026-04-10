# Shaka Player Project Health Metrics
# Copyright 2023 Google LLC
# SPDX-License-Identifier: Apache-2.0

import functools
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
