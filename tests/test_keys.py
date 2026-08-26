import datetime
import decimal
import enum
import os
import pathlib
import subprocess
import sys
import textwrap
import unittest

from farcache import UnstableKeyError
from farcache._keys import canonical_bytes, key_digest


class Colour(enum.Enum):
    RED = 1
    BLUE = 2


class Tagged:
    def __init__(self, tags):
        self.tags = set(tags)


def module_level_function():
    return None


class KeyStabilityTest(unittest.TestCase):
    def test_unordered_containers_survive_hash_randomisation(self):
        """The bug that motivated this module: set keys used to differ per run."""
        script = textwrap.dedent(
            """
            from farcache._keys import key_digest
            print(key_digest("ns", [{"a", "b", "c", "d", "e", "f"},
                                    frozenset({1, 2, 3}),
                                    {"x": {"deep", "set"}, "y": [2, 3]}]))
            """
        )
        digests = set()
        for seed in ("0", "1", "12345", "random"):
            result = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                check=True,
                env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": _src_path()},
            )
            digests.add(result.stdout.strip())
        self.assertEqual(len(digests), 1, digests)

    def test_equal_values_share_a_digest(self):
        self.assertEqual(key_digest({"a": 1, "b": 2}), key_digest({"b": 2, "a": 1}))
        self.assertEqual(key_digest({"a", "b"}), key_digest({"b", "a"}))
        self.assertEqual(key_digest([1, [2, 3]]), key_digest([1, [2, 3]]))

    def test_distinct_values_get_distinct_digests(self):
        distinct = [
            1,
            "1",
            b"1",
            True,
            1.0,
            None,
            [1, 2],
            (1, 2),
            {1, 2},
            frozenset({1, 2}),
            {"1": 2},
            Colour.RED,
            Colour.BLUE,
            datetime.date(2020, 1, 1),
            decimal.Decimal("1.5"),
            pathlib.Path("/tmp"),
        ]
        digests = [key_digest(value) for value in distinct]
        self.assertEqual(len(set(digests)), len(distinct))

    def test_concatenation_is_unambiguous(self):
        self.assertNotEqual(key_digest(("ab", "c")), key_digest(("a", "bc")))
        self.assertNotEqual(key_digest("a", "b"), key_digest("ab"))

    def test_nested_unordered_values_are_normalised(self):
        self.assertEqual(key_digest(Tagged("ab")), key_digest(Tagged("ba")))
        self.assertNotEqual(key_digest(Tagged("ab")), key_digest(Tagged("ac")))

    def test_cycles_terminate(self):
        cyclic = [1]
        cyclic.append(cyclic)
        self.assertEqual(key_digest(cyclic), key_digest(cyclic))

        nested = {"self": None}
        nested["self"] = nested
        self.assertIsInstance(canonical_bytes(nested), bytes)

    def test_deep_nesting_is_rejected(self):
        deep = value = []
        for _ in range(200):
            nested = []
            value.append(nested)
            value = nested
        with self.assertRaises(UnstableKeyError):
            key_digest(deep)

    def test_callables_are_named_not_reduced(self):
        first = lambda: 1  # noqa: E731
        second = lambda: 2  # noqa: E731
        self.assertNotEqual(key_digest(first), key_digest(second))
        self.assertEqual(key_digest(first), key_digest(first))
        self.assertNotEqual(key_digest(module_level_function), key_digest(len))
        self.assertNotEqual(key_digest(dict), key_digest(list))

    def test_unhashable_state_is_reported(self):
        with open("/dev/null") as handle, self.assertRaises(UnstableKeyError):
            key_digest(handle)


def _src_path():
    return str(pathlib.Path(__file__).resolve().parent.parent / "src")


if __name__ == "__main__":
    unittest.main()
