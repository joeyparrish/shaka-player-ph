# Shaka Player Project Health Metrics
# Copyright 2023 Google LLC
# SPDX-License-Identifier: Apache-2.0

import json

from . import gh


class CoverageSummary(object):
  def __init__(self, start_time, event, file_data):
    self.start_time = start_time
    self.event = event
    json_data = json.loads(file_data)

    total_data = json_data["total"]
    lines_covered = total_data["lines"]["covered"]
    lines_total = total_data["lines"]["total"]
    if lines_total:
      line_coverage = lines_covered / lines_total
    else:
      line_coverage = 1

    self.line_coverage = line_coverage

  @classmethod
  def from_line_coverage(cls, start_time, event, line_coverage):
    obj = cls.__new__(cls)
    obj.start_time = start_time
    obj.event = event
    obj.line_coverage = line_coverage
    return obj

  def serializable(self):
    return {
      "start": self.start_time.timestamp(),
      "event": self.event,
      "line_coverage": self.line_coverage,
    }

  @staticmethod
  def get_all(coverage_runs):
    results = []

    for run in coverage_runs:
      key = "coverage-summary:{}".format(run.run_id)
      cached = gh.disk_cache.get(key)
      if cached is not None:
        summary = CoverageSummary.from_line_coverage(
            run.start_time, run.event, json.loads(cached))
        results.append(summary)
        continue

      file_data = run.fetch_artifact("coverage", "coverage.json")
      if file_data is None:
        continue
      summary = CoverageSummary(run.start_time, run.event, file_data)
      gh.disk_cache.store(key, json.dumps(summary.line_coverage),
                          ttl_minutes=gh.LONG_TTL_MINUTES)
      results.append(summary)

    return sorted(results, key=lambda r: r.start_time)
