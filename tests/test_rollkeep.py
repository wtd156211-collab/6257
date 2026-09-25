"""rollkeep 验收测试：只读 samples/，不写仓库。"""
from __future__ import annotations

import io
import json
import os
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from rollkeep import engine as eng
from rollkeep.cli import main
from rollkeep.render import render_html

REPO_ROOT = Path(__file__).resolve().parent.parent
QUERIES_DIR = REPO_ROOT / "samples" / "queries"
EXPECTED_DIR = REPO_ROOT / "samples" / "expected"


def run_query_file(query_path: Path) -> bytes:
    query = json.loads(query_path.read_text(encoding="utf-8"))
    engine = eng.load_points(REPO_ROOT / query["points"])
    rows = engine.query(query["from"], query["to"])
    return "".join(eng.format_row(query["id"], r) for r in rows).encode("utf-8")


class ExpectedOutputsTest(unittest.TestCase):
    """11 组样例查询与 samples/expected/ 逐字节一致。"""

    def test_all_expected(self):
        query_files = sorted(QUERIES_DIR.glob("*.jsonl"))
        self.assertEqual(11, len(query_files))
        for query_path in query_files:
            expected_path = EXPECTED_DIR / (query_path.stem + ".tsv")
            with self.subTest(query=query_path.name):
                self.assertEqual(expected_path.read_bytes(), run_query_file(query_path))

    def test_determinism(self):
        for query_path in sorted(QUERIES_DIR.glob("*.jsonl")):
            with self.subTest(query=query_path.name):
                self.assertEqual(run_query_file(query_path), run_query_file(query_path))


