# Shaka Player Project Health Metrics
# Copyright 2023 Google LLC
# SPDX-License-Identifier: Apache-2.0

import json
import re


# TODO: Figure out how to get karma to output relative paths only.
def _strip_git_dir(path):
  # Strip the path to the git clone, leaving only the source path within the
  # repo.
  return re.sub(r'.*?/(lib|ui)/', r'\1/', path)


def _coverage_lines(coverage_range):
  start_line = coverage_range["start"]["line"]
  end_line = coverage_range["end"]["line"]

  lines = set()
  for line in range(start_line, end_line + 1):
    lines.add(line)
  return lines


class CoverageDetails(object):
  def __init__(self, file_data):
    json_data = json.loads(file_data)

    self.files = {}

    # The structure is something like:
    # {
    #   "/path/to/lib/player.js": {
    #     "statementMap": { ... },
    #     "fnMap": { ... },
    #     "s": { ... }
    #   }
    # }
    for path, path_data in json_data.items():
      path = _strip_git_dir(path)

      statement_to_lines = {}
      instrumented_lines = set()

      # The function map is a structure to map where each function is in a
      # source file:
      # {
      #   "0": {
      #     "loc": {
      #       "start": {
      #         "line": 7,
      #         "column": 0
      #       },
      #       "end": {
      #         "line": 8,
      #         "column": 29
      #       }
      #     }
      #   },
      #   ...
      # }
      # We extract function locations and remove them from statement spans
      # below, so that we don't count (for example) class declaration statements
      # as containing all the lines of every method in the class.
      function_locations = []
      for key, value in path_data["fnMap"].items():
        lines = _coverage_lines(value["loc"])
        function_locations.append(lines)

      # The statement map is a structure to map where each statement is in a
      # source file:
      # {
      #   "0": {
      #     "start": {
      #       "line": 7,
      #       "column": 0
      #     },
      #     "end": {
      #       "line": 8,
      #       "column": 29
      #     }
      #   },
      #   ...
      # }
      for key, value in path_data["statementMap"].items():
        # All the lines of the statement, which may include other functions or
        # statements.
        lines = _coverage_lines(value)

        # Subtract from that the lines of any function that is a subset of
        # these lines.  By excluding entire methods before adding back their
        # child statements, we exclude empty lines in class methods.
        for function_lines in function_locations:
          if function_lines < lines:  # strict subset
            lines -= function_lines  # set subtraction

        # If this statement is inside the range of another statement, remove
        # this inner range from that outer one.  This is important because loops
        # and conditional statements contain their inner branches.
        for older_key, older_lines in statement_to_lines.items():
          # Check for a proper subset (lines contains all elements of
          # child_lines, but child_lines is not an equal set).
          if lines < older_lines:  # strict subset
            statement_to_lines[older_key] -= lines  # set subtraction

        statement_to_lines[key] = lines

      # Whatever is left in any statement, we count as instrumented.
      for key, lines in statement_to_lines.items():
        for line in lines:
          instrumented_lines.add(line)

      # The "s" field is a map from statement numbers to number of times
      # executed.
      executed_lines = set()
      for key, executed in path_data["s"].items():
        if executed:
          for line in statement_to_lines[key]:
            executed_lines.add(line)

      self.files[path] = {
        "instrumented": instrumented_lines,
        "executed": executed_lines,
      }
