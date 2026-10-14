"""A small in-memory stand-in for the Redis commands the backend uses, shared by the tests."""
import fnmatch


class FakeRedis:
    def __init__(self):
        self.store: dict = {}
        self.hashes: dict[str, dict] = {}
        self.ttls: dict[str, int] = {}

    # strings and counters
    def set(self, key, value, ex=None, nx=False, **kwargs):
        if nx and key in self.store:
            return None
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    def get(self, key):
        return self.store.get(key)

    def delete(self, *keys):
        removed = 0
        for key in keys:
            removed += int(self.store.pop(key, None) is not None) + int(self.hashes.pop(key, None) is not None)
            self.ttls.pop(key, None)
        return removed

    def decr(self, key):
        self.store[key] = int(self.store.get(key, 0)) - 1
        return self.store[key]

    def incr(self, key):
        self.store[key] = int(self.store.get(key, 0)) + 1
        return self.store[key]

    def expire(self, key, seconds):
        self.ttls[key] = seconds
        return True

    def exists(self, *keys):
        return sum(int(key in self.store or key in self.hashes) for key in keys)

    def rename(self, key, target):
        if key in self.store:
            self.store[target] = self.store.pop(key)
        elif key in self.hashes:
            self.hashes[target] = self.hashes.pop(key)
        else:
            raise KeyError(key)
        if key in self.ttls:
            self.ttls[target] = self.ttls.pop(key)
        return True

    def ttl(self, key):
        return self.ttls.get(key, -1)

    def scan_iter(self, match="*", **kwargs):
        return iter(self.keys(match))

    def hvals(self, key):
        return list(self.hashes.get(key, {}).values())

    def keys(self, pattern="*"):
        return [key for key in list(self.store) + list(self.hashes) if fnmatch.fnmatch(key, pattern)]

    # hashes
    def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value
        return 1

    def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def hdel(self, key, *fields):
        entry = self.hashes.get(key, {})
        return sum(int(entry.pop(field, None) is not None) for field in fields)

    # lists and sorted sets, for the agent's job queue (left is the head, as in Redis)
    def _list(self, key):
        return self.store.setdefault(key, [])

    def lpush(self, key, *values):
        for value in values:
            self._list(key).insert(0, value)
        return len(self.store[key])

    def rpush(self, key, *values):
        self._list(key).extend(values)
        return len(self.store[key])

    def llen(self, key):
        return len(self.store.get(key) or [])

    def lrange(self, key, start, end):
        items = self.store.get(key) or []
        return items[start:] if end == -1 else items[start:end + 1]

    def ltrim(self, key, start, end):
        self.store[key] = self.lrange(key, start, end)
        return True

    def lrem(self, key, count, value):
        items = self.store.get(key) or []
        if value in items:
            items.remove(value)
            return 1
        return 0

    def lmove(self, source, destination, src="LEFT", dest="RIGHT"):
        items = self.store.get(source) or []
        if not items:
            return None
        value = items.pop(0 if src == "LEFT" else -1)
        target = self._list(destination)
        target.insert(0, value) if dest == "LEFT" else target.append(value)
        return value

    def blmove(self, source, destination, timeout, src="LEFT", dest="RIGHT"):
        return self.lmove(source, destination, src, dest)

    def zadd(self, key, mapping):
        self.hashes.setdefault(key, {}).update(mapping)
        return len(mapping)

    def zrangebyscore(self, key, low, high):
        entries = self.hashes.get(key, {})
        low = float("-inf") if low == "-inf" else float(low)
        high = float("inf") if high == "+inf" else float(high)
        return [member for member, score in sorted(entries.items(), key=lambda item: item[1]) if low <= score <= high]

    def zrem(self, key, *members):
        entries = self.hashes.get(key, {})
        return sum(int(entries.pop(member, None) is not None) for member in members)

    def zcard(self, key):
        return len(self.hashes.get(key, {}))

    # the rate limiter pipelines an increment with an expiry
    def pipeline(self):
        parent = self

        class Pipeline:
            def __init__(self):
                self.calls = []

            def incr(self, key):
                self.calls.append(("incr", key))
                return self

            def expire(self, key, seconds):
                self.calls.append(("expire", key, seconds))
                return self

            def execute(self):
                results = []
                for call in self.calls:
                    results.append(parent.incr(call[1]) if call[0] == "incr" else parent.expire(call[1], call[2]))
                return results

        return Pipeline()
