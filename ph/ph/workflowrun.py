# Shaka Player Project Health Metrics
# Copyright 2023 Google LLC
# SPDX-License-Identifier: Apache-2.0

import dateutil.parser
import functools
import io
import sys
import zipfile

from . import base
from . import gh


class WorkflowRun(object):
  def __init__(self, data):
    self.run_id = data["id"]
    self.head_sha = data["head_sha"]
    self.event = data["event"]
    self.trigger_time = dateutil.parser.parse(data["created_at"])
    self.start_time = dateutil.parser.parse(data["run_started_at"])
    self.end_time = dateutil.parser.parse(data["updated_at"])
    self.duration = self.end_time - self.start_time
    self.artifacts_url = data["artifacts_url"]
    self.logs_url = data["logs_url"]
    self.html_url = data["html_url"]  # URL in GitHub Actions web interface

    conclusion = data["conclusion"]

    if conclusion == "success":
      self.passed = True
    elif conclusion == "failure":
      self.passed = False
    else:
      self.passed = None  # canceled, etc

    self.previous_run = None
    self.flaky = False

    previous_attempt_url = data["previous_attempt_url"]
    if previous_attempt_url:
      self.previous_run = WorkflowRun.load_by_url(previous_attempt_url)
      self.flaky = self.passed and not self.previous_run.passed

  def serializable(self):
    return {
      "html_url": self.html_url,
      "trigger": self.trigger_time.timestamp(),
      "start": self.start_time.timestamp(),
      "duration": self.duration.total_seconds(),
      "event": self.event,
      "passed": self.passed,
      "flaky": self.flaky,
    }

  def fetch_artifact(self, name, filename):
    results = gh.api_multiple(self.artifacts_url, "artifacts")

    zip_data = None
    for data in results:
      if data["name"] == name:
        try:
          zip_data = gh.api_raw(data["archive_download_url"])
          break
        except RuntimeError:
          print(
            'Failed to fetch artifact for run from {}'.format(self.start_time),
            file=sys.stderr)

    if zip_data is None:
      return None

    with zipfile.ZipFile(io.BytesIO(zip_data), 'r') as f:
      try:
        return f.read(filename)
      except KeyError as e:
        return None

  def fetch_logs(self, pattern):
    try:
      zip_data = gh.api_raw(self.logs_url)
    except RuntimeError:
      # The run was cancelled or logs have gone out of retention
      return None

    if zip_data is None:
      return None

    output = {}
    with zipfile.ZipFile(io.BytesIO(zip_data), 'r') as f:
      for filename in f.namelist():
        if pattern.match(filename):
          output[filename] = f.read(filename)
    return output

  @staticmethod
  @functools.lru_cache
  def get_all(repo, workflow, range_start):
    if ":" in workflow:
      workflow_filename, event_filter = workflow.split(":")
    else:
      workflow_filename = workflow
      event_filter = None

    api_path = "/repos/%s/actions/workflows/%s/runs" % (repo, workflow_filename)
    api_path += "?created=>=%s" % range_start.strftime("%Y-%m-%dT%H:%M:%SZ")
    results = gh.api_multiple(api_path, "workflow_runs")

    return base.load_and_filter(
        results,
        constructor=WorkflowRun,
        should_load=lambda d: not event_filter or d["event"] == event_filter,
        sort_by=lambda r: r.start_time)

  @staticmethod
  def load_by_url(url):
    data = gh.api_single(url)
    return WorkflowRun(data)

  @staticmethod
  def average_greenness(runs):
    return base.average(
        runs,
        should_count=lambda r: r.passed is not None,
        get_value=lambda r: 1 if r.passed else 0)

  @staticmethod
  def average_flakiness(runs):
    return base.average(
        runs,
        should_count=lambda r: r.passed is not None,
        get_value=lambda r: 1 if r.flaky else 0)

  @staticmethod
  def average_duration(runs):
    return base.average(
        runs,
        should_count=lambda r: r.passed is not None,
        get_value=lambda r: r.duration.total_seconds())
