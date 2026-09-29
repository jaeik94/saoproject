"""무기별 1:1 평가: data/builds.json의 빌드마다 각 적과 싸운 결과를 표로 보여 준다. 수치를 맞추지는 않는다.

    python -m app.builds --n 100
    python -m app.builds --n 100 --bot party --knowledge none

피해는 "대응 실패 피해"(가드 칩 제외)를 체력 대비로 보여 준다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.batch import run_one
from app.bots import BOTS
from app.loader import DATA_DIR, load_game_data, read_json
from core.battle import Battle
from core.formulas import damage
from core.loadout import build_player


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="무기별 1:1 평가")
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--bot", default="perfect", choices=sorted(BOTS))
    ap.add_argument("--knowledge", default=None, choices=["none", "full"], help="기본: perfect면 full, 나머지는 none")
    ap.add_argument("--data", type=Path, default=DATA_DIR)
    args = ap.parse_args()
    knowledge = args.knowledge or ("full" if args.bot == "perfect" else "none")

    data = load_game_data(args.data)
    builds = [build_player(data, b) for b in read_json(args.data / "builds.json")["builds"]]
    enemies = list(data.enemies.values())
    print(f"■ 무기별 1:1 — 봇 {args.bot}, 정보 {knowledge}, 조건마다 {args.n}판")
    print("  칸: 승률 / 대응 실패 피해 / 가드 칩 / 평균 시간 (괄호: 그 적을 잡는 데 필요한 소드 스킬 수)")
    header = f"  {'빌드':<8}" + "".join(f"{e.name:<30}" for e in enemies)
    print(header)
    for spec in builds:
        cells = []
        for e in enemies:
            b = Battle(data, (spec,), e, 1, 0)
            p, en = b.allies[0], b.enemies[0]
            dmgs = [sum(damage(p, s, h, en, 10000) for h in s.hits) for s in p.sword_skills]
            hits = e.hp / (sum(dmgs) / len(dmgs))
            wins = chip = fail = 0
            secs = 0.0
            for i in range(args.n):
                res, log = run_one(data, (spec,), e.id, 1, args.seed + i, args.bot, knowledge)
                wins += res.winner == "party"
                secs += res.end_tick / data.rules.ticks_per_sec
                for ev in log:
                    if ev.event == "hit" and ev.info.startswith("ally"):
                        if ev.info.endswith(":chip"):
                            chip += ev.value
                        else:
                            fail += ev.value
            total = spec.max_hp * args.n
            cells.append(f"{wins / args.n:4.0%}/{fail / total:4.0%}/{chip / total:3.0%}/{secs / args.n:4.1f}s ({hits:.1f})")
        print(f"  {spec.name:<8}" + "".join(f"{c:<30}" for c in cells))


if __name__ == "__main__":
    main()
