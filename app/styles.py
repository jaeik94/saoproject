"""플레이 방식 비교: 같은 규칙에서 어느 싸움법이 유리한지 연속 사냥으로 비교한다. 수치를 맞추지는 않는다.

    python -m app.styles --which elite --n 20 --kills 10
    python -m app.styles --which normal
    python -m app.styles --which dire_wolf_elite,grass_beetle_elite --styles orig,perfect

칸: 1마리당 초 (전투+휴식) / 사망으로 끝난 사냥 비율 / 1마리당 받은 피해(파티 최대 체력 합 대비).
"탱커 없는 원작형"은 파티 첫 멤버의 방패를 빼고 무기 방어 스킬로 바꾼 구성이다.
"""
from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

from app.bots import BOTS
from app.hunt import run_hunt, specs_without_potions
from app.loader import DATA_DIR, load_game_data, read_json

LABELS = {"orig": "원작형 교대", "orig_notank": "탱커 없는 원작형", "backspam": "뒤에서 쿨마다", "melee": "난전형",
          "spam": "평타 연타형", "perfect": "각자 솔로형"}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="플레이 방식 비교 (연속 사냥)")
    ap.add_argument("--which", default="elite", help="elite | normal | 적 id 쉼표 목록")
    ap.add_argument("--styles", default="orig,orig_notank,melee,spam,perfect", help=f"가능: {', '.join(LABELS)}")
    ap.add_argument("--n", type=int, default=20, help="사냥 횟수")
    ap.add_argument("--kills", type=int, default=10, help="한 사냥의 전투 수")
    ap.add_argument("--rest", type=int, default=7000, help="휴식 기준 (만분율)")
    ap.add_argument("--data", type=Path, default=DATA_DIR)
    args = ap.parse_args()

    data = load_game_data(args.data)
    tps = data.rules.ticks_per_sec
    members = read_json(args.data / "party.json")["members"]
    no_tank = copy.deepcopy(members)
    no_tank[0].update(name=f"{members[0]['name']}(방패 없음)", skill_slots=[members[0]["skill_slots"][0], "weapon_defense"], shield=None)
    party = specs_without_potions(data, members)
    dealers = specs_without_potions(data, no_tank)

    if args.which in ("elite", "normal"):
        ids = [k for k in data.enemies if k.endswith("_elite") == (args.which == "elite")]
    else:
        ids = [x.strip() for x in args.which.split(",") if x.strip()]
    for eid in ids:
        if eid not in data.enemies:
            raise SystemExit(f"적 '{eid}'가 없습니다. 가능: {sorted(data.enemies)}")
    styles = [x.strip() for x in args.styles.split(",") if x.strip()]
    for st in styles:
        if st not in LABELS:
            raise SystemExit(f"방식 '{st}'가 없습니다. 가능: {', '.join(LABELS)}")

    print(f"■ 플레이 방식 비교 — 3대1 연속 사냥 {args.kills}전 × {args.n}회, 휴식 기준 {args.rest / 100:.0f}%, 포션 없음")
    print("  칸: 1마리당 초 (전투+휴식) / 사망한 사냥 / 1마리당 받은 피해(최대 체력 합 대비)")
    print(f"  {'방식':<12}" + "".join(f"{data.enemies[i].name:<26}" for i in ids))
    for st in styles:
        specs, bot = (dealers, "orig") if st == "orig_notank" else (party, st)
        assert bot in BOTS
        cells = []
        for eid in ids:
            res = [run_hunt(data, specs, (data.enemies[eid],), args.kills, args.rest, 1 + i, [bot]) for i in range(args.n)]
            kills = sum(r.kills for r in res) or 1
            fight = sum(r.fight_t for r in res) / tps / kills
            total = fight + sum(r.rest_t for r in res) / tps / kills
            died = sum(r.died for r in res) / args.n
            dmg = sum(sum(r.damage) for r in res) / kills / sum(p.max_hp for p in specs)
            cells.append(f"{total:5.1f} ({fight:4.1f}+{total - fight:4.1f}) {died:3.0%} {dmg:4.0%}")
        print(f"  {LABELS[st]:<12}" + "".join(f"{c:<26}" for c in cells), flush=True)


if __name__ == "__main__":
    main()
