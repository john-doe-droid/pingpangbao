# -*- coding: utf-8 -*-
"""P3 阶段专用循环配置（独立于 P2，不沿用 P2 的旧达标状态）。

对应 README「换一个目标」的流程：把本阶段的 goal / verify / 基线放在项目内，
指向 verify_stage3.py。

为什么不用工作区里的 agent-loop：
    那个目录（C:\\Users\\刘晨阳\\Desktop\\化学\\agent-loop）已记录 goal.json 与
    verify.json 的哈希，改动会破坏既有护栏检查。P3 按任务要求「先按 README 的
    换目标流程配置本阶段专用的 goal、verify 和验收基线」，因此在这里独立建立。
"""
import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
LOOP_DIR = BASE_DIR / "p3-loop"


def main():
    LOOP_DIR.mkdir(parents=True, exist_ok=True)

    goal = {
        "objective": (
            "在乒乓球社 Django 项目上实现工作成果提交与审核闭环："
            "版本化提交、版本级附件、不可覆盖的审核记录、基于 PostgreSQL 行级锁的"
            "提交次数与图片配额并发控制，以及图片处理模块与私有存储适配层。"
        ),
        "acceptance": [
            "tests/p3 全部通过（图片纯逻辑 / 配额 / 提交审核 / 存储 / 并发）",
            "P1 与 P2 既有测试全部保持通过，无删除、无放宽断言",
            "scripts/verify_stage3.py 正常路径退出码 0，人为失败时非零",
            "core/imaging.py 的魔数+解码双重校验、EXIF 方向、白底、1280 主图、400 方缩略图均实测生效",
            "提交次数与图片配额在真实并发下不超额",
            "审核：退回必填理由、不能审自己、并发只一个有效结果、重新审核保留原历史",
            "私有附件匿名不可直取、跨用户被拒、预签名链接到期失效",
            "生产缺 COS 配置时报错而非回退本地",
        ],
        "how_to_verify": "python scripts/verify_stage3.py（退出码 0 表示达标）",
        "runtime_expectation": {
            "name": "P3_ACCEPTANCE_TOKEN",
            "note": "由驱动器运行时生成、只经环境变量下发，工作区内搜不到",
        },
        "budget": {"max_rounds": 5, "max_tokens_total": 900000},
        "created_at": "2026-10-18",
    }
    (LOOP_DIR / "goal.json").write_text(
        json.dumps(goal, ensure_ascii=False, indent=2), encoding="utf-8")

    gate = {
        "command": '"$PY" scripts/verify_stage3.py',
        "cwd": ".",
        "timeoutMs": 1800000,
        "maxBlocks": 3,
        "note": (
            "$PY 由闸门替换为当前解释器路径。"
            "指向 P3 自己的验收入口，不复用 P2 的。"
        ),
    }
    (LOOP_DIR / "verify.json").write_text(
        json.dumps(gate, ensure_ascii=False, indent=2), encoding="utf-8")

    # 本阶段验收基线（P3 开工时的 P1/P2 文件哈希）——
    # 与 scripts/p3_baseline.json 同源，这里放一份便于闸门就近读取
    src = BASE_DIR / "scripts" / "p3_baseline.json"
    if src.exists():
        (LOOP_DIR / "baseline.json").write_text(
            src.read_text(encoding="utf-8"), encoding="utf-8")

    print("P3 专用配置已建立：")
    for name in ("goal.json", "verify.json", "baseline.json"):
        path = LOOP_DIR / name
        print("  %s  %s" % ("OK " if path.exists() else "缺失", path.relative_to(BASE_DIR)))
    print("\n目标：%s" % goal["objective"])
    print("闸门：%s" % gate["command"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
