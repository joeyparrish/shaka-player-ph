# Shaka Player Project Health Metrics
# Copyright 2023 Google LLC
# SPDX-License-Identifier: Apache-2.0

from . import shell

class CommitLog(object):
  def __init__(self, timestamp, tags):
    self.timestamp = timestamp
    self.tags = tags

  @staticmethod
  def get_all(repo, branch, range_start):
    args = ["git", "fetch", "--tags", "https://github.com/%s" % repo, branch]
    shell.run_command(args)

    args = [
      "git", "log", "--format=%ct %D", "--decorate-refs=tags/*", "FETCH_HEAD",
    ]
    output = shell.run_command(args)
    lines = output.strip().split("\n")

    logs = []
    for line in lines:
      timestamp_string, tag_string = (line + " ").split(" ", 1)
      timestamp = int(timestamp_string)
      if range_start is not None and timestamp < range_start.timestamp():
        break

      tags = tag_string.strip().split(", ")
      if len(tags) == 1 and tags[0] == "":
        tags = []
      else:
        tags = list(map(lambda x: x.replace("tag: ", ""), tags))

      logs.append(CommitLog(timestamp, tags))

    return logs
