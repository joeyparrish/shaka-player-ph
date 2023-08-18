# Shaka Player Project Health Metrics
# Copyright 2023 Google LLC
# SPDX-License-Identifier: Apache-2.0

import dateutil.parser

def _parse_date(date_string):
  return dateutil.parser.parse(date_string)

def average(things, should_count, get_value, get_num_things=lambda thing: 1):
  total_things = 0
  total_values = 0

  for thing in things:
    if should_count(thing):
      total_values += get_value(thing)
      total_things += get_num_things(thing)

  if total_things == 0:
    return None

  return total_values / total_things

def load_and_filter(results, constructor, should_load, sort_by):
  things = []

  for data in results:
    if should_load(data):
      thing = constructor(data)
      things.append(thing)

  return sorted(things, key=sort_by)

def load_and_filter_by_time(
    results, constructor, time_field, min_time, sort_by):
  return load_and_filter(
      results,
      constructor=constructor,
      should_load=lambda d: (
          d[time_field] is not None and _parse_date(d[time_field]) >= min_time),
      sort_by=sort_by)
