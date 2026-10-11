"""어그로 이동 측정: 딜러에게 어그로가 얼마나 넘어가고 얼마나 붙어 있는지, 그 직전에 '틈이 아닐 때' 소드 스킬이 있었는지.
틈이 아닐 때 소드 스킬의 헤이트 배율(rules.hate.open_skill_bp)을 바꿔 가며 비교할 수 있다.

    python -m app.aggro --n 100
    python -m app.aggro --enemies dire_wolf_elite --bots orig,backspam --hate 10000,30000

딜러 = 방패가 없는 파티원. 틈 여부는 로그의 명중 사건에 남은 맞은 쪽 상태로 판정한다
(맞아서 굳음·무력화·넘어짐·태세·후딜·이동 = 틈).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.bots import BOTS
from app.hunt import specs_without_potions
from app.loader import DATA_DIR, read_json, read_raw
from core import log as L
from core.battle import Battle
from core.combatant import NO_TARGET
from core.defs import parse_game_data

WINDOW_STATES = ("hitstun", "broken", "knockdown", "stance", "recovery", "move")
CAUSE_WINDOW_FRAMES = 60


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="어그로 이동 측정 (3대1)")
    ap.add_argument("--enemies", default="dire_wolf_elite,frenzy_boar_elite,grass_beetle_elite")
    ap.add_argument("--bots", default="orig,backspam")
    ap.add_argument("--hate", default="10000,30000", help="틈이 아닐 때 소드 스킬 헤이트 배율 목록 (만분율)")
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--data", type=Path, default=DATA_DIR)
    args = ap.parse_args()

    members = read_json(args.data / "party.json")["members"]
    print(f"■ 어그로 이동 — 3대1, 조건마다 {args.n}판, 정보 있음")
    print(f"  딜러로 넘어감/판 (직전 {CAUSE_WINDOW_FRAMES}f 안에 그 딜러의 '틈 아닐 때' 소드 스킬이 있었던 경우)"
          " | 딜러 소드 스킬 중 틈 아닐 때 맞힌 비율 | 딜러가 노려진 시간")
    for eid in [x.strip() for x in args.enemies.split(",") if x.strip()]:
        for bot in [x.strip() for x in args.bots.split(",") if x.strip()]:
            for mult in [int(x) for x in args.hate.split(",") if x.strip()]:
                raw = read_raw(args.data)
                raw["rules"]["hate"]["open_skill_bp"] = mult
                data = parse_game_data(raw)
                party = specs_without_potions(data, members)
                dealers = {f"ally{i}" for i, p in enumerate(party) if p.shield is None}
                tpf = data.rules.ticks_per_frame
                to_dealer = caused = open_hits = hits = 0
                dealer_t = total_t = 0
                for s in range(1, args.n + 1):
                    b = Battle(data, party, (data.enemies[eid],), s, "full")
                    bot_i = BOTS[bot](s)
                    last, who = 0, b.enemies[0].target
                    while (req := b.advance()) is not None:
                        if who != NO_TARGET and b.fighters[who].cid in dealers:
                            dealer_t += b.now - last
                        last, who = b.now, b.enemies[0].target
                        b.submit(bot_i.choose(b, req))
                    if who != NO_TARGET and b.fighters[who].cid in dealers:
                        dealer_t += b.now - last
                    total_t += b.now
                    skill_hits: list[tuple[int, str, bool]] = []
                    for ev in b.log:
                        a = data.actions.get(ev.ref)
                        if ev.event == L.HIT and ev.actor in dealers and a is not None and a.kind == "sword_skill" \
                                and ev.info.startswith("enemy"):
                            state = ev.info.split(":")[1]
                            is_open = state not in WINDOW_STATES
                            skill_hits.append((ev.tick, ev.actor, is_open))
                            hits += 1
                            open_hits += is_open
                        elif ev.event == L.RETARGET and ev.ref in dealers:
                            to_dealer += 1
                            if any(t <= ev.tick and ev.tick - t <= CAUSE_WINDOW_FRAMES * tpf and who_ == ev.ref and op
                                   for t, who_, op in skill_hits):
                                caused += 1
                n = args.n
                print(f"  {data.enemies[eid].name:<10} {bot:<9} ×{mult / 10000:g}: 딜러로 {to_dealer / n:4.2f} ({caused / n:4.2f})"
                      f" | 틈 아닐 때 {open_hits / max(1, hits):4.0%} | 딜러 노려짐 {dealer_t / max(1, total_t):4.0%}", flush=True)


if __name__ == "__main__":
    main()
