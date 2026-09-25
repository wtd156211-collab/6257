"""输入解析：原始点 jsonl 与查询 jsonl。解析失败一律抛 InputError（退出码 1）。"""

from __future__ import annotations

import json

from .engine import OutOfOrderError, Store


class InputError(Exception):
    """输入不可用：文件缺失、JSON 解析失败、t 乱序或重复、from > to。"""


def _fast_point(line: str):
    """快速解析 {"t": <int>, "v": <int>}；形状不符返回 None，交给 json 兜底。"""
    if line.startswith('{"t": ') and line.endswith("}"):
        sep = line.find(', "v": ', 6)
        if sep > 0:
            val_t = line[6:sep]
            val_v = line[sep + 7:-1]
            digits_t = val_t[1:] if val_t.startswith("-") else val_t
            if digits_t.isdigit() and val_v.isdigit():
                return int(val_t), int(val_v)
        return None
    return None


def parse_point(line: str, where: str) -> tuple[int, int]:
    hit = _fast_point(line)
    if hit is not None:
        return hit
    try:
        obj = json.loads(line)
    except ValueError:
        raise InputError(f"{where}: JSON 解析失败") from None
    if not isinstance(obj, dict):
        raise InputError(f"{where}: 每行应是一个对象")
    t, v = obj.get("t"), obj.get("v")
    if (
        not isinstance(t, int)
        or not isinstance(v, int)
        or isinstance(t, bool)
        or isinstance(v, bool)
    ):
        raise InputError(f"{where}: t 与 v 必须是整数")
    return t, v


def load_points(path: str) -> Store:
    """流式读入原始点，写入时同步算好三层粗层并裁剪。"""
    store = Store()
    try:
        lines = open(path, "r", encoding="utf-8")
    except OSError:
        raise InputError(f"打不开原始点文件: {path}") from None
    with lines:
        for lineno, line in enumerate(lines, 1):
            line = line.strip()
            if not line:
                raise InputError(f"{path}:{lineno}: 空行")
            t, v = parse_point(line, f"{path}:{lineno}")
            try:
                store.write(t, v)
            except OutOfOrderError as exc:
                raise InputError(f"{path}:{lineno}: {exc}") from None
    return store


def load_query(path: str) -> dict:
    """读一条查询：{"id", "points", "from", "to"}，from <= to 闭区间。"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            obj = json.load(fh)
    except OSError:
        raise InputError(f"打不开查询文件: {path}") from None
    except ValueError:
        raise InputError(f"{path}: JSON 解析失败") from None
    if not isinstance(obj, dict):
        raise InputError(f"{path}: 查询应是一个对象")
    try:
        qid = obj["id"]
        points = obj["points"]
        frm = obj["from"]
        to = obj["to"]
    except KeyError as exc:
        raise InputError(f"{path}: 缺少字段 {exc}") from None
    if not isinstance(qid, str) or not isinstance(points, str):
        raise InputError(f"{path}: id 与 points 必须是字符串")
    for name, val in (("from", frm), ("to", to)):
        if not isinstance(val, int) or isinstance(val, bool):
            raise InputError(f"{path}: {name} 必须是整数")
    if frm > to:
        raise InputError(f"{path}: from > to")
    return {"id": qid, "points": points, "from": frm, "to": to}
