# Shaka Player Project Health Metrics
# Copyright 2023 Google LLC
# SPDX-License-Identifier: Apache-2.0

ONE_MINUTE = 60
ONE_HOUR = ONE_MINUTE * 60
ONE_DAY = ONE_HOUR * 24

def duration(seconds):
  if seconds is None:
    return "None"
  if seconds < ONE_MINUTE:
    return "%.1f seconds" % seconds
  if seconds < ONE_HOUR:
    return "%.1f minutes" % (seconds / ONE_MINUTE)
  if seconds < ONE_DAY:
    return "%.1f hours" % (seconds / ONE_HOUR)
  return "%.1f days" % (seconds / ONE_DAY)

def percentage(value):
  if value is None:
    return None
  return "%.1f%%" % (value * 100)

def rounded(value, units):
  if value is None:
    return None
  return str(round(value, 1)) + " " + units
