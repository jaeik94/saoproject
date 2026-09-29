"""적 등급 평가: 등급 기준표(data/tiers.json)와 봇 전투 결과를 나란히 보여 준다. 수치를 맞추지는 않는다.

    python -m app.tiers --n 100

1:1은 loadout.json(솔로), 3:1은 party.json. 봇: 완벽 대처(정보 있음), 단순(정보 없음), 평타(정보 없음).
피해는 "대응 실패 피해"(맞음, 태세 실패, 프렌들리 파이어)와 "가드 칩"(막았는데 스며든 피해)으로 나눈다.
기준의 "피해 없음"은 대응 실패 피해가 없다는 뜻이다 (가드 칩은 완벽 대처의 범주).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.batch import run_one
from app.loader import DATA_DIR, load_setup, read_json
from core.battle import Battle
from core.formulas import damage

BOT_RUNS = (("perfect", "full", "완벽 대처"), ("party", "none", "단순"), ("basic", "none", "평타"))


def sword_hits(data, solo, enemy) -> float:
    """솔로 설정의 소드 스킬 평균 피해로 적을 잡는 데 필요한 횟수."""
    b = Battle(data, solo, enemy, 1, 0)
    p, e = b.allies[0], b.enemies[0]
    dmgs = [sum(damage(p, s, h, e, 10000) for h in s.hits) for s in p.sword_skills if s.family == p.weapon_family]
    return enemy.hp / (sum(dmgs) / len(dmgs)) if dmgs else 0.0


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="적 등급 평가")
    ap.add_argument("--n", type=int, default=100, help="조건마다 판 수")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--data", type=Path, default=DATA_DIR)
    args = ap.parse_args()

    data, party = load_setup(args.data)
    _, solo = load_setup(args.data, solo=True)
    tiers = {k: v for k, v in read_json(args.data / "tiers.json").items() if not k.startswith("_")}

    for tier, crit in tiers.items():
        enemies = [e for e in data.enemies.values() if e.tier == tier]
        if not enemies:
            print(f"\n■ {crit['name']}: 해당 등급의 적 없음")
            continue
        for enemy in enemies:
            lo, hi = crit["sword_hits"]
            hits = sword_hits(data, solo, enemy)
            mark = "" if lo - 0.5 <= hits <= hi + 0.5 else "  ← 기준 밖"
            print(f"\n■ {crit['name']} — {enemy.name} (체력 {enemy.hp})")
            print(f"  소드 스킬 약 {hits:.1f}방 (기준 {lo}{'' if lo == hi else f'~{hi}'}방){mark}")
            for mode, specs, label in (("solo", solo, "1대1"), ("party", party, "3대1")):
                print(f"  [{label}] 기준: {crit[mode]}")
                max_hp = sum(s.max_hp for s in specs)
                for bot, knowledge, bot_label in BOT_RUNS:
                    wins = deaths = 0
                    chip = fail = 0
                    secs = 0.0
                    for i in range(args.n):
                        res, log = run_one(data, specs, enemy.id, 1, args.seed + i, bot, knowledge)
                        wins += res.winner == "party"
                        deaths += sum(1 for h in res.ally_hp if h <= 0)
                        secs += res.end_tick / data.rules.ticks_per_sec
                        for e in log:
                            if e.event == "hit" and e.info.startswith("ally"):
                                if e.info.endswith(":chip"):
                                    chip += e.value
                                else:
                                    fail += e.value
                            elif e.event == "friendly_fire":
                                fail += e.value
                    n = args.n
                    total = max_hp * n
                    print(f"    {bot_label:<6} 승률 {wins / n:6.1%}  대응 실패 피해 {fail / total:6.1%}  가드 칩 {chip / total:5.1%}"
                          f"  판당 사망 {deaths / n:4.2f}명  평균 {secs / n:5.1f}초")


if __name__ == "__main__":
    main()
