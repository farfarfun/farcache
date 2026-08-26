import time
import unittest

from farcache import (
    cache,
    fifo_cache,
    lfu_cache,
    lru_cache,
    rr_cache,
    ttl_cache,
    vttl_cache,
)

POLICIES = (lru_cache, lfu_cache, fifo_cache, rr_cache, ttl_cache, vttl_cache)


class MemoryCacheTest(unittest.TestCase):
    def test_bare_and_called_forms_both_work(self):
        for policy in POLICIES:
            with self.subTest(policy=policy.__name__):
                calls = [0]

                @policy
                def bare(value):
                    calls[0] += 1
                    return value * 2

                @policy(maxsize=8)
                def called(value):
                    calls[0] += 1
                    return value * 3

                self.assertEqual(bare(2), 4)
                self.assertEqual(bare(2), 4)
                self.assertEqual(called(2), 6)
                self.assertEqual(called(2), 6)
                self.assertEqual(calls, [2])

    def test_cache_decorator_needs_no_arguments(self):
        calls = [0]

        @cache
        def add(a, b):
            calls[0] += 1
            return a + b

        self.assertEqual(add(1, 2), 3)
        self.assertEqual(add(1, 2), 3)
        self.assertEqual(calls, [1])

    def test_underlying_policy_is_reachable(self):
        @lru_cache(maxsize=4)
        def square(value):
            return value * value

        for value in range(3):
            square(value)
        self.assertEqual(len(square.cache), 3)

        square.cache.clear()
        self.assertEqual(len(square.cache), 0)

    def test_maxsize_is_honoured(self):
        @fifo_cache(maxsize=2)
        def identity(value):
            return value

        for value in range(5):
            identity(value)
        self.assertLessEqual(len(identity.cache), 2)

    def test_ttl_entries_expire(self):
        for policy in (ttl_cache, vttl_cache):
            with self.subTest(policy=policy.__name__):
                calls = [0]

                @policy(maxsize=8, ttl=0.2)
                def load(key):
                    calls[0] += 1
                    return key

                load("a")
                load("a")
                self.assertEqual(calls, [1])
                time.sleep(0.25)
                load("a")
                self.assertEqual(calls, [2])

    def test_vttl_honours_its_ttl_argument(self):
        """Regression: the ttl used to reach the constructor, where it was ignored."""

        @vttl_cache(maxsize=8, ttl=30)
        def load(key):
            return key

        load("a")
        _, expires_at = load.cache.get_with_expire("a")
        self.assertGreater(expires_at, 0)

    def test_metadata_is_preserved(self):
        @lru_cache(maxsize=2)
        def documented(value):
            """Doc string."""
            return value

        self.assertEqual(documented.__name__, "documented")
        self.assertEqual(documented.__doc__, "Doc string.")


class AsyncMemoryCacheTest(unittest.IsolatedAsyncioTestCase):
    async def test_coroutines_are_cached(self):
        for policy in POLICIES:
            with self.subTest(policy=policy.__name__):
                calls = [0]

                @policy(maxsize=8)
                async def fetch(key):
                    calls[0] += 1
                    return key.upper()

                self.assertEqual(await fetch("a"), "A")
                self.assertEqual(await fetch("a"), "A")
                self.assertEqual(await fetch("b"), "B")
                self.assertEqual(calls, [2])


if __name__ == "__main__":
    unittest.main()
