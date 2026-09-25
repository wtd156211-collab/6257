"""降采样与保留引擎：写入时算好粗层，查询按层拼接去重。"""
from __future__ import annotations

import bisect
import json
from collections import namedtuple
from decimal import ROUND_HALF_UP, Decimal, localcontext
from pathlib import Path

LAYER_NAMES = ("raw", "minute", "hour", "day")
COARSE_LAYERS = ("minute", "hour", "day")
BUCKET_WIDTH = {"raw": 1, "minute": 60, "hour": 3600, "day": 86400}
RETENTION = {"raw": 259200, "minute": 1209600, "hour": 10368000, "day": 34560000}

Row = namedtuple("Row", "layer start end count total vmin vmax")
LayerStat = namedtuple("LayerStat", "layer entries first_start last_end")


class InputError(Exception):
    """输入不可用，对应退出码 1。"""


def _ceil_div(a: int, b: int) -> int:
    return -((-a) // b)


def format_avg(total: int, count: int) -> str:
    with localcontext() as ctx:
        ctx.prec = 50
        avg = (Decimal(total) / Decimal(count)).quantize(
            Decimal("0.001"), rounding=ROUND_HALF_UP
        )
    return f"{avg:.3f}"


def format_row(query_id: str, row: Row) -> str:
    return (
        f"{query_id}\t{row.layer}\t{row.start}\t{row.end}\t{row.count}"
        f"\t{row.total}\t{row.vmin}\t{row.vmax}"
        f"\t{format_avg(row.total, row.count)}\n"
    )


class Engine:
    """单条时间序列的四层存储：raw / minute / hour / day。"""

    def __init__(self) -> None:
        self._raw: list[tuple[int, int]] = []
        self._raw_lo = 0
        self._buckets: dict[str, dict[int, list[int]]] = {
            name: {} for name in COARSE_LAYERS
        }
        self._trimmed: dict[str, int] = {}
        # 层 kmin 下次可能变化的水位阈值，避免每点重算
        self._trim_at: dict[str, int] = {name: 0 for name in COARSE_LAYERS}
        self._last_t: int | None = None
        self.watermark: int | None = None

    def add_point(self, t: int, v: int) -> None:
        last = self._last_t
        if last is not None and t <= last:
            raise InputError(f"t 必须严格递增：{t} 未大于前一点 {last}")
        self._last_t = t
        self.watermark = t
        self._raw.append((t, v))
        for name in COARSE_LAYERS:
            width = BUCKET_WIDTH[name]
            k = t // width
            bucket = self._buckets[name].get(k)
            if bucket is None:
                self._buckets[name][k] = [1, v, v, v]
            else:
                bucket[0] += 1
                bucket[1] += v
                if v < bucket[2]:
                    bucket[2] = v
                if v > bucket[3]:
                    bucket[3] = v
        self._trim()

    def _trim(self) -> None:
        watermark = self.watermark
        limit = watermark - RETENTION["raw"]
        raw = self._raw
        lo = self._raw_lo
        n = len(raw)
        while lo < n and raw[lo][0] < limit:
            lo += 1
        if lo > 8192 and lo * 2 > n:
            del raw[:lo]
            lo = 0
        self._raw_lo = lo
        for name in COARSE_LAYERS:
            if watermark < self._trim_at[name]:
                continue
            width = BUCKET_WIDTH[name]
            retention = RETENTION[name]
            kmin = _ceil_div(watermark - retention + 1, width) - 1
            prev = self._trimmed.get(name)
            start = prev if prev is not None else kmin
            if kmin > start:
                buckets = self._buckets[name]
                for k in range(start, kmin):
                    buckets.pop(k, None)
            self._trimmed[name] = kmin
            self._trim_at[name] = (kmin + 1) * width + retention

    def answer_interval(self, name: str) -> tuple[int, int]:
        """层 name 负责回答的闭区间。"""
        watermark = self.watermark
        idx = LAYER_NAMES.index(name)
        lo = watermark - RETENTION[name]
        finer = RETENTION[LAYER_NAMES[idx - 1]] if idx > 0 else -1
        return lo, watermark - finer - 1

    def query(self, frm: int, to: int) -> list[Row]:
        if frm > to:
            raise InputError(f"查询区间非法：from {frm} > to {to}")
        if self.watermark is None:
            return []
        rows: list[Row] = []
        for name in ("day", "hour", "minute", "raw"):
            lo, hi = self.answer_interval(name)
            a = max(frm, lo)
            b = min(to, hi)
            if a > b:
                continue
            if name == "raw":
                rows.extend(self._query_raw(a, b))
            else:
                rows.extend(self._query_buckets(name, a, b))
        return rows

    def _query_raw(self, a: int, b: int) -> list[Row]:
        raw = self._raw
        lo = self._raw_lo
        key = lambda p: p[0]  # noqa: E731
        left = bisect.bisect_left(raw, a, lo=lo, key=key)
        right = bisect.bisect_right(raw, b, lo=lo, key=key)
        return [Row("raw", t, t, 1, v, v, v) for t, v in raw[left:right]]

    def _query_buckets(self, name: str, a: int, b: int) -> list[Row]:
        width = BUCKET_WIDTH[name]
        k_lo = _ceil_div(a, width)
        k_hi = (b + 1) // width - 1
        if k_lo > k_hi:
            return []
        buckets = self._buckets[name]
        rows = []
        for k in sorted(buckets):
            if k_lo <= k <= k_hi:
                count, total, vmin, vmax = buckets[k]
                rows.append(
                    Row(name, k * width, k * width + width - 1, count, total, vmin, vmax)
                )
        return rows

    def layer_stats(self) -> list[LayerStat]:
        stats = []
        n = len(self._raw) - self._raw_lo
        if n:
            stats.append(
                LayerStat("raw", n, self._raw[self._raw_lo][0], self._raw[-1][0])
            )
        else:
            stats.append(LayerStat("raw", 0, None, None))
        for name in COARSE_LAYERS:
            buckets = self._buckets[name]
            if buckets:
                width = BUCKET_WIDTH[name]
                kmin = min(buckets)
                kmax = max(buckets)
                stats.append(
                    LayerStat(name, len(buckets), kmin * width, kmax * width + width - 1)
                )
            else:
                stats.append(LayerStat(name, 0, None, None))
        return stats


def _parse_point_line(line: str):
    """快路径解析 {"t": <int>, "v": <int>}；形状不符返回 None 走 json。"""
    parts = line.split('"')
    if len(parts) == 5 and parts[0] == "{" and parts[1] == "t" and parts[3] == "v":
        try:
            t = int(parts[2].strip(":, "))
            v = int(parts[4].strip(":} "))
        except ValueError:
            return None
        return t, v
    return None


def load_points(path) -> Engine:
    """流式读入原始点文件，写入即完成降采样与裁剪。"""
    path = Path(path)
    engine = Engine()
    try:
        fh = path.open("r", encoding="utf-8")
    except OSError as exc:
        raise InputError(f"无法读取原始点文件 {path}：{exc.strerror or exc}") from exc
    add_point = engine.add_point
    with fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            parsed = _parse_point_line(line)
            if parsed is not None:
                t, v = parsed
            else:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise InputError(f"{path}:{lineno} JSON 解析失败：{exc}") from exc
                if not isinstance(obj, dict):
                    raise InputError(f"{path}:{lineno} 每行须为 JSON 对象")
                t, v = obj.get("t"), obj.get("v")
                if (
                    not isinstance(t, int)
                    or not isinstance(v, int)
                    or isinstance(t, bool)
                    or isinstance(v, bool)
                ):
                    raise InputError(f"{path}:{lineno} t 与 v 须为整数")
            try:
                add_point(t, v)
            except InputError as exc:
                raise InputError(f"{path}:{lineno} {exc}") from exc
    return engine
