import os
import tempfile
import unittest
from pathlib import Path

from nltcache import DiskCache, PickleCache, disk_cache, pkl_cache


class PersistentCacheTest(unittest.TestCase):
    def decorators(self, directory):
        return (
            pkl_cache("key", cache_dir=os.path.join(directory, "pickle")),
            disk_cache("key", cache_dir=os.path.join(directory, "disk")),
        )

    def test_preserves_call_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            for decorator in self.decorators(directory):
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

    def test_caches_none_and_isolates_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            for decorator in self.decorators(directory):
                self.calls = [0, 0]

                @decorator
                def first(key):
                    self.calls[0] += 1
                    return None if key == "none" else type(key).__name__

                second_decorator = type(decorator)("key", cache_dir=decorator.cache_dir)

                @second_decorator
                def second(key):
                    self.calls[1] += 1
                    return "second"

                self.assertIsNone(first("none"))
                self.assertIsNone(first("none"))
                self.assertEqual(first(1), "int")
                self.assertEqual(first("1"), "str")
                self.assertEqual(second(1), "second")
                self.assertEqual(self.calls, [3, 1])

    def test_validates_key_parameter(self):
        with tempfile.TemporaryDirectory() as directory:
            for decorator in self.decorators(directory):
                with self.subTest(
                    decorator=type(decorator).__name__
                ), self.assertRaises(ValueError):

                    @decorator
                    def function(actual):
                        return actual

    def test_disk_cache_initializes_on_first_cached_call(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_dir = os.path.join(directory, "disk")
            decorator = DiskCache("key", cache_dir=cache_dir)

            @decorator
            def function(key, cache=True):
                return key

            self.assertFalse(os.path.exists(cache_dir))
            self.assertEqual(function("bypass", cache=False), "bypass")
            self.assertFalse(os.path.exists(cache_dir))
            self.assertEqual(function("cached"), "cached")
            self.assertTrue(os.path.exists(cache_dir))
            decorator._cache.close()

    def test_pickle_cache_recovers_from_corrupt_file(self):
        with tempfile.TemporaryDirectory() as directory:
            decorator = PickleCache("key", cache_dir=directory)

            def function(key):
                return key

            namespace = f"{function.__module__}.{function.__qualname__}"
            cache_file = decorator._get_cache_file(namespace, "value")
            Path(cache_file).touch()

            self.assertEqual(decorator(function)("value"), "value")


if __name__ == "__main__":
    unittest.main()
