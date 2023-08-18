# Shaka Player Project Health Metrics
# Copyright 2023 Google LLC
# SPDX-License-Identifier: Apache-2.0

import dateutil.parser

from . import base
from . import gh
from .coveragedetails import CoverageDetails


class PullRequest(object):
  def __init__(self, repo, data):
    self.repo = repo

    merged_at = data["merged_at"]
    updated_at = data["updated_at"]

    # If it's merged, that's the timestamp we care about.  Otherwise, most
    # recent update is fine.
    self.timestamp = dateutil.parser.parse(merged_at or updated_at)

    self.number = data["number"]
    self.merged = data["merged_at"] is not None
    self.merge_sha = data["merge_commit_sha"]
    self.head_sha = data["head"]["sha"]

    self.changes = None
    self.num_covered_lines = None
    self.num_instrumented_lines = None
    self.incremental_coverage = None

  def serializable(self):
    return {
      "number": self.number,
      "timestamp": self.timestamp.timestamp(),
      "merged": self.merged,
      "num_covered_lines": self.num_covered_lines,
      "num_instrumented_lines": self.num_instrumented_lines,
    }

  def _matching_workflow_run(self, runs):
    # Start with the most recent runs.
    for run in sorted(runs, key=lambda r: r.start_time, reverse=True):
      if run.head_sha == self.head_sha:
        return run
    return None

  def _load_changes(self):
    self.changes = {}

    api_path = "/repos/%s/commits/%s" % (self.repo, self.merge_sha)
    files = gh.api_multiple(api_path, "files")

    for file_data in files:
      # The patch field is missing for binary files.  Skip those.
      if "patch" not in file_data:
        continue

      filename = file_data["filename"]
      patch = file_data["patch"]

      touched_lines = []
      line_number = None
      for line in patch.split("\n"):
        if line[0] == "@":
          # Turns a header like "@@ -749,7 +757,19 @@ foo" into line number 757.
          # Note that the last part of the new file range could be omitted:
          # "@@ -0,0 +1 @@ foo"
          new_file_range = line.split("+")[1].split(" @@")[0]
          line_number = int(new_file_range.split(",")[0])
        elif line[0] == " ":
          line_number += 1
        elif line[0] == "+":
          touched_lines.append(line_number)
          line_number += 1

      self.changes[filename] = touched_lines

  def _load_incremental_coverage(self, runs):
    if self.num_covered_lines is not None:
      # Already loaded.
      return

    run = self._matching_workflow_run(runs)
    if run is None:
      # No matching run.
      return

    file_data = run.fetch_artifact("coverage", "coverage-details.json")
    if file_data is None:
      # No coverage details available.
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
          # Only count the instrumented lines, not whitespace or comments.
          if line in instrumented_lines:
            self.num_instrumented_lines += 1
            if line in executed_lines:
              self.num_covered_lines += 1

    if self.num_instrumented_lines == 0:
      self.incremental_coverage = None
    else:
      self.incremental_coverage = (
          self.num_covered_lines / self.num_instrumented_lines)

  @staticmethod
  def get(repo, number):
    api_path = "/repos/%s/pulls/%d" % (repo, number)
    data = gh.api_single(api_path)
    return PullRequest(repo, data)

  @staticmethod
  def get_all_merged(repo, range_start):
    results = gh.api_multiple("/repos/%s/pulls?state=closed" % repo)

    return base.load_and_filter_by_time(
        results,
        constructor=lambda data: PullRequest(repo, data),
        time_field="merged_at",
        min_time=range_start,
        sort_by=lambda pr: pr.timestamp)

  @staticmethod
  def average_incremental_coverage(merged_prs, workflow_runs):
    for pr in merged_prs:
      pr._load_changes()
      pr._load_incremental_coverage(workflow_runs)

    return base.average(
        merged_prs,
        should_count=lambda pr: pr.num_instrumented_lines is not None,
        get_value=lambda pr: pr.num_covered_lines,
        get_num_things=lambda pr: pr.num_instrumented_lines)
