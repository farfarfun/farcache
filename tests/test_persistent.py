import contextlib
import inspect
import io
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from nltcache import DiskCache, PickleCache, disk_cache, pkl_cache


class PersistentCacheTestCase(unittest.TestCase):
    """Base for tests that run against both persistent backends."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = directory.name

    def decorators(self, **kwargs):
        for factory, name in ((pkl_cache, "pickle"), (disk_cache, "disk")):
            decorator = factory(cache_dir=os.path.join(self.directory, name), **kwargs)
            self.addCleanup(decorator.close)
            yield decorator

    def path(self, *parts):
        return os.path.join(self.directory, *parts)


class CallSemanticsTest(PersistentCacheTestCase):
    def test_preserves_call_semantics(self):
        for decorator in self.decorators(cache_key="key"):
            with self.subTest(decorator=type(decorator).__name__):

                @decorator
                def variadic(key, /, *values):
                    return key, values

                self.assertEqual(variadic("key", 1, 2), ("key", (1, 2)))
                with self.assertRaises(TypeError):
                    variadic()

                @decorator
                def regular(key):
                    return key

                with self.assertRaises(TypeError):
                    regular("first", key="second")

    def test_validates_key_parameter(self):
        for decorator in self.decorators(cache_key="key"):
            with self.subTest(decorator=type(decorator).__name__):
                with self.assertRaises(ValueError) as caught:

                    @decorator
                    def function(actual):
                        return actual

                self.assertIn("'key'", str(caught.exception))

    def test_rejects_generator_functions(self):
        for decorator in self.decorators(cache_key="key"):
            with self.subTest(decorator=type(decorator).__name__):
                with self.assertRaises(TypeError):

                    @decorator
                    def sync_gen(key):
                        yield key

                with self.assertRaises(TypeError):

                    @decorator
                    async def async_gen(key):
                        yield key

    def test_rejects_empty_key_sequence(self):
        with self.assertRaises(ValueError):
            PickleCache(cache_key=[])

    def test_caches_none_and_isolates_by_function_and_type(self):
        for decorator in self.decorators(cache_key="key"):
            with self.subTest(decorator=type(decorator).__name__):
                calls = [0, 0]

                @decorator
                def first(key):
                    calls[0] += 1
                    return None if key == "none" else type(key).__name__

                @decorator
                def second(key):
                    calls[1] += 1
                    return "second"

                self.assertIsNone(first("none"))
                self.assertIsNone(first("none"))
                self.assertEqual(first(1), "int")
                self.assertEqual(first("1"), "str")
                self.assertEqual(second(1), "second")
                self.assertEqual(calls, [3, 1])

    def test_survives_a_new_decorator_instance(self):
        """Entries must be readable by a fresh process, i.e. a fresh instance."""
        for factory in (pkl_cache, disk_cache):
            with self.subTest(factory=factory.__name__):
                directory = self.path(factory.__name__)
                calls = [0]

                def build():
                    decorator = factory(cache_key="key", cache_dir=directory)
                    self.addCleanup(decorator.close)

                    @decorator
                    def parse(key):
                        calls[0] += 1
                        return sorted(key)

                    return parse

                self.assertEqual(build()({"b", "a"}), ["a", "b"])
                self.assertEqual(build()({"a", "b"}), ["a", "b"])
                self.assertEqual(calls, [1])


class KeySelectionTest(PersistentCacheTestCase):
    def test_multiple_key_parameters(self):
        for decorator in self.decorators(cache_key=["query", "top_k"]):
            with self.subTest(decorator=type(decorator).__name__):
                calls = [0]

                @decorator
                def search(query, top_k=10):
                    calls[0] += 1
                    return query, top_k

                self.assertEqual(search("a", 1), ("a", 1))
                self.assertEqual(search("a", 2), ("a", 2))
                self.assertEqual(search("a", 1), ("a", 1))
                self.assertEqual(calls, [2])

    def test_all_arguments_are_keyed_by_default(self):
        for decorator in self.decorators():
            with self.subTest(decorator=type(decorator).__name__):
                calls = [0]

                @decorator
                def add(a, b=1):
                    calls[0] += 1
                    return a + b

                self.assertEqual(add(1), 2)
                self.assertEqual(add(1, 1), 2)  # same bound arguments
                self.assertEqual(add(1, 2), 3)
                self.assertEqual(calls, [2])

    def test_toggling_is_cache_does_not_move_the_entry(self):
        for decorator in self.decorators():
            with self.subTest(decorator=type(decorator).__name__):
                calls = [0]

                @decorator
                def load(name, cache=True):
                    calls[0] += 1
                    return name

                self.assertEqual(load("x"), "x")
                self.assertEqual(load("x", cache=False), "x")  # recomputed
                self.assertEqual(load("x"), "x")  # still the original entry
                self.assertEqual(calls, [2])

    def test_none_key_bypasses_the_cache(self):
        for decorator in self.decorators(cache_key="key"):
            with self.subTest(decorator=type(decorator).__name__):
                calls = [0]

                @decorator
                def load(key):
                    calls[0] += 1
                    return key

                self.assertIsNone(load(None))
                self.assertIsNone(load(None))
                self.assertEqual(calls, [2])

    def test_explicit_key_reports_unstable_values(self):
        for decorator in self.decorators(cache_key="key"):
            with self.subTest(decorator=type(decorator).__name__):

                @decorator
                def load(key):
                    return key

                with open(os.devnull) as handle, self.assertRaises(TypeError):
                    load(handle)

    def test_all_args_mode_degrades_instead_of_raising(self):
        for decorator in self.decorators():
            with self.subTest(decorator=type(decorator).__name__):
                calls = [0]

                @decorator
                def load(handle):
                    calls[0] += 1
                    return "ok"

                with open(os.devnull) as handle:
                    with self.assertLogs("nltcache", level="WARNING"):
                        self.assertEqual(load(handle), "ok")
                    self.assertEqual(load(handle), "ok")
                self.assertEqual(calls, [2])


class CacheControlTest(PersistentCacheTestCase):
    def test_invalidate_clear_and_key(self):
        for decorator in self.decorators(cache_key="key"):
            with self.subTest(decorator=type(decorator).__name__):
                calls = [0]

                @decorator
                def load(key):
                    calls[0] += 1
                    return key

                self.assertIsNone(load.cache_key(None))
                self.assertEqual(load.cache_key("a"), load.cache_key("a"))

                load("a")
                load("a")
                self.assertEqual(calls, [1])

                self.assertTrue(load.cache_invalidate("a"))
                self.assertFalse(load.cache_invalidate("a"))
                load("a")
                self.assertEqual(calls, [2])

                load("b")
                self.assertEqual(calls, [3])
                self.assertEqual(load.cache_clear(), 2)
                load("a")
                self.assertEqual(calls, [4])

    def test_expire(self):
        for decorator in self.decorators(cache_key="key", expire=0.2):
            with self.subTest(decorator=type(decorator).__name__):
                calls = [0]

                @decorator
                def load(key):
                    calls[0] += 1
                    return key

                load("a")
                load("a")
                self.assertEqual(calls, [1])
                time.sleep(0.25)
                load("a")
                self.assertEqual(calls, [2])

    def test_prune_drops_expired_entries(self):
        for decorator in self.decorators(cache_key="key", expire=0.2):
            with self.subTest(decorator=type(decorator).__name__):

                @decorator
                def load(key):
                    return key

                load("a")
                load("b")
                time.sleep(0.25)
                self.assertEqual(load.cache_prune(), 2)
                self.assertEqual(load.cache_clear(), 0)

    def test_cache_close_is_reentrant(self):
        for decorator in self.decorators(cache_key="key"):
            with self.subTest(decorator=type(decorator).__name__):

                @decorator
                def load(key):
                    return key

                load("a")
                load.cache_close()
                load.cache_close()
                self.assertEqual(load("a"), "a")  # store reopens on demand

    def test_concurrent_first_calls_share_one_store(self):
        for decorator in self.decorators(cache_key="key"):
            with self.subTest(decorator=type(decorator).__name__):

                @decorator
                def load(key):
                    return key * 2

                results = []
                barrier = threading.Barrier(8)

                def worker(index):
                    barrier.wait()
                    results.append(load(index % 2))

                threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()

                self.assertEqual(len(results), 8)
                self.assertEqual(set(results), {0, 2})


class AsyncCacheTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = directory.name

    async def test_coroutine_results_are_cached(self):
        for factory, name in ((pkl_cache, "pickle"), (disk_cache, "disk")):
            with self.subTest(factory=name):
                decorator = factory(
                    cache_key="key", cache_dir=os.path.join(self.directory, name)
                )
                self.addCleanup(decorator.close)
                calls = [0]

                @decorator
                async def fetch(key):
                    calls[0] += 1
                    return key.upper()

                self.assertTrue(inspect.iscoroutinefunction(fetch))
                self.assertEqual(await fetch("a"), "A")
                self.assertEqual(await fetch("a"), "A")
                self.assertEqual(await fetch("b"), "B")
                self.assertEqual(calls, [2])

    async def test_coroutine_bypass_is_awaited(self):
        decorator = pkl_cache(cache_key="key", cache_dir=self.directory)
        self.addCleanup(decorator.close)

        @decorator
        async def fetch(key):
            return key

        self.assertIsNone(await fetch(None))


class PickleBackendTest(PersistentCacheTestCase):
    def test_recovers_from_a_corrupt_file(self):
        decorator = PickleCache("key", cache_dir=self.directory)
        self.addCleanup(decorator.close)
        calls = [0]

        @decorator
        def load(key):
            calls[0] += 1
            return key

        load("value")
        digest = load.cache_key("value")
        corrupt = Path(self.directory) / digest[:2] / f"{digest}.pkl"
        corrupt.write_bytes(b"not a pickle")

        self.assertEqual(load("value"), "value")
        self.assertEqual(calls, [2])
        self.assertEqual(load("value"), "value")
        self.assertEqual(calls, [2])

    def test_writes_leave_no_temporary_files(self):
        decorator = PickleCache("key", cache_dir=self.directory)
        self.addCleanup(decorator.close)

        @decorator
        def load(key):
            return key

        for index in range(20):
            load(index)

        names = [p.name for p in Path(self.directory).rglob("*") if p.is_file()]
        self.assertEqual(len(names), 20)
        self.assertTrue(all(name.endswith(".pkl") for name in names), names)

    def test_clear_only_touches_its_own_files(self):
        decorator = PickleCache("key", cache_dir=self.directory)
        self.addCleanup(decorator.close)

        @decorator
        def load(key):
            return key

        load("a")
        bystander = Path(self.directory) / "important.txt"
        bystander.write_text("keep me")

        self.assertEqual(load.cache_clear(), 1)
        self.assertTrue(bystander.exists())

    def test_max_entries_trims_the_oldest(self):
        store_dir = Path(self.path("bounded"))
        decorator = PickleCache("key", cache_dir=str(store_dir), max_entries=5)
        self.addCleanup(decorator.close)

        @decorator
        def load(key):
            return key

        for index in range(20):
            load(index)
            digest = load.cache_key(index)
            # Age the entries deterministically so "oldest first" is well defined.
            os.utime(store_dir / digest[:2] / f"{digest}.pkl", (index, index))

        self.assertEqual(load.cache_prune(), 15)
        survivors = {path.stem for path in store_dir.rglob("*.pkl")}
        self.assertEqual(survivors, {load.cache_key(i) for i in range(15, 20)})

    def test_events_are_logged_and_optionally_printed(self):
        decorator = PickleCache("key", cache_dir=self.directory, printf=True)
        self.addCleanup(decorator.close)

        @decorator
        def load(key):
            return key

        stdout = io.StringIO()
        with (
            self.assertLogs("nltcache", level="DEBUG") as logged,
            contextlib.redirect_stdout(stdout),
        ):
            load("a")
            load("a")

        self.assertTrue(any("Cache hit" in line for line in logged.output))
        self.assertIn("Cache hit", stdout.getvalue())


class DiskBackendTest(PersistentCacheTestCase):
    def test_store_opens_on_the_first_cached_call(self):
        cache_dir = self.path("disk")
        decorator = DiskCache("key", cache_dir=cache_dir)
        self.addCleanup(decorator.close)

        @decorator
        def function(key, cache=True):
            return key

        self.assertFalse(os.path.exists(cache_dir))
        self.assertEqual(function("bypass", cache=False), "bypass")
        self.assertFalse(os.path.exists(cache_dir))
        self.assertEqual(function("cached"), "cached")
        self.assertTrue(os.path.exists(cache_dir))

    def test_derived_directories_are_per_function(self):
        """One decorator instance may be reused; it must not latch onto the first."""
        decorator = DiskCache("key", cache_dir=None)
        self.addCleanup(decorator.close)

        def alpha(key):
            return "alpha"

        def beta(key):
            return "beta"

        decorator(alpha)
        decorator(beta)

        self.assertIsNone(decorator.cache_dir)  # never rewritten in place
        self.assertNotEqual(decorator._prepare(alpha), decorator._prepare(beta))

    def test_settings_are_forwarded(self):
        decorator = DiskCache(
            "key", cache_dir=self.path("sized"), size_limit=2**20, expire=None
        )
        self.addCleanup(decorator.close)

        @decorator
        def load(key):
            return key

        load("a")
        self.assertEqual(decorator.settings["size_limit"], 2**20)

    def test_decorator_is_a_context_manager(self):
        with DiskCache("key", cache_dir=self.path("ctx")) as decorator:

            @decorator
            def load(key):
                return key

            self.assertEqual(load("a"), "a")


if __name__ == "__main__":
    unittest.main()
