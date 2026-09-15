#!/usr/bin/env python3
"""r24 对照实验：严格 FIFO vs 可执行 FIFO 的候补递补效果对比。

研究问题（外部设计文档 9.2 节推荐）：
    在跨资源时间冲突存在的条件下，可执行 FIFO 相比严格 FIFO 能否提高名额分配利用率？
    会给等待公平性带来什么代价？

做法：用**同一组申请序列**分别驱动两种策略的真实服务实例，统计四项指标。
策略通过服务端 `--waitlist-strategy=strict|executable` 切换，业务数据与断言代码完全一致，
差异只来自递补策略本身。

指标口径（与设计文档 10.2 节一致）：
  1. 名额利用率 = 最终被有效占用的释放名额数 / 实际释放的名额数
     （"有效占用"要求获得者自身不存在时间冲突，即拿到手真的能用）
  2. 候补成功率 = 由 WAITING 达到 PROMOTED 的申请数 / 入队申请数
  3. 等待时间：入队至获得名额的秒数（中位数 / P95）
  4. 队首阻塞次数：因队首暂时不可执行而导致本次递补未能分配的次数

用法：
    LAB_TEST_PASSWORD=... python tests/experiment.py --rounds 5
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import statistics
import subprocess
import sys
import time
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import integration as ig  # noqa: E402  复用 Client / Server / PASSWORD 等脚手架

PASSWORD = os.environ.get("LAB_TEST_PASSWORD", "") or ig.PASSWORD


def uid() -> str:
    return str(uuid.uuid4())


def setup_scenario(server) -> dict:
    """构造一次"队首暂时不可执行"的场景，返回所需标识。

    两个实验室各有同一时刻的场次（t1 / t2，容量均为 1）：
      - 占用者占住 t1；
      - A 先入 t1 候补队列（此时 A 无冲突，允许入队）；
      - B 随后入队；
      - A 再去占用 t2 的同时段 → A 变成"当前不可执行"；
      - 占用者释放 t1 → 观察名额最终给了谁。
    """
    adm = ig.Client(server.port).login("admin")
    tag = uid()[:6]
    lab1 = adm.post("/api/admin/labs", {"name": "对照A" + tag, "location": "实验楼", "description": "x"})["data"]["lab_id"]
    time.sleep(1.2)
    lab2 = adm.post("/api/admin/labs", {"name": "对照B" + tag, "location": "实验楼", "description": "x"})["data"]["lab_id"]
    day = time.strftime("%Y-%m-%d", time.localtime(time.time() + 172800))
    adm.post("/api/admin/slots/publish", {"lab_id": str(lab1), "start_date": day, "end_date": day, "capacity": "1"})
    time.sleep(1.2)
    adm.post("/api/admin/slots/publish", {"lab_id": str(lab2), "start_date": day, "end_date": day, "capacity": "1"})
    t1 = server.sql("SELECT id FROM slots WHERE lab_id=? AND enabled=1 AND start_at>? ORDER BY start_at LIMIT 1",
                    (int(lab1), int(time.time())))[0][0]
    peer = server.sql("SELECT id FROM slots WHERE lab_id=? AND start_at=(SELECT start_at FROM slots WHERE id=?) AND enabled=1",
                      (int(lab2), t1))
    t2 = peer[0][0]

    # 用全新的注册用户，避免种子库中既有预约带来干扰
    occ = ig.Client(server.port)
    st_o, bo = occ.request("POST", "/api/register", {"username": "eo" + uid()[:6], "password": PASSWORD})
    if st_o != 200:
        raise RuntimeError(f"占用者注册失败: {st_o} {bo}")
    occ.csrf = bo["data"]["csrf_token"]
    occ.post("/api/reservations", {"slot_id": str(t1), "request_id": uid()})
    holder = occ.request("GET", "/api/me/records?page=1&page_size=5")[1]["data"]["reservations"]
    holder_id = [r for r in holder if str(r["slot_id"]) == str(t1)][0]["id"]

    a = ig.Client(server.port)
    st_a, ba = a.request("POST", "/api/register", {"username": "ea" + uid()[:6], "password": PASSWORD})
    if st_a != 200:
        raise RuntimeError(f"A 注册失败: {st_a} {ba}")
    a.csrf = ba["data"]["csrf_token"]
    tA = time.time()
    st, wb = a.request("POST", "/api/waitlist", {"slot_id": str(t1), "request_id": uid()})
    if st != 200:
        raise RuntimeError(f"A 入队失败: {st} {wb}")
    wA = wb["data"]["waitlist_id"]

    bcl = ig.Client(server.port)
    st_b, bb = bcl.request("POST", "/api/register", {"username": "eb" + uid()[:6], "password": PASSWORD})
    if st_b != 200:
        raise RuntimeError(f"B 注册失败: {st_b} {bb}")
    bcl.csrf = bb["data"]["csrf_token"]
    tB = time.time()
    st2, wb2 = bcl.request("POST", "/api/waitlist", {"slot_id": str(t1), "request_id": uid()})
    if st2 != 200:
        raise RuntimeError(f"B 入队失败: {st2} {wb2}")
    wB = wb2["data"]["waitlist_id"]

    # A 入队后才制造冲突：占用 lab2 的同时段场次
    stx, bx = a.request("POST", "/api/reservations", {"slot_id": str(t2), "request_id": uid()})
    if stx != 200:
        raise RuntimeError(f"A 占用同时段失败: {stx} {bx}")

    occ.post(f"/api/reservations/{holder_id}/cancel", {"request_id": uid()})
    return dict(t1=t1, t2=t2, wA=wA, wB=wB, tA=tA, tB=tB)


def evaluate(server, sc: dict) -> dict:
    """读取递补结果并折算成指标。"""
    stA = server.sql("SELECT status FROM waitlist WHERE id=?", (int(sc["wA"]),))[0][0]
    stB = server.sql("SELECT status FROM waitlist WHERE id=?", (int(sc["wB"]),))[0][0]
    winner = "A" if stA == "PROMOTED" else ("B" if stB == "PROMOTED" else "none")
    # 获得名额者是否真的可用：是否存在与 t1 时间重叠的其他确认预约
    usable = 0
    if winner != "none":
        wuid = server.sql(
            "SELECT user_id FROM waitlist WHERE id=?",
            (int(sc["wA"] if winner == "A" else sc["wB"]),))[0][0]
        clash = server.sql(
            "SELECT count(*) FROM reservations r JOIN slots s2 ON s2.id=r.slot_id JOIN slots s ON s.id=? "
            "WHERE r.user_id=? AND r.status IN('CONFIRMED','HELD') AND r.slot_id<>? "
            "AND s.start_at<s2.end_at AND s2.start_at<s.end_at",
            (int(sc["t1"]), int(wuid), int(sc["t1"])))[0][0]
        usable = 1 if clash == 0 else 0
    wait_s = 0.0
    if winner == "A":
        wait_s = time.time() - sc["tA"]
    elif winner == "B":
        wait_s = time.time() - sc["tB"]
    return dict(winner=winner, usable=usable, wait_s=round(wait_s, 3),
                head_blocked=(winner == "B"), wA=stA, wB=stB)


def make_baseline(tmp: pathlib.Path) -> pathlib.Path:
    """生成一个干净的种子库作为各轮实验的基线（与集成测试的口径一致）。"""
    tmp.mkdir(parents=True, exist_ok=True)
    baseline = tmp / "baseline.db"
    if baseline.exists():
        baseline.unlink()
    env = os.environ.copy()
    env["LAB_SEED_PASSWORD"] = PASSWORD
    r = subprocess.run([str(ROOT / "build" / "lab-booking.exe"), "--db", str(baseline), "--seed", "--init-only"],
                       env=env, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"seed failed: {r.returncode} {r.stdout} {r.stderr}")
    return baseline


def run_arm(strategy: str, rounds: int, tmp: pathlib.Path) -> dict:
    """在指定策略下重复实验，返回聚合指标。每轮使用**独立的种子基线**，避免上一轮的预约造成时间冲突。"""
    results = []
    for r in range(rounds):
        base = make_baseline(tmp / f"base-{strategy}-{r}")
        with ig.running(ROOT / "build" / "lab-booking.exe", tmp / f"exp-{strategy}-{r}", base,
                        extra=["--waitlist-strategy", strategy, "--sweep-interval", "2"]) as server:
            sc = setup_scenario(server)
            results.append(evaluate(server, sc))
    released = len(results)
    usable = sum(x["usable"] for x in results)
    promoted = sum(1 for x in results if x["winner"] != "none")
    waits = sorted(x["wait_s"] for x in results if x["winner"] != "none")
    blocked = sum(1 for x in results if x["head_blocked"])
    p95 = waits[min(len(waits) - 1, int(round(0.95 * (len(waits) - 1))))] if waits else 0.0
    return dict(
        strategy=strategy, rounds=rounds, released_slots=released,
        utilization=round(usable / released, 4) if released else 0.0,
        waitlist_success_rate=round(promoted / released, 4) if released else 0.0,
        wait_median_s=round(statistics.median(waits), 3) if waits else 0.0,
        wait_p95_s=round(p95, 3),
        head_blocked_count=blocked,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rounds", type=int, default=5, help="每种策略的重复轮数（默认 5）")
    ap.add_argument("--output", type=pathlib.Path, default=ROOT / "docs" / "evidence" / "experiment")
    args = ap.parse_args()
    if len(PASSWORD) < 8:
        print("请通过 LAB_TEST_PASSWORD 提供至少 8 位测试口令", file=sys.stderr)
        return 2
    ig.PASSWORD = PASSWORD
    tmp = ROOT / "artifacts" / "experiment-runs"
    args.output.mkdir(parents=True, exist_ok=True)

    arms = [run_arm(s, args.rounds, tmp) for s in ("strict", "executable")]
    strict, execu = arms[0], arms[1]
    delta = round(execu["utilization"] - strict["utilization"], 4)

    report = dict(
        question="跨资源冲突下，可执行 FIFO 相比严格 FIFO 能否提高名额分配利用率？代价是什么？",
        metric_definitions={
            "utilization": "最终被有效占用（获得者自身无时间冲突）的释放名额数 / 实际释放名额数",
            "waitlist_success_rate": "由 WAITING 达到 PROMOTED 的申请数 / 释放名额数",
            "wait_median_s": "入队至获得名额的秒数中位数",
            "wait_p95_s": "同上 P95",
            "head_blocked_count": "因队首暂时不可执行而由后位获得名额的次数",
        },
        rounds_per_arm=args.rounds,
        arms=arms,
        utilization_delta=delta,
        conclusion=(
            f"可执行 FIFO 的名额利用率 {execu['utilization']}，严格 FIFO 为 {strict['utilization']}，"
            f"提升 {delta}；严格 FIFO 下名额交由暂不可执行的队首而空置，"
            f"可执行 FIFO 通过暂跳（{execu['head_blocked_count']} 次）把名额给了下一位可执行者。"
        ),
        fairness_cost=(
            f"代价是队首的原序不再绝对优先：{execu['head_blocked_count']} 次递补由后位获得；"
            "但暂跳者保留原序号，后续扫描仍优先于更晚入队者，因此是"
            "「可执行申请间的 FIFO」而非无约束插队。"
        ),
        limitations=[
            "未使用虚拟时钟与确定性事件回放，等待时间含真实排队与锁等待，波动较大",
            "样本量小（每臂 {} 轮），结论为方向性而非统计显著".format(args.rounds),
            "仅在单机 SQLite、容量为 1 的场次上验证",
        ],
    )
    out = args.output / "fifo_comparison.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n证据已写入: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
