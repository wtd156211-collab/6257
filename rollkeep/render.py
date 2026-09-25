"""把一次查询的层覆盖与取层明细画成单文件 HTML（无脚本、无外部资源）。

所有数字与色带范围都来自引擎统计：色带用 Store.stats() 的保有区间，
明细行用本次查询结果按层汇总。
"""

from __future__ import annotations

import html
import time

from .engine import COARSE, LAYERS, RAW, Store

_COLORS = {"raw": "#2f81f7", "minute": "#3fb950", "hour": "#d29922", "day": "#f47067"}
_SVG_W = 1000
_X0 = 60
_X1 = 960
_BAND_H = 26
_BAND_GAP = 18
_TOP = 46


def _fmt_ts(t: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(t))


def _esc(text) -> str:
    return html.escape(str(text), quote=True)


def render_html(query: dict, store: Store, rows: list[tuple]) -> str:
    water = store.water
    stats = {name: (n, lo, hi) for name, n, lo, hi in store.stats()}

    # 本次查询每层取到的桶数（原始层为点数）与贡献点数（count 之和）
    detail = {layer.name: [0, 0] for layer in LAYERS}
    for name, _start, _end, count, _total, _vmin, _vmax in rows:
        detail[name][0] += 1
        detail[name][1] += count

    # 时间轴范围：覆盖各层保有区间、保留起点与查询区间
    lo_candidates = [query["from"]]
    hi_candidates = [query["to"]]
    if water is not None:
        hi_candidates.append(water)
        for layer in LAYERS:
            lo_candidates.append(water - layer.retention)
    for n, lo, hi in stats.values():
        if n:
            lo_candidates.append(lo)
            hi_candidates.append(hi)
    t0 = min(lo_candidates)
    t1 = max(hi_candidates)
    span = (t1 - t0) or 1

    def x(t: int) -> float:
        return _X0 + (t - t0) * (_X1 - _X0) / span

    bands = []
    markers = []
    labels = []
    for i, layer in enumerate(LAYERS):
        y = _TOP + i * (_BAND_H + _BAND_GAP)
        ymid = y + _BAND_H / 2
        n, lo, hi = stats[layer.name]
        color = _COLORS[layer.name]
        if n:
            rx, rw = x(lo), max(x(hi) - x(lo), 1.0)
            attr_start, attr_end = str(lo), str(hi)
        else:
            rx, rw = _X0, 0.0
            attr_start = attr_end = ""
        retention_start = "" if water is None else str(water - layer.retention)
        bands.append(
            f'<rect data-layer="{layer.name}" data-start="{attr_start}" '
            f'data-end="{attr_end}" data-retention-start="{retention_start}" '
            f'data-buckets="{n}" x="{rx:.2f}" y="{y}" width="{rw:.2f}" '
            f'height="{_BAND_H}" fill="{color}" fill-opacity="0.75" rx="3"/>'
        )
        labels.append(
            f'<text x="{_X0 - 8}" y="{ymid:.1f}" text-anchor="end" '
            f'dominant-baseline="middle" class="layer-name">{layer.name}</text>'
        )
        if water is not None:
            mx = x(water - layer.retention)
            markers.append(
                f'<line x1="{mx:.2f}" y1="{y - 4}" x2="{mx:.2f}" y2="{y + _BAND_H + 4}" '
                f'stroke="{color}" stroke-width="1.5" stroke-dasharray="4 3"/>'
                f'<text x="{mx:.2f}" y="{y - 8}" text-anchor="middle" class="tick">'
                f'保留起点 {_esc(_fmt_ts(water - layer.retention))}</text>'
            )
    svg_h = _TOP + len(LAYERS) * (_BAND_H + _BAND_GAP) + 30

    # 查询区间遮罩
    qx0 = max(min(x(query["from"]), _X1), _X0)
    qx1 = max(min(x(query["to"]), _X1), _X0)
    overlay = (
        f'<rect x="{qx0:.2f}" y="{_TOP - 24}" width="{max(qx1 - qx0, 0.5):.2f}" '
        f'height="{svg_h - _TOP - 8}" fill="#8b949e" fill-opacity="0.12" '
        f'stroke="#8b949e" stroke-opacity="0.4" stroke-dasharray="6 4"/>'
    )

    # 时间轴刻度（5 等分，UTC）
    ticks = []
    axis_y = svg_h - 22
    for i in range(6):
        t = t0 + span * i // 5
        tx = x(t)
        ticks.append(
            f'<line x1="{tx:.2f}" y1="{axis_y}" x2="{tx:.2f}" y2="{axis_y + 5}" '
            f'stroke="#57606a" stroke-width="1"/>'
            f'<text x="{tx:.2f}" y="{axis_y + 18}" text-anchor="middle" class="tick">'
            f'{_esc(_fmt_ts(t))}</text>'
        )
    ticks.append(
        f'<line x1="{_X0}" y1="{axis_y}" x2="{_X1}" y2="{axis_y}" stroke="#57606a"/>'
    )

    detail_rows = []
    for layer in LAYERS:
        buckets, points = detail[layer.name]
        detail_rows.append(
            f'<tr data-layer="{layer.name}" data-buckets="{buckets}" '
            f'data-points="{points}"><td>{layer.name}</td><td>{buckets}</td>'
            f'<td>{points}</td></tr>'
        )

    stats_rows = []
    for layer in LAYERS:
        n, lo, hi = stats[layer.name]
        rng = f"{_fmt_ts(lo)} ~ {_fmt_ts(hi)}" if n else "-"
        stats_rows.append(
            f"<tr><td>{layer.name}</td><td>{n}</td><td>{_esc(rng)}</td>"
            f"<td>{layer.retention // 86400} 天</td></tr>"
        )

    water_text = "-" if water is None else f"{water}（{_fmt_ts(water)} UTC）"
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>rollkeep 层覆盖 — {_esc(query["id"])}</title>
<style>
body {{ font-family: "Helvetica Neue", "PingFang SC", sans-serif; margin: 32px; color: #1f2328; }}
h1 {{ font-size: 20px; }}
.meta {{ color: #57606a; font-size: 13px; margin-bottom: 16px; }}
svg {{ display: block; background: #f6f8fa; border: 1px solid #d0d7de; border-radius: 6px; }}
.layer-name {{ font-size: 13px; fill: #1f2328; font-weight: 600; }}
.tick {{ font-size: 10px; fill: #57606a; }}
table {{ border-collapse: collapse; margin-top: 20px; font-size: 13px; }}
th, td {{ border: 1px solid #d0d7de; padding: 5px 14px; text-align: right; }}
th {{ background: #f6f8fa; }}
td:first-child, th:first-child {{ text-align: left; }}
h2 {{ font-size: 15px; margin-top: 28px; }}
</style>
</head>
<body>
<h1>rollkeep 层覆盖与取层明细</h1>
<p class="meta">查询 <strong>{_esc(query["id"])}</strong> · 区间 [{query["from"]}, {query["to"]}]
（{_esc(_fmt_ts(query["from"]))} ~ {_esc(_fmt_ts(query["to"]))} UTC） · 水位 W = {_esc(water_text)}</p>
<svg id="bands" width="{_SVG_W}" height="{svg_h}" viewBox="0 0 {_SVG_W} {svg_h}"
 xmlns="http://www.w3.org/2000/svg" role="img" aria-label="各层覆盖区间">
{overlay}
{chr(10).join(bands)}
{chr(10).join(labels)}
{chr(10).join(markers)}
{chr(10).join(ticks)}
</svg>
<h2>本次查询取层明细</h2>
<table id="detail">
<thead><tr><th>层</th><th>桶数（原始层为点数）</th><th>贡献点数</th></tr></thead>
<tbody>
{chr(10).join(detail_rows)}
</tbody>
</table>
<h2>各层保有统计</h2>
<table id="stats">
<thead><tr><th>层</th><th>条目数</th><th>保有区间（UTC）</th><th>保留期</th></tr></thead>
<tbody>
{chr(10).join(stats_rows)}
</tbody>
</table>
</body>
</html>
"""
