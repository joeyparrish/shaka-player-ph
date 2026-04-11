import time
import pytest
from ph.diskcache import DiskCache


def test_store_and_get_default_ttl(tmp_path):
    cache = DiskCache(str(tmp_path), expiration_minutes=120)
    cache.store("key1", "value1", ttl_minutes=120)
    assert cache.get("key1") == "value1"


def test_get_returns_none_for_expired_entry(tmp_path):
    cache = DiskCache(str(tmp_path), expiration_minutes=120)
    cache.store("key1", "value1", ttl_minutes=0)
    # ttl_minutes=0 means expires immediately
    time.sleep(0.01)
    assert cache.get("key1") is None


def test_long_ttl_survives_default_expiry(tmp_path):
    cache = DiskCache(str(tmp_path), expiration_minutes=0)
    cache.store("key1", "value1", ttl_minutes=144000)
    # Default TTL is 0 (expired immediately), but this entry has long TTL
    assert cache.get("key1") == "value1"


def test_bytes_round_trip(tmp_path):
    cache = DiskCache(str(tmp_path), expiration_minutes=120)
    cache.store("key1", b"\x00\x01\x02", ttl_minutes=120)
    assert cache.get("key1") == b"\x00\x01\x02"


def test_key_mismatch_returns_none(tmp_path):
    """Simulate a SHA256 collision by writing a cache file with a different key."""
    import json, hashlib, os
    cache = DiskCache(str(tmp_path), expiration_minutes=120)
    # Store under key1, but manually write a file that claims to be key2
    real_key = "key1"
    sha = hashlib.sha256(real_key.encode("utf8")).hexdigest()
    path = os.path.join(str(tmp_path), sha + ".json")
    with open(path, "w") as f:
        json.dump({
            "time": time.time(),
            "expires_at": time.time() + 7200,
            "key": "key2",  # wrong key
            "text": "value1",
        }, f)
    assert cache.get("key1") is None


def test_entry_without_expires_at_is_cache_miss(tmp_path):
    """Entries lacking expires_at (old format) are always treated as expired."""
    import json, hashlib, os
    cache = DiskCache(str(tmp_path), expiration_minutes=120)
    real_key = "key1"
    sha = hashlib.sha256(real_key.encode("utf8")).hexdigest()
    path = os.path.join(str(tmp_path), sha + ".json")
    with open(path, "w") as f:
        json.dump({
            "time": time.time(),
            "key": real_key,
            "text": "value1",
        }, f)
    assert cache.get("key1") is None


def test_entry_without_expires_at_is_pruned(tmp_path):
    """Entries lacking expires_at are pruned immediately."""
    import json, hashlib, os
    cache = DiskCache(str(tmp_path), expiration_minutes=120)
    real_key = "key1"
    sha = hashlib.sha256(real_key.encode("utf8")).hexdigest()
    path = os.path.join(str(tmp_path), sha + ".json")
    with open(path, "w") as f:
        json.dump({
            "time": time.time(),
            "key": real_key,
            "text": "value1",
        }, f)
    cache._prune_cache()
    assert not os.path.exists(path)


def test_prune_removes_expired_entry(tmp_path):
    cache = DiskCache(str(tmp_path), expiration_minutes=120)
    cache.store("key1", "value1", ttl_minutes=0)
    time.sleep(0.01)
    # Manually trigger prune (normally runs at startup)
    cache._prune_cache()
    import os
    assert len(os.listdir(str(tmp_path))) == 0


def test_prune_keeps_long_ttl_entry(tmp_path):
    cache = DiskCache(str(tmp_path), expiration_minutes=0)
    cache.store("key1", "value1", ttl_minutes=144000)
    cache._prune_cache()
    assert cache.get("key1") == "value1"


def test_prune_skips_non_json_files(tmp_path):
    """Non-JSON files in the cache directory are not deleted by prune."""
    import os
    non_json = tmp_path / "README.txt"
    non_json.write_text("not a cache file")
    cache = DiskCache(str(tmp_path), expiration_minutes=120)
    cache._prune_cache()
    assert non_json.exists()
