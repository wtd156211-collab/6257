"""命令行：query / stats / render。退出码 0 成功，1 输入不可用，2 用法错误。"""

from __future__ import annotations

import argparse
import os
import sys

from .engine import format_row
from .io import InputError, load_points, load_query
from .render import render_html


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rollkeep", description="单条时间序列的降采样与保留：写入算粗层，查询按层拼接。"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_query = sub.add_parser("query", help="跑一个查询文件或整个查询目录")
    p_query.add_argument("--queries", required=True, help="查询文件或目录（目录按文件名字典序）")
    p_query.add_argument("--out", help="把与 stdout 逐字节相同的结果写到该文件")

    p_stats = sub.add_parser("stats", help="输出四层条目数与保有区间")
    p_stats.add_argument("--points", required=True, help="原始点 jsonl")

    p_render = sub.add_parser("render", help="画一条查询的层覆盖与取层明细")
    p_render.add_argument("--query", required=True, help="单个查询 jsonl")
    p_render.add_argument("--html", required=True, help="输出 HTML 路径")
    return parser


def _query_files(path: str) -> list[str]:
    if os.path.isdir(path):
        return [
            os.path.join(path, name)
            for name in sorted(os.listdir(path))
            if os.path.isfile(os.path.join(path, name))
        ]
    if os.path.isfile(path):
        return [path]
    raise InputError(f"找不到查询路径: {path}")


def _write_text(path: str, text: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _cmd_query(args) -> str:
    stores: dict[str, object] = {}
    chunks: list[str] = []
    for path in _query_files(args.queries):
        query = load_query(path)
        points = query["points"]
        if points not in stores:
            stores[points] = load_points(points)
        store = stores[points]
        for row in store.query(query["from"], query["to"]):
            chunks.append(format_row(query["id"], row))
    text = "".join(line + "\n" for line in chunks)
    if args.out:
        _write_text(args.out, text)
    return text


def _cmd_stats(args) -> str:
    store = load_points(args.points)
    lines = []
    for name, count, lo, hi in store.stats():
        lo_text = "-" if lo is None else str(lo)
        hi_text = "-" if hi is None else str(hi)
        lines.append(f"{name}\t{count}\t{lo_text}\t{hi_text}")
    return "\n".join(lines) + "\n"


def _cmd_render(args) -> str:
    query = load_query(args.query)
    store = load_points(query["points"])
    rows = store.query(query["from"], query["to"])
    page = render_html(query, store, rows)
    _write_text(args.html, page)
    return ""


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "query":
            sys.stdout.write(_cmd_query(args))
        elif args.command == "stats":
            sys.stdout.write(_cmd_stats(args))
        elif args.command == "render":
            _cmd_render(args)
    except InputError as exc:
        print(f"rollkeep: {exc}", file=sys.stderr)
        return 1
    return 0
