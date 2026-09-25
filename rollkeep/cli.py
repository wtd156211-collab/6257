"""命令行：query / stats / render。退出码 0 成功、1 输入不可用、2 用法错误。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import engine as eng
from .render import render_html


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rollkeep", description="时间序列降采样与保留：写入算粗层，查询按层拼接。"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_query = sub.add_parser("query", help="跑查询文件，stdout 输出 9 列 TSV")
    p_query.add_argument("--queries", required=True, help="查询文件或目录（目录按文件名字典序）")
    p_query.add_argument("--out", help="把与 stdout 逐字节相同的结果写到该文件")

    p_stats = sub.add_parser("stats", help="输出四层条目数与覆盖区间")
    p_stats.add_argument("--points", required=True, help="原始点 jsonl 文件")

    p_render = sub.add_parser("render", help="把一条查询画成单文件 HTML")
    p_render.add_argument("--query", required=True, help="单个查询 jsonl 文件")
    p_render.add_argument("--html", required=True, help="输出 HTML 路径")
    return parser


def _load_query(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise eng.InputError(f"无法读取查询文件 {path}：{exc.strerror or exc}") from exc
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise eng.InputError(f"{path} JSON 解析失败：{exc}") from exc
    if not isinstance(obj, dict):
        raise eng.InputError(f"{path} 须为单个 JSON 对象")
    qid, points = obj.get("id"), obj.get("points")
    frm, to = obj.get("from"), obj.get("to")
    if not isinstance(qid, str) or not isinstance(points, str):
        raise eng.InputError(f"{path} 缺 id 或 points 字段")
    for name, val in (("from", frm), ("to", to)):
        if not isinstance(val, int) or isinstance(val, bool):
            raise eng.InputError(f"{path} 字段 {name} 须为整数")
    if frm > to:
        raise eng.InputError(f"{path} 查询区间非法：from {frm} > to {to}")
    return {"id": qid, "points": points, "from": frm, "to": to}


def _cmd_query(args) -> int:
    qpath = Path(args.queries)
    if qpath.is_dir():
        files = sorted(
            (p for p in qpath.iterdir() if p.suffix == ".jsonl"), key=lambda p: p.name
        )
    elif qpath.is_file():
        files = [qpath]
    else:
        raise eng.InputError(f"查询路径不存在：{qpath}")
    engines: dict[str, eng.Engine] = {}
    chunks = []
    for qf in files:
        query = _load_query(qf)
        points = query["points"]
        if points not in engines:
            engines[points] = eng.load_points(points)
        for row in engines[points].query(query["from"], query["to"]):
            chunks.append(eng.format_row(query["id"], row))
    data = "".join(chunks).encode("utf-8")
    if args.out:
        Path(args.out).write_bytes(data)
    sys.stdout.buffer.write(data)
    return 0


def _cmd_stats(args) -> int:
    engine = eng.load_points(args.points)
    lines = []
    for stat in engine.layer_stats():
        if stat.entries:
            lines.append(
                f"{stat.layer}\t{stat.entries}\t{stat.first_start}\t{stat.last_end}"
            )
        else:
            lines.append(f"{stat.layer}\t0\t-\t-")
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


def _cmd_render(args) -> int:
    query = _load_query(Path(args.query))
    engine = eng.load_points(query["points"])
    if engine.watermark is None:
        raise eng.InputError(f"{query['points']} 没有任何点，无法渲染")
    rows = engine.query(query["from"], query["to"])
    html = render_html(query["id"], query["from"], query["to"], engine, rows)
    Path(args.html).write_bytes(html.encode("utf-8"))
    return 0


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "query":
            return _cmd_query(args)
        if args.command == "stats":
            return _cmd_stats(args)
        if args.command == "render":
            return _cmd_render(args)
    except eng.InputError as exc:
        print(f"rollkeep: {exc}", file=sys.stderr)
        return 1
    return 2
