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

  def __init__(self, cache_folder):
    self.cache_folder = cache_folder
    os.makedirs(self.cache_folder, mode=0o755, exist_ok=True)
    self._prune_cache()

  def _prune_cache(self):
    now = time.time()
    for name in os.listdir(self.cache_folder):
      if not name.endswith(".json"):
        continue
      path = os.path.join(self.cache_folder, name)
      self._prune_file_if_expired(path, now)

  def _prune_file_if_expired(self, path, now):
    try:
      with open(path, "r") as f:
        data = json.load(f)

      expires_at = data.get("expires_at", 0)
      if expires_at < now:
        os.unlink(path)
    except Exception as e:
      print("Exception pruning cache file {}: {}".format(path, e),
            file=sys.stderr)
      self._delete_corrupt_file(path)

  def _delete_corrupt_file(self, path):
    try:
      os.unlink(path)
    except:
      pass

  def _path_for_key(self, key):
    sha = hashlib.sha256(key.encode("utf8")).hexdigest()
    return os.path.join(self.cache_folder, sha + ".json")

  def get(self, key):
    """Returns data if it exists and is valid, or None."""
    path = self._path_for_key(key)
    try:
      with open(path, "r") as f:
        stored = json.load(f)

      if stored.get("key") != key:
        return None

      expires_at = stored.get("expires_at", 0)
      if time.time() >= expires_at:
        return None

      if "json" in stored:
        return stored["json"]
      elif "text" in stored:
        return stored["text"]
      else:
        return base64.b64decode(stored["bytes"])
    except FileNotFoundError:
      return None
    except Exception as e:
      print("Exception loading cache file {}: {}".format(path, e),
            file=sys.stderr)
      self._delete_corrupt_file(path)
      return None

  def store(self, key, data, ttl_minutes):
    """Stores data in the cache."""
    path = self._path_for_key(key)
    try:
      with open(path, "w") as f:
        stored = {
          "time": time.time(),
          "expires_at": time.time() + ttl_minutes * 60,
          "key": key,
        }
        if type(data) is str:
          stored["text"] = data
        elif type(data) is bytes:
          stored["bytes"] = base64.b64encode(data).decode("utf8")
        else:
          stored["json"] = data
        json.dump(stored, f)
    except Exception as e:
      print("Exception storing cache file {}: {}".format(path, e),
            file=sys.stderr)
      self._delete_corrupt_file(path)
