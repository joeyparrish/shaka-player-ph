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

      expires_at = data.get(
          "expires_at",
          data["time"] + self.expiration_minutes * 60)
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

      expires_at = stored.get(
          "expires_at",
          stored["time"] + self.expiration_minutes * 60)
      if time.time() >= expires_at:
        return None

      if "text" in stored:
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

  def store(self, key, data, ttl_minutes=None):
    """Stores data in the cache."""
    if ttl_minutes is None:
      ttl_minutes = self.expiration_minutes
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
          raise RuntimeError("Unexpected data type in cache: {}".format(
                             type(data)))
        json.dump(stored, f)
    except Exception as e:
      print("Exception storing cache file {}: {}".format(path, e),
            file=sys.stderr)
      self._delete_corrupt_file(path)
