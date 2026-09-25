"""引擎口径单测：层级、保留、聚合、拼接与去重（README 第 2 节）。"""

import unittest

from rollkeep.engine import LAYERS, STORAGE_BOUNDS, Store, average


def make_store(points):
    store = Store()
    for t, v in points:
        store.write(t, v)
    return store


class TestLayerSpec(unittest.TestCase):
    def test_layer_widths_and_retentions(self):
        spec = {layer.name: (layer.width, layer.retention) for layer in LAYERS}
        self.assertEqual(spec["raw"], (1, 259200))
        self.assertEqual(spec["minute"], (60, 1209600))
        self.assertEqual(spec["hour"], (3600, 10368000))
        self.assertEqual(spec["day"], (86400, 34560000))

    def test_storage_bounds(self):
        self.assertEqual(STORAGE_BOUNDS["minute"], 20161)
        self.assertEqual(STORAGE_BOUNDS["hour"], 2881)
        self.assertEqual(STORAGE_BOUNDS["day"], 401)
        self.assertEqual(
            sum(STORAGE_BOUNDS[name] for name in ("minute", "hour", "day")), 23443
        )


class TestAggregation(unittest.TestCase):
    def test_bucket_aggregates(self):
        # 同一个分钟桶里三条点
        store = make_store([(60, 10), (61, 20), (119, 7)])
        rows = store.query(0, 119)
        # 原始层仍覆盖这些点（水位 119，3 天窗口），粗层不回答
        self.assertTrue(all(row[0] == "raw" for row in rows))
        stats = {name: (n, lo, hi) for name, n, lo, hi in store.stats()}
        self.assertEqual(stats["minute"][0], 1)
        bucket = store._buckets["minute"][1]
        self.assertEqual(bucket, [3, 37, 7, 20])

    def test_average_half_up(self):
        self.assertEqual(average(1, 2), "0.500")
        self.assertEqual(average(421, 2), "210.500")
        self.assertEqual(average(2, 3), "0.667")
        self.assertEqual(average(5, 2), "2.500")
        self.assertEqual(average(1010, 1), "1010.000")

    def test_out_of_order_rejected(self):
        store = make_store([(10, 1)])
        with self.assertRaises(ValueError):
            store.write(10, 2)  # 重复
        with self.assertRaises(ValueError):
            store.write(9, 2)  # 乱序


class TestStitching(unittest.TestCase):
    def setUp(self):
        # 水位 400 天处一个点，回答区间边界由此推出
        self.water = 400 * 86400
        self.store = make_store([(self.water, 5)])

    def test_answer_intervals_tile_without_gap(self):
        intervals = {
            layer.name: self.store.answer_interval(layer) for layer in LAYERS
        }
        self.assertEqual(intervals["raw"], (self.water - 259200, self.water))
        self.assertEqual(
            intervals["minute"], (self.water - 1209600, self.water - 259201)
        )
        self.assertEqual(
            intervals["hour"], (self.water - 10368000, self.water - 1209601)
        )
        self.assertEqual(
            intervals["day"], (self.water - 34560000, self.water - 10368001)
        )
        # 首尾相接：粗层终点 + 1 == 下一细层起点
        order = ["day", "hour", "minute", "raw"]
        for coarse, fine in zip(order, order[1:]):
            self.assertEqual(intervals[coarse][1] + 1, intervals[fine][0])

    def test_seam_second_goes_to_raw(self):
        # t = W - 259200 归原始层
        store = make_store([(self.water - 259200, 9), (self.water, 5)])
        rows = store.query(self.water - 259200, self.water - 259200)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "raw")
        self.assertEqual(rows[0][1], self.water - 259200)

    def test_second_before_seam_goes_to_minute(self):
        # t = W - 259201 归分钟层最后一个桶
        t = self.water - 259201
        store = make_store([(t, 9), (self.water, 5)])
        rows = store.query(t, t)
        self.assertEqual(rows, [])  # 单点区间切不进完整分钟桶，整桶丢弃
        k = t // 60
        rows = store.query(k * 60, k * 60 + 59)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "minute")
        self.assertEqual(rows[0][1:3], (k * 60, k * 60 + 59))

    def test_clipped_bucket_dropped(self):
        # 查询端点切进桶里，半截桶整桶丢弃
        store = make_store([(60, 1), (61, 2), (self.water, 5)])
        rows = store.query(61, 119)
        self.assertEqual(rows, [])

    def test_beyond_retention_empty(self):
        rows = self.store.query(0, self.water - 34560000 - 1)
        self.assertEqual(rows, [])

    def test_pruning_removes_expired_points(self):
        store = make_store([(0, 1), (100, 2), (self.water, 5)])
        stats = {name: n for name, n, _lo, _hi in store.stats()}
        self.assertEqual(stats["raw"], 1)  # 早于 W-259200 的点被裁掉
        rows = store.query(0, 100)
        self.assertEqual(rows, [])


class TestEmptyStore(unittest.TestCase):
    def test_empty(self):
        store = Store()
        self.assertEqual(store.query(0, 10**9), [])
        self.assertEqual(
            store.stats(),
            [("raw", 0, None, None), ("minute", 0, None, None),
             ("hour", 0, None, None), ("day", 0, None, None)],
        )


if __name__ == "__main__":
    unittest.main()
