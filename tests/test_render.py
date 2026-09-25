"""render 验收：layers.html 的元素、属性与引擎统计一致，两次生成逐字节相同。"""

import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_render(html_path):
    return subprocess.run(
        [sys.executable, "-m", "rollkeep", "render",
         "--query", os.path.join("samples", "queries", "four-layers.jsonl"),
         "--html", html_path],
        cwd=ROOT, capture_output=True, text=True,
    )


def run_stats():
    return subprocess.run(
        [sys.executable, "-m", "rollkeep", "stats",
         "--points", os.path.join("samples", "points", "span.jsonl")],
        cwd=ROOT, capture_output=True, text=True,
    )


class TestRender(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.html_path = os.path.join(cls.tmp.name, "layers.html")
        result = run_render(cls.html_path)
        assert result.returncode == 0, result.stderr
        with open(cls.html_path, "r", encoding="utf-8") as fh:
            cls.page = fh.read()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_bands_cover_all_layers(self):
        rects = re.findall(r"<rect data-layer=[^>]*>", self.page)
        self.assertEqual(len(rects), 4)
        layers = [re.search(r'data-layer="(\w+)"', r).group(1) for r in rects]
        self.assertEqual(layers, ["raw", "minute", "hour", "day"])
        for rect in rects:
            for attr in ("data-start", "data-end", "data-retention-start",
                         "data-buckets"):
                self.assertIn(attr + "=", rect)

    def test_bands_match_engine_stats(self):
        stats = {}
        for line in run_stats().stdout.splitlines():
            name, count, lo, hi = line.split("\t")
            stats[name] = (count, lo, hi)
        for rect in re.findall(r"<rect data-layer=[^>]*>", self.page):
            attrs = dict(re.findall(r'(data-[\w-]+)="([^"]*)"', rect))
            count, lo, hi = stats[attrs["data-layer"]]
            self.assertEqual(attrs["data-buckets"], count)
            self.assertEqual(attrs["data-start"], lo)
            self.assertEqual(attrs["data-end"], hi)

    def test_detail_rows_match_query_result(self):
        rows = re.findall(r"<tr data-layer=[^>]*>", self.page)
        self.assertEqual(len(rows), 4)
        total_points = 0
        for row in rows:
            attrs = dict(re.findall(r'(data-[\w-]+)="(\d+)"', row))
            total_points += int(attrs["data-points"])
        # four-layers 全部行的 count 之和 == 水位内未被裁剪的点数
        self.assertEqual(total_points, 229)

    def test_no_script_no_external_resources(self):
        self.assertNotIn("<script", self.page)
        self.assertNotIn("http://", self.page.replace("http://www.w3.org", ""))
        self.assertNotIn("https://", self.page)
        self.assertNotIn("<link", self.page)

    def test_deterministic(self):
        other = os.path.join(self.tmp.name, "again.html")
        result = run_render(other)
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(other, "rb") as fh:
            self.assertEqual(fh.read(), self.page.encode())


if __name__ == "__main__":
    unittest.main()
