"""프레임표와 반격 성립 여부를 자동으로 계산해 보여 준다 (수치 조정용).

    python -m app.frames --enemy dire_wolf
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from app.loader import DATA_DIR, LOG_DIR, load_setup
from core.battle import Battle
from core.defs import STANCE_ORDER, AttackDef
from core.formulas import BP, attack_timing, first_hit_offset, hitstun, stance_chance


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="프레임표")
    ap.add_argument("--enemy", default="dire_wolf")
    ap.add_argument("--data", type=Path, default=DATA_DIR)
    ap.add_argument("--loadout", type=Path, default=None, help="기본: loadout.json (솔로 설정)")
    args = ap.parse_args()

    data, party = load_setup(args.data, args.loadout, solo=True)
    b = Battle(data, party, data.enemies[args.enemy], 1, 0)
    p, e, r = b.allies[0], b.enemies[0], data.rules
    tpf = r.ticks_per_frame
    assert e.enemy_def is not None
    reaction = e.enemy_def.reaction_t

    def f(t: int) -> str:
        return f"{t // tpf}" if t % tpf == 0 else f"{t / tpf:.1f}"

    mine: list[AttackDef] = [s for s in p.sword_skills if s.family == p.weapon_family]
    mine += [data.actions["basic_attack"], data.actions["throw"]]
    rows: list[list[str]] = []

    print(f"■ 플레이어 행동 (무기: {p.weapon.weapon.name if p.weapon else '없음'}) vs {e.name} [{e.size}]")
    print(f"  {'행동':<10}{'발생':>6}{'지속':>6}{'후딜':>6}{'전체':>6}{'첫타':>6}{'히트이득':>9}{'+적반응':>8}")
    for a in mine:
        s, act, rec = attack_timing(p, a)
        total = s + act + rec
        last = a.hits[-1]
        after = total - (s + last.at_t)
        adv = hitstun(r, e, last.hitstun_t, 0) - after
        print(f"  {a.name:<10}{f(s):>6}{f(act):>6}{f(rec):>6}{f(total):>6}{f(first_hit_offset(p, a)):>6}{f(adv) if adv >= 0 else '-' + f(-adv):>9}{f(adv + reaction):>8}")
        rows.append(["player", a.id, f(s), f(act), f(rec), f(total), f(first_hit_offset(p, a)), f(adv), "", "", ""])

    print(f"\n■ {e.name} 공격 — 방어 성공 후 반격 창 (적 후딜 종료까지, 적 반응 {f(reaction)}f 별도)")
    for a in e.enemy_def.attacks if e.enemy_def else ():
        s, act, rec = attack_timing(e, a)
        total = s + act + rec
        last = a.hits[-1]
        after = total - (s + last.at_t)
        attrs = ",".join(r.attribute(x).name for x in a.attributes) or "-"
        print(f"\n  {a.name} [{attrs}] 발생 {f(s)} 지속 {f(act)} 후딜 {f(rec)} / 첫타 {f(s + a.hits[0].at_t)} / {len(a.hits)}타"
              f"{' / 장소 지정' if a.targeting == 'place' else ''}")
        e_first = s + a.hits[0].at_t
        cut = [x.name for x in mine if x.interrupts and x.range_min == 1 and first_hit_offset(p, x) < e_first]
        print(f"    프리모션 보고 바로 끊기 가능: {', '.join(cut) or '없음'}")
        for kind in STANCE_ORDER:
            rule = r.stance(kind)
            if rule.requires_weapon and p.weapon is None:
                continue
            chance = stance_chance(data, p, kind, a.attributes)
            adv = rule.advantage_t
            if rule.weight_bonus and p.weapon:
                adv += p.weapon.weapon.weight * r.weight_stun_ticks
            dstun = last.hitstun_t * rule.defender_stun_bp // BP
            gap = after + adv - dstun
            ok = [x.name for x in mine if x.range_min == 1 and first_hit_offset(p, x) < gap]
            print(f"    {rule.name:<4} 성공률 {chance / 100:>3.0f}%  반격 창 {f(gap):>5}f  → {', '.join(ok) or '없음'}")
            rows.append(["enemy", a.id, f(s), f(act), f(rec), f(total), f(s + a.hits[0].at_t), "", kind, f"{chance}", f(gap)])

    out = LOG_DIR / f"frames_{args.enemy}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["side", "action", "startup", "active", "recovery", "total", "first_hit", "hit_adv", "stance", "chance_bp", "punish_gap"])
        w.writerows(rows)
    print(f"\n(CSV: {out})")


if __name__ == "__main__":
    main()
