"""봇으로 전투를 여러 번 돌려 CSV 로그를 남긴다.

    python -m app.batch --n 200 --enemy dire_wolf --count 2 --knowledge full
    python -m app.batch --solo --n 200          # loadout.json 한 명
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

from app.bots import BOTS
from app.loader import DATA_DIR, LOG_DIR, load_setup, read_json
from core.battle import Battle
from core.defs import GameData
from core.loadout import PlayerSpec
from core.log import BattleResult, LogEvent

# 한 판이 이 횟수 이상 결정을 요구하면 봇이나 엔진이 멈춘 것으로 본다
MAX_DECISIONS = 50000


def run_one(data: GameData, party: tuple[PlayerSpec, ...], enemy_id: str, count: int, seed: int, bot_name: str,
            knowledge: str) -> tuple[BattleResult, list[LogEvent]]:
    b = Battle(data, party, data.enemies[enemy_id], count, seed, knowledge)
    bot = BOTS[bot_name](seed)
    decisions = 0
    while (req := b.advance()) is not None:
        b.submit(bot.choose(b, req))
        decisions += 1
        if decisions > MAX_DECISIONS:
            raise RuntimeError(f"seed {seed}: 결정 {MAX_DECISIONS}회 초과 (tick {b.now})")
    assert b.result is not None
    return b.result, b.log


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="전투 배치 시뮬레이션")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--seed", type=int, default=1, help="첫 시드 (이후 +1씩)")
    ap.add_argument("--enemy", default="dire_wolf")
    ap.add_argument("--count", type=int, default=1, help="적 수")
    ap.add_argument("--solo", action="store_true", help="party.json 대신 loadout.json 한 명")
    ap.add_argument("--bot", default="party", choices=sorted(BOTS))
    ap.add_argument("--knowledge", default="none", choices=["none", "full"])
    ap.add_argument("--data", type=Path, default=DATA_DIR)
    ap.add_argument("--party", type=Path, default=None, help="파티(또는 --solo면 loadout) 파일")
    ap.add_argument("--out", type=Path, default=None, help="출력 폴더 (기본: logs/<시각>_<적>x<수>_<봇>_<정보>)")
    args = ap.parse_args()

    data, party = load_setup(args.data, args.party, args.solo)
    if args.enemy not in data.enemies:
        raise SystemExit(f"적 '{args.enemy}'가 없습니다. 가능: {sorted(data.enemies)}")
    out: Path = args.out or LOG_DIR / f"{time.strftime('%Y%m%d_%H%M%S')}_{args.enemy}x{args.count}_{args.bot}_{args.knowledge}"
    out.mkdir(parents=True, exist_ok=True)
    tpf = data.rules.ticks_per_frame

    wins = 0
    total_frames = 0
    with (out / "battles.csv").open("w", newline="", encoding="utf-8") as fb, \
         (out / "events.csv").open("w", newline="", encoding="utf-8") as fe:
        wb, we = csv.writer(fb), csv.writer(fe)
        wb.writerow(["battle", "seed", "winner", "end_tick", "end_frame", "allies", "allies_alive", "ally_hp", "ally_max_hp",
                     "enemies", "enemy_hp"])
        we.writerow(["battle", "seed", "tick", "frame", "actor", "event", "ref", "value", "info"])
        for i in range(args.n):
            seed = args.seed + i
            res, events = run_one(data, party, args.enemy, args.count, seed, args.bot, args.knowledge)
            wb.writerow([i, seed, res.winner, res.end_tick, res.end_tick // tpf, len(party), sum(1 for h in res.ally_hp if h > 0),
                         sum(res.ally_hp), sum(p.max_hp for p in party), args.count, sum(res.enemy_hp)])
            for e in events:
                we.writerow([i, seed, e.tick, e.tick // tpf, e.actor, e.event, e.ref, e.value, e.info])
            wins += res.winner == "party"
            total_frames += res.end_tick // tpf

    meta = {"args": {k: str(v) for k, v in vars(args).items()},
            "party": read_json(args.party or args.data / ("loadout.json" if args.solo else "party.json"))}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{args.n}판 완료 → {out}")
    print(f"  승률 {wins / args.n:.1%}, 평균 전투 시간 {total_frames / args.n / data.rules.fps:.1f}초")
    print(f"  분석: python -m analysis.summarize \"{out}\"")


if __name__ == "__main__":
    main()
