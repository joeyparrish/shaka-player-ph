# Shaka Player Project Health Metrics
# Copyright 2023 Google LLC
# SPDX-License-Identifier: Apache-2.0

import time

class RateLimit(object):
  """Rate limit calls to an arbitrary thing."""

  def __init__(self, calls_per_hour):
    self.seconds_per_call = 3600 / calls_per_hour
    self.first_call_time = None
    self.num_calls = 0

  def wait(self):
    """Returns when another call would not break the rate limit."""
    self.num_calls += 1

    if self.first_call_time is None:
      now = time.time()
      self.first_call_time = now
      return

    min_elapsed_seconds = self.seconds_per_call * self.num_calls
    min_timestamp_to_proceed = self.first_call_time + min_elapsed_seconds
    now = time.time()
    wait_seconds = min_timestamp_to_proceed - now
    if wait_seconds > 0:
      time.sleep(wait_seconds)
