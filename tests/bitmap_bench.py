#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""r26 位图冲突检测对比实验（纯算法模拟口径，不动生产路径）。

对比两种"用户已约时段集合 × 目标时段"冲突检测实现：
  A. 朴素区间线扫 O(n)：逐条比较 [start,end) 相交；
  B. 时间片位图 O(1)：一天 48 个 30 分钟片，两个 uint64（低/高 24 片用掩码），
     查询 = 用户占用位图 & 目标时段位图，按位与一次判断。
模拟 n∈{100,1k,10k} 条既有预约，各执行 10^4 次随机目标查询，输出耗时 CSV 与倍数表
到 docs/evidence/bitmap/。注意：本实验为算法量级对比的模拟口径；生产系统采用 SQLite
索引化区间查询（等价于 A 的数据库执行形态），位图路线用于论证可选优化方向。
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import random
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
SLICES = 48  # 一天 48 片 × 30 分钟


def span_mask(start: int, end: int) -> int:
    """[start,end) 分钟 → 48 片位图（int 当 64 位用，Python 无溢出）。"""
    a = max(0, start // 30)
    b = min(SLICES, (end + 29) // 30)
    return ((1 << (b - a)) - 1) << a if b > a else 0


def naive_check(intervals, start: int, end: int) -> bool:
    for s, e in intervals:
        if s < end and start < e:
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--queries", type=int, default=10_000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=str(ROOT / "docs/evidence/bitmap"))
    args = ap.parse_args()
    out = pathlib.Path(args.out).resolve()
    if out != ROOT and ROOT not in out.parents:  # 输出必须落在仓库内，拒绝任意路径写入
        ap.error("--out 必须位于仓库目录内")
    rng = random.Random(args.seed)
    rows = []
    for n in (100, 1_000, 10_000):
        intervals = []
        user_mask = 0
        for _ in range(n):
            a = rng.randrange(0, 720 - 90)  # 已约全部落在上午（0..12 点）
            b = a + rng.choice((30, 60, 90))
            intervals.append((a, b))
            user_mask |= span_mask(a, b)
        hit_probes = [(rng.randrange(0, 720 - 60),) for _ in range(args.queries)]  # 必命中区
        miss_probes = [(rng.randrange(780, SLICES * 30 - 60),) for _ in range(args.queries)]  # 下午空区：朴素必全扫 O(n)
        hit_probes = [(a, a + 60) for (a,) in hit_probes]
        miss_probes = [(a, a + 60) for (a,) in miss_probes]
        for tag, probes, expect_hit in (("hit", hit_probes, True), ("miss_worst", miss_probes, False)):
            t0 = time.perf_counter()
            hits = sum(1 for a, b in probes if naive_check(intervals, a, b))
            t_naive = time.perf_counter() - t0
            t0 = time.perf_counter()
            hb = sum(1 for a, b in probes if user_mask & span_mask(a, b))
            t_bitmap = time.perf_counter() - t0
            mismatch = sum(1 for a, b in probes
                           if naive_check(intervals, a, b) != bool(user_mask & span_mask(a, b)))
            assert hits == hb == (args.queries if expect_hit else 0) or mismatch == 0
            rows.append(dict(n=n, scenario=tag, queries=args.queries,
                             naive_ms=round(t_naive * 1000, 2), bitmap_ms=round(t_bitmap * 1000, 2),
                             speedup=round(t_naive / t_bitmap, 1) if t_bitmap > 0 else None,
                             naive_hit=hits, bitmap_hit=hb, semantic_mismatch=mismatch))
    out.mkdir(parents=True, exist_ok=True)
    import io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)
    (out / "bitmap_bench.csv").write_text("\ufeff" + buf.getvalue(), encoding="utf-8")
    (out / "bitmap_bench.json").write_text(json.dumps(
        dict(meta=dict(queries=args.queries, seed=args.seed, slice_minutes=30,
                       note="纯算法模拟口径；生产路径为 SQLite 索引化区间查询"), rows=rows),
        ensure_ascii=False, indent=2), encoding="utf-8")
    for r in rows:
        print(f"n={r['n']:>6}: naive={r['naive_ms']:>8}ms bitmap={r['bitmap_ms']:>6}ms "
              f"speedup={r['speedup']}x mismatch={r['semantic_mismatch']}")
    print(f"证据已写入: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
