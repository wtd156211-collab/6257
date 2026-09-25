"""降采样与保留引擎：写入时算粗层，查询按层拼接。

口径见 README 第 2 节：四层（raw/minute/hour/day）、整桶保留、
闭区间拼接、半截桶整桶丢弃。查询阶段只读已算好的桶，不重算。
"""

from __future__ import annotations

import bisect
from array import array
from decimal import ROUND_HALF_UP, Decimal

SECONDS_PER_DAY = 86400


class Layer:
    """一个降采样层：桶宽（秒）与保留期（秒）。raw 层桶宽为 1，逐点存储。"""

    __slots__ = ("name", "width", "retention")

    def __init__(self, name: str, width: int, retention: int) -> None:
        self.name = name
        self.width = width
        self.retention = retention


LAYERS = (
    Layer("raw", 1, 3 * SECONDS_PER_DAY),
    Layer("minute", 60, 14 * SECONDS_PER_DAY),
    Layer("hour", 3600, 120 * SECONDS_PER_DAY),
    Layer("day", SECONDS_PER_DAY, 400 * SECONDS_PER_DAY),
)
RAW = LAYERS[0]
COARSE = LAYERS[1:]

# 层 L 的条目数上界：保留期 // 桶宽 + 1（多留不到一个桶宽）
STORAGE_BOUNDS = {layer.name: layer.retention // layer.width + 1 for layer in LAYERS}


class OutOfOrderError(ValueError):
    """t 非严格递增（乱序或重复），输入不可用。"""


class Store:
    """一条时间序列的四层存储。

    原始层按 t 升序存点；粗层按 k = t // 桶宽 建索引，一桶一条
    [count, sum, min, max]。写入即推进水位并按整桶裁剪。
    """

    def __init__(self) -> None:
        self.water: int | None = None
        self._raw_t = array("q")
        self._raw_v = array("q")
        self._raw_lo = 0  # 已裁掉的原始点前缀长度
        self._buckets = {layer.name: {} for layer in COARSE}
        self._keys = {layer.name: [] for layer in COARSE}  # 升序桶号（含已裁前缀）
        self._keys_lo = {layer.name: 0 for layer in COARSE}

    def write(self, t: int, v: int) -> None:
        if self.water is not None and t <= self.water:
            raise OutOfOrderError(f"t={t} 未严格递增（水位 {self.water}）")
        self.water = t
        self._raw_t.append(t)
        self._raw_v.append(v)
        for layer in COARSE:
            k = t // layer.width
            buckets = self._buckets[layer.name]
            rec = buckets.get(k)
            if rec is None:
                buckets[k] = [1, v, v, v]
                self._keys[layer.name].append(k)
            else:
                rec[0] += 1
                rec[1] += v
                if v < rec[2]:
                    rec[2] = v
                if v > rec[3]:
                    rec[3] = v
        self._prune()

    def _prune(self) -> None:
        water = self.water
        # 原始层保留 t >= W - 259200
        raw_t = self._raw_t
        lo = self._raw_lo
        floor = water - RAW.retention
        n = len(raw_t)
        while lo < n and raw_t[lo] < floor:
            lo += 1
        if lo:
            self._raw_lo = lo
            if lo > 4096 and lo * 2 >= n:
                del raw_t[:lo]
                del self._raw_v[:lo]
                self._raw_lo = 0
        # 粗层保留「桶尾 >= W - 保留期」的桶
        for layer in COARSE:
            name = layer.name
            width = layer.width
            floor = water - layer.retention
            keys = self._keys[name]
            kp = self._keys_lo[name]
            buckets = self._buckets[name]
            total = len(keys)
            while kp < total and keys[kp] * width + width - 1 < floor:
                del buckets[keys[kp]]
                kp += 1
            if kp:
                self._keys_lo[name] = kp
                if kp > 4096 and kp * 2 >= total:
                    del keys[:kp]
                    self._keys_lo[name] = 0

    def answer_interval(self, layer: Layer) -> tuple[int, int]:
        """层 L 的回答区间 [W - 保留期, W - 更细层保留期 - 1]，闭区间。"""
        water = self.water
        finer_retention = -1
        for candidate in LAYERS:
            if candidate is layer:
                return water - layer.retention, water - finer_retention - 1
            finer_retention = candidate.retention
        raise KeyError(layer.name)

    def query(self, frm: int, to: int) -> list[tuple]:
        """按细到粗去重拼接，返回 (层, 起点, 终点, count, sum, min, max)，时间升序。"""
        rows: list[tuple] = []
        if self.water is None:
            return rows
        # 粗层按回答区间从远到近（day → hour → minute），天然时间升序
        for layer in reversed(COARSE):
            lo, hi = self.answer_interval(layer)
            lo = max(lo, frm)
            hi = min(hi, to)
            if lo > hi:
                continue
            width = layer.width
            # 只取完整落在 [lo, hi] 里的桶，半截桶整桶丢弃
            k_lo = -(-lo // width)
            k_hi = (hi + 1 - width) // width
            if k_lo > k_hi:
                continue
            keys = self._keys[layer.name]
            buckets = self._buckets[layer.name]
            i = bisect.bisect_left(keys, k_lo, self._keys_lo[layer.name])
            j = bisect.bisect_right(keys, k_hi, i)
            for k in keys[i:j]:
                count, total, vmin, vmax = buckets[k]
                rows.append(
                    (layer.name, k * width, k * width + width - 1, count, total, vmin, vmax)
                )
        # 原始层：点本身落在区间内即输出
        lo, hi = self.answer_interval(RAW)
        lo = max(lo, frm)
        hi = min(hi, to)
        if lo <= hi:
            raw_t = self._raw_t
            raw_v = self._raw_v
            i = bisect.bisect_left(raw_t, lo, self._raw_lo)
            j = bisect.bisect_right(raw_t, hi, i)
            for idx in range(i, j):
                v = raw_v[idx]
                rows.append((RAW.name, raw_t[idx], raw_t[idx], 1, v, v, v))
        return rows

    def stats(self) -> list[tuple]:
        """每层 (层代号, 条目数, 最早起点, 最晚终点)；空层起点终点为 None。"""
        out = []
        n = len(self._raw_t) - self._raw_lo
        if n:
            out.append((RAW.name, n, self._raw_t[self._raw_lo], self._raw_t[-1]))
        else:
            out.append((RAW.name, 0, None, None))
        for layer in COARSE:
            keys = self._keys[layer.name]
            lo = self._keys_lo[layer.name]
            n = len(keys) - lo
            if n:
                out.append(
                    (layer.name, n, keys[lo] * layer.width, keys[-1] * layer.width + layer.width - 1)
                )
            else:
                out.append((layer.name, 0, None, None))
        return out


_AVG_QUANT = Decimal("0.001")


def average(total: int, count: int) -> str:
    """sum / count，ROUND_HALF_UP，固定 3 位小数。"""
    return str((Decimal(total) / Decimal(count)).quantize(_AVG_QUANT, rounding=ROUND_HALF_UP))


def format_row(query_id: str, row: tuple) -> str:
    name, start, end, count, total, vmin, vmax = row
    return (
        f"{query_id}\t{name}\t{start}\t{end}\t{count}\t{total}\t{vmin}\t{vmax}"
        f"\t{average(total, count)}"
    )