class CliTest(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        os.chdir(REPO_ROOT)
        self.addCleanup(os.chdir, self._cwd)
        self._stdout = None

    def run_cli(self, argv):
        class Stdout(io.TextIOBase):
            def __init__(self):
                self.buffer = io.BytesIO()

            def write(self, text):
                self.buffer.write(text.encode("utf-8"))

        out = Stdout()
        real = os.sys.stdout
        os.sys.stdout = out
        try:
            code = main(argv)
        finally:
            os.sys.stdout = real
        return code, out.buffer.getvalue()

    def test_query_dir_matches_expected(self):
        code, data = self.run_cli(["query", "--queries", "samples/queries"])
        self.assertEqual(0, code)
        expected = b"".join(
            (EXPECTED_DIR / (p.stem + ".tsv")).read_bytes()
            for p in sorted(QUERIES_DIR.glob("*.jsonl"), key=lambda p: p.name)
        )
        self.assertEqual(expected, data)

    def test_query_out_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "out.tsv"
            code, data = self.run_cli(
                ["query", "--queries", "samples/queries", "--out", str(out_path)]
            )
            self.assertEqual(0, code)
            self.assertEqual(data, out_path.read_bytes())

    def test_query_single_file(self):
        code, data = self.run_cli(
            ["query", "--queries", "samples/queries/raw-tail.jsonl"]
        )
        self.assertEqual(0, code)
        self.assertEqual((EXPECTED_DIR / "raw-tail.tsv").read_bytes(), data)

    def test_stats_bounds_and_format(self):
        code, data = self.run_cli(["stats", "--points", "samples/points/span.jsonl"])
        self.assertEqual(0, code)
        lines = data.decode("utf-8").splitlines()
        self.assertEqual(4, len(lines))
        self.assertEqual(
            ["raw", "minute", "hour", "day"], [ln.split("\t")[0] for ln in lines]
        )
        for ln in lines:
            name, entries, first, last = ln.split("\t")
            bound = eng.RETENTION[name] // eng.BUCKET_WIDTH[name] + 1
            self.assertLessEqual(int(entries), bound)
            self.assertLessEqual(int(first), int(last))
        # span.jsonl 最早三个点应已被裁掉
        self.assertEqual("1753660800", lines[3].split("\t")[2])

    def test_render_html_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            html_path = Path(tmp) / "layers.html"
            code, _ = self.run_cli(
                [
                    "render",
                    "--query",
                    "samples/queries/four-layers.jsonl",
                    "--html",
                    str(html_path),
                ]
            )
            self.assertEqual(0, code)
            html = html_path.read_text(encoding="utf-8")
        self.assertNotIn("<script", html)
        self.assertIn('id="bands"', html)
        self.assertIn('id="detail"', html)
        rects = re.findall(r"<rect data-layer=[^>]*>", html)
        self.assertEqual(4, len(rects))
        for rect in rects:
            for attr in ("data-layer", "data-start", "data-end",
                         "data-retention-start", "data-buckets"):
                self.assertIn(attr, rect)
        trs = re.findall(r"<tr data-layer=[^>]*>", html)
        self.assertEqual(4, len(trs))
        for tr in trs:
            for attr in ("data-layer", "data-buckets", "data-points"):
                self.assertIn(attr, tr)
        # bands 的 data-buckets 必须与引擎统计一致
        engine = eng.load_points(REPO_ROOT / "samples/points/span.jsonl")
        stats = {s.layer: s.entries for s in engine.layer_stats()}
        for rect in rects:
            layer = re.search(r'data-layer="(\w+)"', rect).group(1)
            buckets = int(re.search(r'data-buckets="(\d+)"', rect).group(1))
            self.assertEqual(stats[layer], buckets)
        # detail 的桶数与点数必须与查询结果一致
        rows = engine.query(1753660800, 1788220800)
        for tr in trs:
            layer = re.search(r'data-layer="(\w+)"', tr).group(1)
            layer_rows = [r for r in rows if r.layer == layer]
            self.assertEqual(
                len(layer_rows),
                int(re.search(r'data-buckets="(\d+)"', tr).group(1)),
            )
            self.assertEqual(
                sum(r.count for r in layer_rows),
                int(re.search(r'data-points="(\d+)"', tr).group(1)),
            )

    def test_render_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            p1, p2 = Path(tmp) / "a.html", Path(tmp) / "b.html"
            argv = ["render", "--query", "samples/queries/four-layers.jsonl"]
            self.assertEqual(0, self.run_cli(argv + ["--html", str(p1)])[0])
            self.assertEqual(0, self.run_cli(argv + ["--html", str(p2)])[0])
            self.assertEqual(p1.read_bytes(), p2.read_bytes())

    def test_exit_code_bad_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            # from > to
            bad_query = tmp / "bad.jsonl"
            bad_query.write_text(
                '{"id": "x", "points": "samples/points/near.jsonl",'
                ' "from": 10, "to": 5}',
                encoding="utf-8",
            )
            code, _ = self.run_cli(["query", "--queries", str(bad_query)])
            self.assertEqual(1, code)
            # 查询路径不存在
            code, _ = self.run_cli(["query", "--queries", str(tmp / "nope")])
            self.assertEqual(1, code)
            # 原始点文件缺失
            code, _ = self.run_cli(["stats", "--points", str(tmp / "nope.jsonl")])
            self.assertEqual(1, code)
            # JSON 解析失败
            bad_points = tmp / "bad-points.jsonl"
            bad_points.write_text('{"t": 1, "v": 2}\nnot json\n', encoding="utf-8")
            code, _ = self.run_cli(["stats", "--points", str(bad_points)])
            self.assertEqual(1, code)
            # t 乱序 / 重复
            dup_points = tmp / "dup.jsonl"
            dup_points.write_text(
                '{"t": 5, "v": 1}\n{"t": 5, "v": 2}\n', encoding="utf-8"
            )
            code, _ = self.run_cli(["stats", "--points", str(dup_points)])
            self.assertEqual(1, code)
            # 非 0 时不写输出文件
            out_path = tmp / "should-not-exist.tsv"
            self.run_cli(
                ["query", "--queries", str(bad_query), "--out", str(out_path)]
            )
            self.assertFalse(out_path.exists())

    def test_exit_code_usage(self):
        with self.assertRaises(SystemExit) as ctx:
            main(["query"])
        self.assertEqual(2, ctx.exception.code)
        with self.assertRaises(SystemExit) as ctx:
            main(["unknown-command"])
        self.assertEqual(2, ctx.exception.code)


class EngineTest(unittest.TestCase):
    def test_seam_assignment(self):
        """t = W-259200 归原始层，t = W-259201 归分钟层最后一桶。"""
        engine = eng.load_points(REPO_ROOT / "samples/points/near.jsonl")
        watermark = engine.watermark
        rows = engine.query(watermark - 259260, watermark - 259200)
        self.assertEqual("minute", rows[0].layer)
        self.assertEqual(watermark - 259260, rows[0].start)
        self.assertEqual(watermark - 259201, rows[0].end)
        self.assertEqual("raw", rows[1].layer)
        self.assertEqual(watermark - 259200, rows[1].start)

    def test_half_clipped_bucket_dropped(self):
        """被端点切掉一半的桶整桶丢弃。"""
        engine = eng.load_points(REPO_ROOT / "samples/points/near.jsonl")
        rows = engine.query(1787961461, 1787961599)
        self.assertEqual(
            [(1787961480, 1787961539), (1787961540, 1787961599)],
            [(r.start, r.end) for r in rows],
        )

    def test_avg_rounding(self):
        self.assertEqual("210.500", eng.format_avg(421, 2))
        self.assertEqual("0.333", eng.format_avg(1, 3))
        self.assertEqual("0.667", eng.format_avg(2, 3))
        self.assertEqual("2.500", eng.format_avg(5, 2))

    def test_storage_bounds(self):
        engine = eng.load_points(REPO_ROOT / "samples/points/span.jsonl")
        for stat in engine.layer_stats():
            bound = eng.RETENTION[stat.layer] // eng.BUCKET_WIDTH[stat.layer] + 1
            self.assertLessEqual(stat.entries, bound)


if __name__ == "__main__":
    unittest.main()
