#!/usr/bin/env python3

# Shaka Player Project Health Metrics
# Copyright 2023 Google LLC
# SPDX-License-Identifier: Apache-2.0

import argparse
import datetime
import json
import time
import sys

from ph import gh
from ph import formatters
from ph import shell
from ph.commitlog import CommitLog
from ph.coveragedetails import CoverageDetails
from ph.coveragesummary import CoverageSummary
from ph.pullrequest import PullRequest
from ph.release import Release
from ph.workflowrun import WorkflowRun


def parse_args():
  parser = argparse.ArgumentParser(
      description="Take project health (PH) measurements",
      formatter_class=argparse.ArgumentDefaultsHelpFormatter)
  parser.add_argument(
      "--days", "-d", type=int, help="Time period in days", default=90)
  parser.add_argument(
      "--repo", "-r", help="GitHub repo name",
      default="shaka-project/shaka-player")
  parser.add_argument(
      "--green-workflow", "-gw",
      help="GitHub Actions workflow (filename or filename:event)"
           " for greenness and flake measurements",
      default="selenium-lab-tests.yaml:schedule")
  parser.add_argument(
      "--latency-workflow", "-lw",
      help="GitHub Actions workflow (filename or filename:event)"
           " for latency measurements",
      default="build-and-test.yaml:pull_request")
  parser.add_argument(
      "--coverage-workflow", "-cw",
      help="GitHub Actions workflow (filename or filename:event)"
           " for coverage measurements",
      default="selenium-lab-tests.yaml:schedule")
  parser.add_argument(
      "--incremental-coverage-workflow", "-iw",
      help="GitHub Actions workflow (filename or filename:event)"
           " for incremental coverage measurements",
      default="build-and-test.yaml:pull_request")
  parser.add_argument(
      "--json", "-j", action="store_true", help="Output in JSON", default=False)

  return parser.parse_args()


class CollectData(object):
  def __init__(self, args):
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


def print_json(args, data):
  print(json.dumps({
    "range": args.days,
    "release_duration": Release.average_duration(data.releases),
    "release_granularity": Release.average_granularity(data.releases),
    "test_greenness": WorkflowRun.average_greenness(data.green_runs),
    "test_flakiness": WorkflowRun.average_flakiness(data.green_runs),
    "test_latency": WorkflowRun.average_duration(data.latency_runs),
    "test_coverage": data.latest_line_coverage,
    "incremental_coverage": data.average_incremental_coverage,
    "releases": list(map(lambda r: r.serializable(), data.releases)),
    "green_runs": list(map(lambda r: r.serializable(), data.green_runs)),
    "latency_runs": list(map(lambda r: r.serializable(), data.latency_runs)),
    "coverage_summaries": list(map(lambda s: s.serializable(), data.coverage_summaries)),
    "merged_prs": list(map(lambda pr: pr.serializable(), data.merged_prs)),
  }))


def print_text_tables(args, data):
  print("Release".ljust(10), "Duration".ljust(15), "Granularity")
  print("=======".ljust(10), "========".ljust(15), "===========")
  for release in data.releases:
    duration = release.duration()
    if duration is not None:
      duration = duration.total_seconds()
    granularity = release.num_commits
    print(release.name.ljust(10), formatters.duration(duration).ljust(15),
          granularity)

  print()

  print("Start".ljust(30), "Event".ljust(15), "Passed".ljust(10), "Flaky")
  print("=====".ljust(30), "=====".ljust(15), "======".ljust(10), "=====")
  for run in data.green_runs:
    start = run.start_time.isoformat()
    print(start.ljust(30), run.event.ljust(15), str(run.passed).ljust(10),
          run.flaky)

  print()

  print("Start".ljust(30), "Event".ljust(15), "Duration")
  print("=====".ljust(30), "=====".ljust(15), "========")
  for run in data.latency_runs:
    start = run.start_time.isoformat()
    duration = run.duration.total_seconds()
    print(start.ljust(30), run.event.ljust(15),
          formatters.duration(duration))

  print()

  print("Start".ljust(30), "Event".ljust(15), "Coverage")
  print("=====".ljust(30), "=====".ljust(15), "========")
  for summary in data.coverage_summaries:
    start = summary.start_time.isoformat()
    print(start.ljust(30), summary.event.ljust(15),
          formatters.percentage(summary.line_coverage))

  print()

  print("PR #".ljust(10), "Incremental Coverage")
  print("====".ljust(10), "====================")
  for pr in data.merged_prs:
    print(str(pr.number).ljust(10),
          formatters.percentage(pr.incremental_coverage))

  print()

  print("Average release duration over", args.days, "days:",
        formatters.duration(Release.average_duration(data.releases)))
  print("Average release granularity over", args.days, "days:",
        formatters.rounded(Release.average_granularity(data.releases), "commits"))
  print("Average test greenness over", args.days, "days:",
        formatters.percentage(WorkflowRun.average_greenness(data.green_runs)))
  print("Average test flakiness over", args.days, "days:",
        formatters.percentage(WorkflowRun.average_flakiness(data.green_runs)))
  print("Average test latency over", args.days, "days:",
        formatters.duration(WorkflowRun.average_duration(data.latency_runs)))
  print("Latest test coverage:",
        formatters.percentage(data.latest_line_coverage))
  print("Average incremental test coverage over", args.days, "days:",
        formatters.percentage(data.average_incremental_coverage))


def main():
  args = parse_args()
  data = CollectData(args)

  if args.json:
    print_json(args, data)
  else:
    print_text_tables(args, data)

  num_calls = gh.rate_limiter.num_calls
  minutes = (time.time() - gh.rate_limiter.start_time) / 60
  print("Made {} GH API calls over {:.1f} minutes.".format(num_calls, minutes),
        file=sys.stderr)


if __name__ == "__main__":
  main()
