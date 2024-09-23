# Shaka Player Project Health Metrics
# Copyright 2023 Google LLC
# SPDX-License-Identifier: Apache-2.0

import dateutil.parser
import requests

from . import base
from . import gh
from .commitlog import CommitLog


# TODO: Generalize this
CDN_URL_TEMPLATE = "https://ajax.googleapis.com/ajax/libs/shaka-player/%s/shaka-player.compiled.js"


def _tag_to_version(tag):
  return tag.replace("v", "").split(".")

def _version_to_tag(version):
  return "v" + ".".join(version)


class Release(object):
  def __init__(self, repo, data):
    self.repo = repo
    self.name = data["tag_name"]

    # TODO: Generalize this, default to workflow start and end time.
    self.start_time = dateutil.parser.parse(data["published_at"])
    self.end_time = None
    self.num_commits = None

    # NOTE: This is a computed branch name and it could be wrong.  We want to
    # use these when possible, because we can cache results from the GitHub API
    # when we load a commit log for the branch instead of for each individual
    # release.
    version = _tag_to_version(self.name)
    branch = _version_to_tag(version[0:2] + ["x"])
    self.branch = branch

    self.load_end_time()
    self.load_num_commits()

  def duration(self):
    if self.end_time is None:
      return None

    return self.end_time - self.start_time

  # TODO: Generalize this, default to release time
  def load_end_time(self):
    bare_version = self.name.replace("v", "")
    url = CDN_URL_TEMPLATE % bare_version
    response = requests.get(url)
    last_modified = response.headers.get("last-modified")
    if last_modified is not None:
      self.end_time = dateutil.parser.parse(last_modified)
    else:
      self.end_time = None

  def load_num_commits(self):
    try:
      # First load from the computed branch name.  This is _almost_ always
      # correct and we get cache benefits WRT the GitHub API when it is.
      self._load_num_commits_internal(self.branch)
    except RuntimeError as e:
      # Fall back to the commit log from this exact tag.  This will always be a
      # cache miss, but should also always be accurate.
      self._load_num_commits_internal(self.name)

  def _load_num_commits_internal(self, ref):
    # Pull the commit log starting from this specific ref.  It could be a
    # branch (more cacheable) or a tag (always accurate).
    commit_logs = CommitLog.get_all(self.repo, ref, None)

    # Index of the tag in the commit log (0 = most recent)
    tag_index = None
    # Index of the next tag after this one in the commit log (before it in time)
    next_tag_index = None

    for index, log in enumerate(commit_logs):
      # If we already found the release we wanted, and then we find another tag,
      # note the index of this other tag.  The difference is the number of
      # commits in this release.
      if tag_index is not None and len(log.tags) != 0:
        next_tag_index = index
        break

      # If this is the tag we wanted (self.name == name of release), then note
      # the index.
      if self.name in log.tags:
        tag_index = index

    if tag_index is None:
      raise RuntimeError("Unable to find tag %s in branch %s" % (
          self.name, self.branch))

    if next_tag_index is None:
      raise RuntimeError("Unable to find tag before %s in branch %s" % (
          self.name, self.branch))

    # Exclude 1 for the release PR itself.
    self.num_commits = next_tag_index - tag_index - 1

  def serializable(self):
    return {
      "name": self.name,
      "start": self.start_time.timestamp(),
      "duration": self.duration().total_seconds() if self.duration() else None,
      "num_commits": self.num_commits,
    }

  @staticmethod
  def get_all(repo, range_start):
    # Stop paging results in when we see releases published earlier than
    # range_start.
    def stop_predicate(results):
      for item in results[::-1]:
        if item["published_at"] is not None:
          release_date = dateutil.parser.parse(item["published_at"])
          return release_date <= range_start

    results = gh.api_multiple("/repos/%s/releases" % repo, subkey=None,
                              stop_predicate=stop_predicate)

    # This filter is more fine-grained, and will remove results that are too
    # old, but came in a page with results we needed.
    return base.load_and_filter_by_time(
        results,
        constructor=lambda data: Release(repo, data),
        time_field="published_at",
        min_time=range_start,
        sort_by=lambda r: r.start_time)

  @staticmethod
  def average_duration(releases):
    return base.average(
        releases,
        should_count=lambda r: r.end_time is not None,
        get_value=lambda r: r.duration().total_seconds())

  @staticmethod
  def average_granularity(releases):
    return base.average(
        releases,
        # Skip .0 releases, which are a branch point and therefore show up as
        # being made up of 0 commits.
        should_count=lambda r: not r.name.endswith(".0"),
        get_value=lambda r: r.num_commits)
