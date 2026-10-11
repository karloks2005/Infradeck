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

    def ttl(self, key):
        return self.ttls.get(key, -1)

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
