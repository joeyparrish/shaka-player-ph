# Shaka Player Project Health Metrics
# Copyright 2023 Google LLC
# SPDX-License-Identifier: Apache-2.0

import base64
import hashlib
import json
import os
import time
import sys

class DiskCache(object):
  """Cache some arbitrary data on disk."""

  def __init__(self, cache_folder, expiration_minutes):
    self.cache_folder = cache_folder
    self.expiration_minutes = expiration_minutes

    if not os.path.exists(self.cache_folder):
      os.mkdir(self.cache_folder, mode=0o755)

    self._prune_cache()

  def _prune_cache(self):
    expired_limit = time.time() - (self.expiration_minutes * 60)

    for name in os.listdir(self.cache_folder):
      path = os.path.join(self.cache_folder, name)
      self._prune_file_if_expired(path, expired_limit)

  def _prune_file_if_expired(self, path, expired_limit):
    try:
      with open(path, "r") as f:
        data = json.load(f)

      if data["time"] < expired_limit:
        os.unlink(path)
    except Exception as e:
      print("Exception pruning cache file {}: {}".format(path, e),
            file=sys.stderr)
      self._delete_corrupt_file(path)

  def _delete_corrupt_file(self, path):
    # Try, unconditionally, and ignoring errors, to delete the file.
    try:
      os.unlink(path)
    except:
      pass

  def _path_for_key(self, key):
    sha = hashlib.sha256(key.encode("utf8")).hexdigest()
    return os.path.join(self.cache_folder, sha + ".json")

  def get(self, key):
    """Returns data if it exists, or None if it doesn't."""
    path = self._path_for_key(key)
    try:
      with open(path, "r") as f:
        stored = json.load(f)
        if "text" in stored:
          return stored["text"]
        else:
          return base64.b64decode(stored["bytes"])
    except FileNotFoundError as e:
      return None
    except Exception as e:
      print("Exception loading cache file {}: {}".format(path, e),
            file=sys.stderr)
      self._delete_corrupt_file(path)
      return None

  def store(self, key, data):
    """Stores data in the cache."""
    path = self._path_for_key(key)
    try:
      with open(path, "w") as f:
        stored = {
          "time": time.time(),
          "key": key,
        }
        if type(data) is str:
          stored["text"] = data
        elif type(data) is bytes:
          stored["bytes"] = base64.b64encode(data).decode("utf8")
        else:
          raise RuntimeError("Unexpected data type in cache: {}".format(
                             type(data)))
        json.dump(stored, f)
    except Exception as e:
      print("Exception storing cache file {}: {}".format(path, e),
            file=sys.stderr)
      self._delete_corrupt_file(path)
