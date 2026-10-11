"""연속 사냥 평가: 같은 구성으로 여러 마리를 연달아 잡는다. 체력은 전투 사이에 이어지고, 포션은 쓰지 않는다.

    python -m app.hunt --enemy dire_wolf_elite --kills 10 --n 50 --bot orig
    python -m app.hunt --enemy dire_wolf --kills 10 --n 50 --bot perfect --solo

전투 사이: 체력이 기준(--rest, 만분율) 아래인 사람이 있으면 모두 최대 체력까지 쉰다.
휴식 시간은 rules.rest의 회복 속도(interval마다 regen)로 계산한다. 누군가 쓰러지면 그 사냥은 거기서 끝난다.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from app.bots import BOTS
from app.loader import DATA_DIR, load_game_data, parse_enemies, read_json
from core.battle import Battle
from core.defs import EnemyDef, GameData
from core.loadout import PlayerSpec, build_player

MAX_DECISIONS = 50000


@dataclass(slots=True)
class HuntResult:
    kills: int
    died: bool
    fight_t: int
    rest_t: int
    damage: list[int]              # 아군별 받은 피해 합


def specs_without_potions(data: GameData, members: list[dict]) -> tuple[PlayerSpec, ...]:
    out = []
    for m in members:
        m = dict(m, pouch=[], inventory=[])
        out.append(build_player(data, m))
    return tuple(out)


def run_hunt(data: GameData, party: tuple[PlayerSpec, ...], enemies: tuple[EnemyDef, ...], kills: int,
             rest_bp: int, seed: int, bot_names: list[str], knowledge: str = "full") -> HuntResult:
    r = data.rules
    hp = [p.max_hp for p in party]
    res = HuntResult(0, False, 0, 0, [0] * len(party))
    for k in range(kills):
        if any(h * 10000 < p.max_hp * rest_bp for h, p in zip(hp, party)):
            need = max(-(-(p.max_hp - h) // r.rest_regen) for h, p in zip(hp, party))
            res.rest_t += need * r.rest_interval_t
            hp = [p.max_hp for p in party]
        fight_seed = seed * 1000 + k
        b = Battle(data, party, enemies, fight_seed, knowledge, tuple(hp))
        bots = [BOTS[bot_names[i % len(bot_names)]](fight_seed) for i in range(len(party))]
        n = 0
        while (req := b.advance()) is not None:
            b.submit(bots[req.actor].choose(b, req))
            n += 1
            if n > MAX_DECISIONS:
                raise RuntimeError(f"seed {fight_seed}: 결정 {MAX_DECISIONS}회 초과")
        assert b.result is not None
        res.fight_t += b.result.end_tick
        for i, (before, after) in enumerate(zip(hp, b.result.ally_hp)):
            res.damage[i] += max(0, before - after)
        hp = list(b.result.ally_hp)
        if b.result.winner != "party" or any(h <= 0 for h in hp):
            res.died = any(h <= 0 for h in hp)
            if b.result.winner == "party":
                res.kills += 1
            break
        res.kills += 1
    return res


def summarize(data: GameData, party: tuple[PlayerSpec, ...], results: list[HuntResult]) -> list[str]:
    tps = data.rules.ticks_per_sec
    kills = sum(x.kills for x in results)
    fight = sum(x.fight_t for x in results) / tps
    rest = sum(x.rest_t for x in results) / tps
    lines = [f"  1마리당 {(fight + rest) / max(1, kills):5.1f}초 (전투 {fight / max(1, kills):4.1f} + 휴식 {rest / max(1, kills):4.1f}), "
             f"사망으로 끝난 사냥 {sum(x.died for x in results) / len(results):4.0%}, 평균 처치 {kills / len(results):4.1f}"]
    for i, p in enumerate(party):
        dmg = sum(x.damage[i] for x in results)
        lines.append(f"    {p.name:<6} 1마리당 받은 피해 {dmg / max(1, kills):5.1f} (최대 체력 대비 {dmg / max(1, kills) / p.max_hp:4.0%})")
    return lines


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="연속 사냥 평가")
    ap.add_argument("--enemy", default="dire_wolf_elite", help='한 전투의 적 구성. 예: "dire_wolf:2"')
    ap.add_argument("--kills", type=int, default=10, help="한 사냥에서 잡을 전투 수")
    ap.add_argument("--n", type=int, default=50, help="사냥 횟수")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--rest", type=int, default=7000, help="이 체력 비율(만분율) 아래인 사람이 있으면 쉰다 [임시]")
    ap.add_argument("--bot", default="orig", help="봇 이름, 쉼표로 멤버별 지정 가능 (예: perfect,party,party)")
    ap.add_argument("--solo", action="store_true", help="party.json 대신 loadout.json 한 명")
    ap.add_argument("--data", type=Path, default=DATA_DIR)
    ap.add_argument("--party", type=Path, default=None)
    args = ap.parse_args()

    data = load_game_data(args.data)
    if args.solo:
        members = [read_json(args.party or args.data / "loadout.json")]
    else:
        members = read_json(args.party or args.data / "party.json")["members"]
    party = specs_without_potions(data, members)
    enemies = parse_enemies(data, args.enemy)
    bots = args.bot.split(",")
    for name in bots:
        if name not in BOTS:
            raise SystemExit(f"봇 '{name}'가 없습니다. 가능: {sorted(BOTS)}")
    results = [run_hunt(data, party, enemies, args.kills, args.rest, args.seed + i, bots) for i in range(args.n)]
    print(f"■ 연속 사냥 — 적 {args.enemy}, 사냥마다 {args.kills}전, {args.n}회, 봇 {args.bot}, 휴식 기준 {args.rest / 100:.0f}%, 포션 없음")
    for line in summarize(data, party, results):
        print(line)


if __name__ == "__main__":
    main()
