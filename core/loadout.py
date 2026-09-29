"""플레이어 구성(loadout) 검증과 전투용 사양 생성."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.defs import ArmorDef, AttackDef, GameData, WeaponDef


class LoadoutError(Exception):
    pass


@dataclass(slots=True)
class ItemStack:
    item_id: str
    count: int


@dataclass(slots=True, frozen=True)
class PlayerSpec:
    name: str
    level: int
    max_hp: int
    str_: int
    agi: int
    slot_skills: tuple[str, ...]
    sword_skills: tuple[AttackDef, ...]
    weapon: WeaponDef | None
    spare_weapons: tuple[WeaponDef, ...]
    armor: ArmorDef | None
    shield: ArmorDef | None
    quick_change: bool
    pouch_capacity: int
    pouch: tuple[tuple[str, int], ...]
    inventory: tuple[tuple[str, int], ...]
    carry_weight: int         # 인벤토리 소지 무게 (착용 무게는 전투 중 장비로 계산)


def build_player(data: GameData, raw: dict[str, Any]) -> PlayerSpec:
    """문제를 모두 모아 한 번에 알려 준다."""
    rules = data.rules
    errors: list[str] = []
    known = {"name", "level", "stats", "skill_slots", "proficiency", "sword_skills", "weapon", "spare_weapons",
             "armor", "shield", "quick_change", "pouch_capacity", "pouch", "inventory"}
    for k in raw:
        if not k.startswith("_") and k not in known:
            errors.append(f"알 수 없는 키 '{k}'")

    level = int(raw.get("level", 1))
    stats = raw.get("stats", {})
    str_, agi = int(stats.get("str", 0)), int(stats.get("agi", 0))
    if str_ < 0 or agi < 0:
        errors.append("스탯은 0 이상이어야 합니다")
    if level == 1 and str_ + agi != rules.level1_stat_points:
        errors.append(f"Lv1 스탯 합은 {rules.level1_stat_points}이어야 합니다 (현재 근력 {str_} + 민첩 {agi} = {str_ + agi})")

    slots = tuple(raw.get("skill_slots", []))
    max_slots = rules.slots_for_level(level)
    if len(slots) > max_slots:
        errors.append(f"슬롯 스킬 {len(slots)}개: Lv{level}은 최대 {max_slots}개")
    if len(set(slots)) != len(slots):
        errors.append("슬롯 스킬이 중복되었습니다")
    for s in slots:
        if s not in data.slot_skills:
            errors.append(f"슬롯 스킬 '{s}'가 slot_skills.json에 없습니다")
    slot_families = {data.slot_skills[s].family for s in slots if s in data.slot_skills and data.slot_skills[s].kind == "weapon"}

    proficiency: dict[str, int] = {k: int(v) for k, v in raw.get("proficiency", {}).items()}

    def weapon(wid: str, where: str) -> WeaponDef | None:
        if wid not in data.weapons:
            errors.append(f"{where} '{wid}'가 weapons.json에 없습니다")
            return None
        return data.weapons[wid]

    w_id = raw.get("weapon")
    main = weapon(w_id, "무기") if w_id else None
    spares = tuple(w for w in (weapon(x, "예비 무기") for x in raw.get("spare_weapons", [])) if w is not None)
    for w in ([main] if main else []) + list(spares):
        if str_ < w.min_str:
            errors.append(f"'{w.name}'은 근력 {w.min_str} 이상이어야 장비할 수 있습니다 (현재 {str_})")

    sword_skills: list[AttackDef] = []
    for sid in raw.get("sword_skills", []):
        a = data.actions.get(sid)
        if a is None or a.kind != "sword_skill":
            errors.append(f"소드 스킬 '{sid}'가 skills.json에 없거나 sword_skill이 아닙니다")
            continue
        slot_id = next((s for s in slots if s in data.slot_skills and data.slot_skills[s].family == a.family), None)
        if a.family not in slot_families or slot_id is None:
            errors.append(f"'{a.name}'은 {a.family} 계열 슬롯 스킬이 필요합니다")
            continue
        prof = proficiency.get(slot_id, 0)
        if prof < a.required_proficiency:
            errors.append(f"'{a.name}'은 숙련도 {a.required_proficiency} 필요 (현재 {prof})")
            continue
        sword_skills.append(a)

    def armor(aid: str | None, slot: str) -> ArmorDef | None:
        if not aid:
            return None
        a = data.armors.get(aid)
        if a is None:
            errors.append(f"방어구 '{aid}'가 armors.json에 없습니다")
            return None
        if a.slot != slot:
            errors.append(f"'{a.name}'은 {slot} 슬롯 장비가 아닙니다")
        if a.requires and a.requires not in slots:
            errors.append(f"'{a.name}' 착용에는 슬롯 스킬 '{a.requires}'가 필요합니다")
        return a

    body = armor(raw.get("armor"), "body")
    shield = armor(raw.get("shield"), "shield")

    pouch_capacity = int(raw.get("pouch_capacity", 0))

    def stacks(key: str) -> tuple[tuple[str, int], ...]:
        out: list[tuple[str, int]] = []
        for st in raw.get(key, []):
            iid, cnt = st.get("item"), int(st.get("count", 0))
            if iid not in data.items:
                errors.append(f"{key}: 아이템 '{iid}'가 items.json에 없습니다")
            elif cnt > 0:
                out.append((iid, cnt))
        return tuple(out)

    pouch = stacks("pouch")
    inventory = stacks("inventory")
    if len(pouch) > pouch_capacity:
        errors.append(f"파우치 {len(pouch)}칸 사용: 용량 {pouch_capacity}칸")

    carry = 0
    for w in spares:
        carry += w.wear_weight // 10          # 예비 무기는 소지품 (착용 무게는 0.1 단위)
    for iid, cnt in pouch + inventory:
        if iid in data.items:
            carry += data.items[iid].weight * cnt

    if errors:
        raise LoadoutError(f"'{raw.get('name', '?')}' 구성 오류:\n  - " + "\n  - ".join(errors))

    return PlayerSpec(
        name=str(raw.get("name", "플레이어")), level=level,
        max_hp=rules.base_hp + rules.hp_per_level * (level - 1),
        str_=str_, agi=agi, slot_skills=slots, sword_skills=tuple(sword_skills),
        weapon=main, spare_weapons=spares, armor=body, shield=shield,
        quick_change=bool(raw.get("quick_change", False)),
        pouch_capacity=pouch_capacity, pouch=pouch, inventory=inventory, carry_weight=carry,
    )


def build_party(data: GameData, raw: dict[str, Any]) -> tuple[PlayerSpec, ...]:
    members = raw.get("members")
    if not isinstance(members, list) or not members:
        raise LoadoutError("party: members 목록이 필요합니다")
    errors: list[str] = []
    specs: list[PlayerSpec] = []
    for m in members:
        try:
            specs.append(build_player(data, m))
        except LoadoutError as e:
            errors.append(str(e))
    if len(data.rules.ally_spawns) < len(members):
        errors.append(f"파티 {len(members)}명: rules.arena.ally_spawns가 부족합니다")
    if errors:
        raise LoadoutError("\n".join(errors))
    return tuple(specs)
