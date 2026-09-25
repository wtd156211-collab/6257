"""CLI 验收：对照 samples/expected 逐字节比对、stats 上界、退出码。"""

import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUERIES = os.path.join(ROOT, "samples", "queries")
EXPECTED = os.path.join(ROOT, "samples", "expected")


def run_cli(*args, cwd=ROOT):
    return subprocess.run(
        [sys.executable, "-m", "rollkeep", *args],
        cwd=cwd, capture_output=True, text=True,
    )


class TestQueryAgainstExpected(unittest.TestCase):
    def test_each_query_matches_expected_bytes(self):
        for name in sorted(os.listdir(QUERIES)):
            query_path = os.path.join(QUERIES, name)
            stem = os.path.splitext(name)[0]
            with self.subTest(query=stem):
                result = run_cli("query", "--queries", query_path)
                self.assertEqual(result.returncode, 0, result.stderr)
                with open(os.path.join(EXPECTED, stem + ".tsv"), "rb") as fh:
                    self.assertEqual(result.stdout.encode(), fh.read())

    def test_directory_order_concatenates_expected(self):
        result = run_cli("query", "--queries", QUERIES)
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = b""
        for name in sorted(os.listdir(QUERIES)):
            stem = os.path.splitext(name)[0]
            with open(os.path.join(EXPECTED, stem + ".tsv"), "rb") as fh:
                expected += fh.read()
        self.assertEqual(result.stdout.encode(), expected)

    def test_out_file_byte_identical_to_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "queries.tsv")
            result = run_cli("query", "--queries", QUERIES, "--out", out)
            self.assertEqual(result.returncode, 0, result.stderr)
            with open(out, "rb") as fh:
                self.assertEqual(fh.read(), result.stdout.encode())

    def test_deterministic_across_runs(self):
        first = run_cli("query", "--queries", QUERIES)
        second = run_cli("query", "--queries", QUERIES)
        self.assertEqual(first.stdout, second.stdout)


class TestStats(unittest.TestCase):
    def test_stats_span(self):
        result = run_cli(
            "stats", "--points", os.path.join("samples", "points", "span.jsonl")
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual([line.split("\t")[0] for line in lines],
                         ["raw", "minute", "hour", "day"])
        bounds = {"raw": 259201, "minute": 20161, "hour": 2881, "day": 401}
        for line in lines:
            name, count, lo, hi = line.split("\t")
            self.assertLessEqual(int(count), bounds[name])
            self.assertNotEqual(lo, "-")
            self.assertNotEqual(hi, "-")

    def test_stats_known_values(self):
        # span.jsonl 水位 1788220800，最早三个点已被裁掉
        result = run_cli(
            "stats", "--points", os.path.join("samples", "points", "span.jsonl")
        )
        lines = dict(
            (line.split("\t")[0], line.split("\t")[1:])
            for line in result.stdout.splitlines()
        )
        self.assertEqual(lines["raw"], ["8", "1787961600", "1788220800"])
        self.assertEqual(lines["day"], ["75", "1753660800", "1788307199"])


class TestExitCodes(unittest.TestCase):
    def test_missing_file_exit_1(self):
        result = run_cli("stats", "--points", "samples/points/nope.jsonl")
        self.assertEqual(result.returncode, 1)

    def test_from_greater_than_to_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "bad.jsonl")
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write('{"id": "x", "points": "samples/points/near.jsonl",'
                         ' "from": 200, "to": 100}\n')
            result = run_cli("query", "--queries", bad)
            self.assertEqual(result.returncode, 1)

    def test_out_of_order_points_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "bad-points.jsonl")
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write('{"t": 100, "v": 1}\n{"t": 100, "v": 2}\n')
            result = run_cli("stats", "--points", bad)
            self.assertEqual(result.returncode, 1)

    def test_broken_json_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "broken.jsonl")
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write('{"t": 100, "v": \n')
            result = run_cli("stats", "--points", bad)
            self.assertEqual(result.returncode, 1)

    def test_usage_error_exit_2(self):
        result = run_cli("query")  # 缺 --queries
        self.assertEqual(result.returncode, 2)

    def test_no_output_file_on_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "should-not-exist.tsv")
            bad = os.path.join(tmp, "bad.jsonl")
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write('{"id": "x", "points": "nope.jsonl",'
                         ' "from": 1, "to": 2}\n')
            result = run_cli("query", "--queries", bad, "--out", out)
            self.assertEqual(result.returncode, 1)
            self.assertFalse(os.path.exists(out))


if __name__ == "__main__":
    unittest.main()
