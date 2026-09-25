"""把一次查询的层覆盖与取层明细画成单文件 HTML（无脚本、无外部资源）。"""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from .engine import LAYER_NAMES, RETENTION, Engine, Row

_COLORS = {"raw": "#3b82f6", "minute": "#22a06b", "hour": "#e2a600", "day": "#d9480f"}

_SVG_W = 960
_MARGIN_L = 70
_MARGIN_R = 24
_BAND_H = 26
_BAND_GAP = 14
_TOP = 34
_BOTTOM_AXIS = 46


def _fmt_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _fmt_date(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def render_html(query_id: str, frm: int, to: int, engine: Engine, rows: list[Row]) -> str:
    stats = {s.layer: s for s in engine.layer_stats()}
    watermark = engine.watermark

    used = {name: [0, 0] for name in LAYER_NAMES}
    for row in rows:
        used[row.layer][0] += 1
        used[row.layer][1] += row.count

    lo_candidates = [watermark - RETENTION[name] for name in LAYER_NAMES]
    hi = watermark
    for name in LAYER_NAMES:
        s = stats[name]
        if s.entries:
            lo_candidates.append(s.first_start)
            hi = max(hi, s.last_end)
    lo = min(lo_candidates)
    span = max(hi - lo, 1)
    plot_w = _SVG_W - _MARGIN_L - _MARGIN_R

    def x(ts: int) -> float:
        return _MARGIN_L + (ts - lo) * plot_w / span

    svg_h = _TOP + len(LAYER_NAMES) * (_BAND_H + _BAND_GAP) + _BOTTOM_AXIS

    parts = []
    # 查询窗口底纹
    qx0 = x(max(frm, lo))
    qx1 = x(min(to, hi))
    if qx1 > qx0:
        parts.append(
            f'<rect x="{qx0:.2f}" y="{_TOP - 8}" width="{qx1 - qx0:.2f}" '
            f'height="{svg_h - _TOP - _BOTTOM_AXIS + 8}" fill="#eef2ff"/>'
        )
        parts.append(
            f'<text x="{(qx0 + qx1) / 2:.2f}" y="{_TOP - 14}" text-anchor="middle" '
            f'font-size="11" fill="#5b6b8c">查询窗口</text>'
        )

    bands = []
    labels = []
    for i, name in enumerate(LAYER_NAMES):
        y = _TOP + i * (_BAND_H + _BAND_GAP)
        s = stats[name]
        retention_start = watermark - RETENTION[name]
        color = _COLORS[name]
        if s.entries:
            x0, x1 = x(s.first_start), x(s.last_end)
            d_start, d_end = str(s.first_start), str(s.last_end)
            width = max(x1 - x0, 1.0)
        else:
            x0 = x1 = x(retention_start)
            d_start = d_end = ""
            width = 0.0
        bands.append(
            f'<rect data-layer="{name}" data-start="{d_start}" data-end="{d_end}" '
            f'data-retention-start="{retention_start}" data-buckets="{s.entries}" '
            f'x="{x0:.2f}" y="{y}" width="{width:.2f}" height="{_BAND_H}" '
            f'fill="{color}" fill-opacity="0.75" rx="2"/>'
        )
        labels.append(
            f'<text x="{_MARGIN_L - 8}" y="{y + _BAND_H / 2 + 4}" text-anchor="end" '
            f'font-size="12" fill="#33415c">{name}</text>'
        )
        rx = x(retention_start)
        labels.append(
            f'<line x1="{rx:.2f}" y1="{y - 4}" x2="{rx:.2f}" y2="{y + _BAND_H + 4}" '
            f'stroke="#b03030" stroke-width="1.4" stroke-dasharray="4 3"/>'
        )
        labels.append(
            f'<text x="{rx + 3:.2f}" y="{y - 6}" font-size="10" fill="#b03030">'
            f'保留起点 {_fmt_date(retention_start)}</text>'
        )

    ticks = []
    axis_y = svg_h - _BOTTOM_AXIS + 14
    for j in range(7):
        ts = lo + span * j // 6
        tx = x(ts)
        ticks.append(
            f'<line x1="{tx:.2f}" y1="{axis_y - 6}" x2="{tx:.2f}" y2="{axis_y}" '
            f'stroke="#8a94a6" stroke-width="1"/>'
        )
        ticks.append(
            f'<text x="{tx:.2f}" y="{axis_y + 14}" text-anchor="middle" '
            f'font-size="10" fill="#5b6b8c">{_fmt_date(ts)}</text>'
        )
    ticks.append(
        f'<line x1="{_MARGIN_L}" y1="{axis_y}" x2="{_SVG_W - _MARGIN_R}" y2="{axis_y}" '
        f'stroke="#8a94a6" stroke-width="1"/>'
    )

    svg = (
        f'<svg viewBox="0 0 {_SVG_W} {svg_h}" width="{_SVG_W}" height="{svg_h}" '
        f'xmlns="http://www.w3.org/2000/svg" role="img">'
        + "".join(parts)
        + '<g id="bands">'
        + "".join(bands)
        + "</g>"
        + "".join(labels)
        + "".join(ticks)
        + "</svg>"
    )

    detail_rows = []
    for name in LAYER_NAMES:
        nbuckets, npoints = used[name]
        detail_rows.append(
            f'<tr data-layer="{name}" data-buckets="{nbuckets}" '
            f'data-points="{npoints}"><td>{name}</td><td>{nbuckets}</td>'
            f"<td>{npoints}</td></tr>"
        )
    detail = (
        '<table><thead><tr><th>层</th><th>本次取桶数（raw 为点数）</th>'
        "<th>贡献点数（count 之和）</th></tr></thead>"
        '<tbody id="detail">'
        + "".join(detail_rows)
        + "</tbody></table>"
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>rollkeep 层级覆盖 — {escape(query_id)}</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 24px; color: #1f2933; }}
h1 {{ font-size: 18px; }}
p.meta {{ font-size: 13px; color: #52606d; }}
table {{ border-collapse: collapse; margin-top: 16px; font-size: 13px; }}
th, td {{ border: 1px solid #cbd2d9; padding: 4px 12px; text-align: right; }}
th:first-child, td:first-child {{ text-align: left; }}
thead {{ background: #f1f5f9; }}
</style>
</head>
<body>
<h1>查询 {escape(query_id)} 的层覆盖与取层明细</h1>
<p class="meta">查询区间 [{_fmt_ts(frm)} — {_fmt_ts(to)}]；水位 W = {watermark}（{_fmt_ts(watermark)}）。色带为各层保有区间，红色虚线为保留起点（W − 保留期）。</p>
{svg}
{detail}
</body>
</html>
"""
