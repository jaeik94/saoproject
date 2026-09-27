"""전투 공식. 모두 정수 연산 (만분율, 틱)."""
from __future__ import annotations

from core.combatant import Combatant
from core.defs import AttackDef, GameData, HitDef, Rules
from core.hexgrid import Hex, dist

BP = 10000


def clamp(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else hi if v > hi else v


def speed_scaled(ticks: int, speed_bp: int) -> int:
    return ticks * (BP - speed_bp) // BP


def attack_timing(c: Combatant, a: AttackDef) -> tuple[int, int, int]:
    """(발생, 지속, 후딜) 틱. 무기 속도는 아군의 무기 행동(소드 스킬, 일반 공격)의 발생·후딜에만 적용."""
    if c.is_ally and c.weapon and a.kind in ("sword_skill", "basic"):
        sp = c.weapon.weapon.speed_bp
        return speed_scaled(a.startup_t, sp), a.active_t, speed_scaled(a.recovery_t, sp)
    return a.startup_t, a.active_t, a.recovery_t


def first_hit_offset(c: Combatant, a: AttackDef) -> int:
    startup, _, _ = attack_timing(c, a)
    return startup + a.hits[0].at_t


def attr_mod(rules: Rules, attributes: tuple[str, ...], stance: str) -> int:
    total = 0
    for attr in attributes:
        total += rules.attribute(attr).mod_for(stance)
    return total


def stance_bonus(data: GameData, c: Combatant, stance: str) -> int:
    """속성·적응을 제외한 태세 성공률 (기본 + 스킬 + 민첩 + 방패, 적은 기본 + 적 보너스)."""
    rule = data.rules.stance(stance)
    bp = rule.base_bp + rule.agi_bonus_bp * c.agi
    if c.enemy_def is not None:
        return bp + c.enemy_def.stance_bonus_bp
    for s in c.slot_skills:
        bp += data.slot_skills[s].bonus_for(stance)
    if stance == "guard" and c.shield is not None:
        bp += c.shield.guard_bonus_bp
    return bp


def stance_chance(data: GameData, c: Combatant, stance: str, attributes: tuple[str, ...], extra_bp: int = 0) -> int:
    r = data.rules
    return clamp(stance_bonus(data, c, stance) + attr_mod(r, attributes, stance) + extra_bp, r.min_success_bp, r.max_success_bp)


def stance_chance_range(data: GameData, c: Combatant, stance: str, attribute_sets: list[tuple[str, ...]]) -> tuple[int, int]:
    lo, hi = BP, 0
    for attrs in attribute_sets:
        v = stance_chance(data, c, stance, attrs)
        lo, hi = min(lo, v), max(hi, v)
    return lo, hi


def raw_hit_power(c: Combatant, a: AttackDef, hit: HitDef) -> int:
    base = a.fixed_power if a.fixed_power > 0 else c.power
    return base * hit.power_bp // BP


def damage(c: Combatant, a: AttackDef, hit: HitDef, target: Combatant, dmg_bp: int) -> int:
    return max(1, raw_hit_power(c, a, hit) * dmg_bp // BP - target.defense)


def clash_power(rules: Rules, c: Combatant, a: AttackDef, hit: HitDef) -> int:
    bonus = c.str_ * rules.clash_str_bonus_bp + c.clash_bonus_bp
    return raw_hit_power(c, a, hit) * (BP + bonus) // BP


def hitstun(rules: Rules, target: Combatant, base_t: int, combo: int) -> int:
    size = rules.size(target.size)
    decay = size.decay[combo] if combo < len(size.decay) else size.decay[-1]
    return base_t * size.hitstun_bp // BP * decay // BP


def move_cell_ticks(rules: Rules, c: Combatant) -> int:
    """한 칸 이동 시간."""
    if c.enemy_def is not None:
        return c.enemy_def.move_t
    reduction = min(rules.move_max_reduction_bp, c.agi * rules.move_agi_reduction_bp)
    t = rules.move_cell_t * (BP - reduction) // BP
    if c.total_weight > rules.weight_limit:
        t = t * (BP + rules.overweight_penalty_bp) // BP
    return t


def turn_ticks(rules: Rules, c: Combatant) -> int:
    assert c.enemy_def is not None
    return max(0, rules.turn_t * (BP - c.enemy_def.turn_speed_bp) // BP)


def cells_distance(a: list[Hex], b: list[Hex]) -> int:
    best = -1
    for x in a:
        for y in b:
            d = dist(x, y)
            if best < 0 or d < best:
                best = d
    return best


def unit_distance(a: Combatant, b: Combatant) -> int:
    return cells_distance(a.cells(), b.cells())


def in_range(a: AttackDef, d: int) -> bool:
    return a.range_min <= d <= a.range_max
