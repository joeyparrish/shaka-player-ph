# Shaka Player Project Health Metrics
# Copyright 2023 Google LLC
# SPDX-License-Identifier: Apache-2.0

import time

class RateLimit(object):
  """Rate limit calls to an arbitrary thing."""

  def __init__(self, burst_limit, max_calls_per_hour):
    """Allow up to burst_limit calls beyond the limit (max_calls_per_hour)."""
    self.burst_limit = burst_limit
    self.seconds_per_call = 3600 / max_calls_per_hour
    self.start_time = time.time()
    self.num_calls = 0

  def wait(self):
    """Returns when another call would not break the rate limit."""
    self.num_calls += 1

    # Are we over our burst budget?  Compute how long we "should" wait to make
    # this many calls without considering the burst behavior.
    now = time.time()
    end_time = self.start_time + (self.num_calls * self.seconds_per_call)

    # See how far in the future that is, computed in number of calls.  This is
    # how far over-budget we are without the burst behavior.
    over_budget_calls = (end_time - now) / self.seconds_per_call

    # Now compare that to the burst limit.  If we're over by less than the
    # burst limit, we can proceed.
    if over_budget_calls <= self.burst_limit:
      # Within the burst limit.  OK to proceed.
      return

    # Now we are over the burst limit, so we decide how long to wait to get
    # back under that limit.
    over_budget_calls -= self.burst_limit
    wait_seconds = over_budget_calls * self.seconds_per_call

    # It should be positive, but sleep() throws if it's not.
    if wait_seconds > 0:
      time.sleep(wait_seconds)
