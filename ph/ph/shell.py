# Shaka Player Project Health Metrics
# Copyright 2023 Google LLC
# SPDX-License-Identifier: Apache-2.0

import subprocess

def run_command(args, text=True):
  proc = subprocess.run(args, capture_output=True, text=text)
  if proc.returncode != 0:
    raise RuntimeError("Command failed:", args, proc.stdout, proc.stderr)
  return proc.stdout
