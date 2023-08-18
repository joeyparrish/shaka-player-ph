# Shaka Player Project Health Metrics
# Copyright 2023 Google LLC
# SPDX-License-Identifier: Apache-2.0

import json

from . import shell


def _api_base(url_or_full_path, args, text):
  args = ["gh", "api", "--cache", "10m", *args, url_or_full_path]
  return shell.run_command(args, text=text)

def api_raw(url_or_path):
  return _api_base(url_or_path, [], text=False)

def api_single(url_or_path):
  output = _api_base(url_or_path, [], text=True)
  return json.loads(output)

def api_multiple(url_or_path, subkey=None):
  output = _api_base(url_or_path, ["--paginate"], text=True)

  # Paginate output is multiple JSON objects concatenated together.
  # So we need to do a more detailed parse/decode for this than
  # json.loads(output).
  decoder = json.JSONDecoder()
  output = output.lstrip()
  results = []
  while output:
    next_page, index = decoder.raw_decode(output)
    if subkey is not None:
      next_page = next_page[subkey]
    results.extend(next_page)
    output = output[index:].lstrip()

  return results
