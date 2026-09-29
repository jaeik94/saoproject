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


def wear_limit(rules: Rules, c: Combatant) -> int:
    """착용 무게 한도 (0.1 단위) = 6 + 근력 × 0.6."""
    return rules.wear.base_limit + rules.wear.per_str * c.str_


def wear_multiplier_bp(rules: Rules, c: Combatant) -> int:
    """착용 무게 초과 배수 = 1 + linear·r + quad·r² (r = 초과 비율). 한도 이하면 1."""
    if not c.is_ally:
        return BP
    limit = wear_limit(rules, c)
    over = c.wear_weight - limit
    if over <= 0 or limit <= 0:
        return BP
    r = over * BP // limit
    return BP + rules.wear.linear * r + rules.wear.quad * r * r // BP


def attack_timing(rules: Rules, c: Combatant, a: AttackDef) -> tuple[int, int, int]:
    """(발생, 지속, 후딜) 틱. 무기 속도는 아군 무기 행동(소드 스킬, 일반 공격)의 발생·후딜에,
    착용 무게 초과 배수는 아군의 모든 공격 프레임에 적용."""
    startup, active, recovery = a.startup_t, a.active_t, a.recovery_t
    if c.is_ally and c.weapon and a.kind in ("sword_skill", "basic"):
        sp = c.weapon.weapon.speed_bp
        startup, recovery = speed_scaled(startup, sp), speed_scaled(recovery, sp)
    m = wear_multiplier_bp(rules, c)
    if m != BP:
        startup, active, recovery = startup * m // BP, active * m // BP, recovery * m // BP
    return startup, active, recovery


def hit_offsets(rules: Rules, c: Combatant, a: AttackDef) -> list[int]:
    """행동 시작부터 각 타격까지의 틱."""
    startup, _, _ = attack_timing(rules, c, a)
    m = wear_multiplier_bp(rules, c)
    return [startup + h.at_t * m // BP for h in a.hits]


def first_hit_offset(rules: Rules, c: Combatant, a: AttackDef) -> int:
    return hit_offsets(rules, c, a)[0]


def stance_bonus(data: GameData, c: Combatant, stance: str) -> int:
    """적응을 제외한 태세 성공률. 기본 + 근력(가드)·민첩(회피) + 스킬 + 방패, 적은 기본 + 적 보너스."""
    rule = data.rules.stance(stance)
    bp = rule.base_bp + rule.str_bonus_bp * c.str_ + rule.agi_bonus_bp * c.agi
    if c.enemy_def is not None:
        return bp + c.enemy_def.stance_bonus_bp
    for s in c.slot_skills:
        bp += data.slot_skills[s].bonus_for(stance)
    if stance == "guard" and c.shield is not None:
        bp += c.shield.guard_bonus_bp
    return bp


def stance_chance(data: GameData, c: Combatant, stance: str, extra_bp: int = 0) -> int:
    """공격 태그와 태세의 상성 규칙은 미정이라 아직 반영하지 않는다."""
    r = data.rules
    return clamp(stance_bonus(data, c, stance) + extra_bp, r.min_success_bp, r.max_success_bp)


def raw_hit_power(c: Combatant, a: AttackDef, hit: HitDef) -> int:
    base = a.fixed_power if a.fixed_power > 0 else c.power
    return base * hit.power_bp // BP


def damage(c: Combatant, a: AttackDef, hit: HitDef, target: Combatant, dmg_bp: int, ignore_defense: bool = False) -> int:
    return max(1, raw_hit_power(c, a, hit) * dmg_bp // BP - (0 if ignore_defense else target.defense))


def clash_power(rules: Rules, c: Combatant, a: AttackDef, hit: HitDef) -> int:
    """패리 위력: 타격 위력 × 근력 보정 × 중량 보정."""
    bonus = c.str_ * rules.parry.str_bonus_bp + c.clash_bonus_bp
    power = raw_hit_power(c, a, hit) * (BP + bonus) // BP
    return power * rules.heavy.parry_power_bp // BP if a.heavy else power


def hitstun(rules: Rules, target: Combatant, base_t: int, combo: int) -> int:
    size = rules.size(target.size)
    decay = size.decay[combo] if combo < len(size.decay) else size.decay[-1]
    return base_t * size.hitstun_bp // BP * decay // BP


def getup_ticks(rules: Rules, c: Combatant) -> int:
    """넘어졌을 때 기상 시간. 아군은 착용 무게가 무거울수록 느리다."""
    if c.enemy_def is not None:
        return c.enemy_def.getup_t
    return rules.knockdown.getup_t * (BP + c.wear_weight * rules.knockdown.wear_bp) // BP


def move_cell_ticks(rules: Rules, c: Combatant) -> int:
    """한 칸 이동 시간."""
    if c.enemy_def is not None:
        return c.enemy_def.move_t
    reduction = min(rules.move_max_reduction_bp, c.agi * rules.move_agi_reduction_bp)
    t = rules.move_cell_t * (BP - reduction) // BP
    if c.carry_weight > rules.carry_limit:
        t = t * (BP + rules.carry_over_penalty_bp) // BP
    return t * wear_multiplier_bp(rules, c) // BP


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
