"""정의 데이터(불변). 경계 레이어가 읽어 온 dict를 받아 타입이 고정된 정의로 바꾼다.

데이터 파일의 시간은 프레임이고, 여기서 틱(정수)으로 바꿔 저장한다. 필드 이름의 `_t`는 틱 단위라는 뜻.
무게(착용·소지)는 0.1 단위 정수, 내구도는 기획서 수치의 10배 정수로 다룬다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.hexgrid import Hex, dist

STANCE_ORDER: tuple[str, ...] = ("guard", "evade")        # 패리는 태세가 아니다 (소드 스킬 판정 맞추기 = 상쇄)
ATTACK_KINDS: tuple[str, ...] = ("sword_skill", "basic", "throw", "beast")
TARGETINGS: tuple[str, ...] = ("person", "place")
AREAS: tuple[str, ...] = ("single", "arc3", "ring1", "line2", "disc1")
SIZES: tuple[str, ...] = ("small", "medium", "large")
PHYSICALS: tuple[str, ...] = ("", "slash", "thrust", "blunt", "pierce")   # 참격, 찌르기, 타격, 관통 (상성 규칙은 미정)
HOWL_MODES: tuple[str, ...] = ("boost", "cut")
SECTORS = 4                                                # 정면, 앞쪽 측면, 뒤쪽 측면, 후면


class DataError(Exception):
    pass


_REQUIRED = object()


class _Reader:
    """키 오타를 잡기 위해 읽지 않은 키가 남으면 오류를 낸다. '_'로 시작하는 키는 주석으로 무시."""

    def __init__(self, raw: Any, where: str) -> None:
        if not isinstance(raw, dict):
            raise DataError(f"{where}: 객체(dict)여야 합니다")
        self.raw: dict[str, Any] = raw
        self.where = where
        self.used: set[str] = set()

    def get(self, key: str, default: Any = _REQUIRED) -> Any:
        self.used.add(key)
        if key in self.raw:
            return self.raw[key]
        if default is _REQUIRED:
            raise DataError(f"{self.where}: 필수 키 '{key}'가 없습니다")
        return default

    def int(self, key: str, default: Any = _REQUIRED) -> int:
        v = self.get(key, default)
        if isinstance(v, bool) or not isinstance(v, int):
            raise DataError(f"{self.where}.{key}: 정수여야 합니다 (값: {v!r})")
        return v

    def bool(self, key: str, default: Any = _REQUIRED) -> bool:
        v = self.get(key, default)
        if not isinstance(v, bool):
            raise DataError(f"{self.where}.{key}: true/false여야 합니다 (값: {v!r})")
        return v

    def str(self, key: str, default: Any = _REQUIRED) -> str:
        v = self.get(key, default)
        if v is None:
            return ""
        if not isinstance(v, str):
            raise DataError(f"{self.where}.{key}: 문자열이어야 합니다 (값: {v!r})")
        return v

    def physical(self, key: str, default: str = "") -> str:
        v = self.str(key, default)
        if v not in PHYSICALS:
            raise DataError(f"{self.where}.{key}: {PHYSICALS[1:]} 중 하나여야 합니다")
        return v

    def hexes(self, key: str) -> tuple[Hex, ...]:
        v = self.get(key)
        try:
            return tuple((int(a), int(b)) for a, b in v)
        except (TypeError, ValueError):
            raise DataError(f"{self.where}.{key}: [[q, r], ...] 형식이어야 합니다") from None

    def sub(self, key: str) -> _Reader:
        return _Reader(self.get(key), f"{self.where}.{key}")

    def done(self) -> None:
        extra = [k for k in self.raw if k not in self.used and not k.startswith("_")]
        if extra:
            raise DataError(f"{self.where}: 알 수 없는 키 {extra}")


@dataclass(slots=True, frozen=True)
class HitDef:
    at_t: int
    power_bp: int
    hitstun_t: int
    break_value: int


@dataclass(slots=True, frozen=True)
class AttackDef:
    id: str
    name: str
    kind: str
    family: str
    required_proficiency: int
    range_min: int            # 칸 거리 (적은 차지한 칸 중 가장 가까운 칸 기준)
    range_max: int
    targeting: str            # person: 대상을 따라감, place: 시작 시 칸 고정
    area: str                 # 범위 모양 (AREAS)
    startup_t: int
    active_t: int
    recovery_t: int
    cooldown_t: int
    lunge: bool               # 돌진: 판정 시 대상 옆 칸으로 파고든다
    can_clash: bool           # 패리(상쇄)에 쓰이거나 패리될 수 있다
    interrupts: bool
    fixed_power: int
    hits: tuple[HitDef, ...]
    weak: bool                # 일반 공격: 자동 대응 대상, 수동 대응은 거의 확정, 막히면 딜레이 캐치
    physical: str             # 물리 속성 태그 (아군 무기 기술은 무기를 따른다)
    heavy: bool               # 중량 태그: 가드 칩 피해·가드 경직·내구도 소모·패리 위력이 커진다
    knockdown_bp: int         # 부가 효과 넘어짐 확률 (가드로 막으면 걸리지 않는다)

    @property
    def style(self) -> str:
        """소드 스킬 유형 태그: 돌진 / 연격 / 단타."""
        return "rush" if self.lunge else "combo" if len(self.hits) > 1 else "single"


@dataclass(slots=True, frozen=True)
class WeaponDef:
    id: str
    name: str
    family: str
    physical: str
    attack: int
    speed_bp: int
    weight_stat: int          # 무게 스탯(강화 항목): 패리 승리 시 상대 경직·무력화 증가
    wear_weight: int          # 착용 무게 (0.1 단위)
    min_str: int              # 최소 근력 (모자라면 장비할 수 없다)
    durability: int           # 최대 내구도 (기획서 수치 × 10)


@dataclass(slots=True, frozen=True)
class ArmorDef:
    id: str
    name: str
    slot: str
    defense: int
    wear_weight: int          # 착용 무게 (0.1 단위)
    durability: int
    requires: str
    guard_bonus_bp: int


@dataclass(slots=True, frozen=True)
class ItemDef:
    id: str
    name: str
    kind: str                 # regen_boost: 휴식 회복 속도를 일정 시간 높임
    boost_bp: int
    duration_t: int
    weight: int               # 소지 무게


@dataclass(slots=True, frozen=True)
class SlotSkillDef:
    id: str
    name: str
    kind: str
    family: str
    stance_bonus: tuple[tuple[str, int], ...]

    def bonus_for(self, stance: str) -> int:
        for k, v in self.stance_bonus:
            if k == stance:
                return v
        return 0


@dataclass(slots=True, frozen=True)
class StanceRule:
    kind: str
    name: str
    startup_t: int
    min_hold_t: int
    base_bp: int
    str_bonus_bp: int         # 근력 1당 (가드)
    agi_bonus_bp: int         # 민첩 1당 (회피)
    requires_weapon: bool
    roll_per_attack: bool
    chip_bp: int
    defender_stun_bp: int
    advantage_t: int
    fail_damage_bp: int
    fail_extra_stun_t: int


@dataclass(slots=True, frozen=True)
class ParryRule:
    """패리(상쇄): 내 소드 스킬의 판정을 적의 판정에 맞춘다. 맞으면 피해 0, 적의 모든 타를 막았을 때 위력 비교."""
    window_t: int
    draw_margin_bp: int
    str_bonus_bp: int
    draw_stun_t: int
    lose_stun_t: int
    knockback_cells: int      # 진 아군이 밀려나는 칸 수


@dataclass(slots=True, frozen=True)
class HeavyRule:
    chip_bp: int              # 가드 칩 피해 배율
    guard_stun_bp: int        # 가드한 쪽의 경직 배율
    durability_bp: int        # 막은 쪽 장비 내구도 소모 배율
    parry_power_bp: int       # 패리 위력 비교 배율


@dataclass(slots=True, frozen=True)
class KnockdownRule:
    getup_t: int              # 아군 기상 시간 (착용 무게로 늘어남). 적은 적 데이터의 기상 시간
    wear_bp: int              # 착용 무게 0.1당 기상 시간 증가율


@dataclass(slots=True, frozen=True)
class WearRule:
    """착용 무게: 무기·방패·방어구 무게 합 vs 근력으로 정해지는 한도. 넘으면 이동과 공격 프레임에 배수."""
    base_limit: int           # 0.1 단위
    per_str: int              # 근력 1당 한도 (0.1 단위)
    linear: int               # 배수 = 1 + linear·r + quad·r²  (r = 초과 비율)
    quad: int


@dataclass(slots=True, frozen=True)
class DurabilityRule:
    skill_hit: int            # 소드 스킬 1타 (무기)
    basic_hit: int            # 일반 공격 (무기)
    parry: int                # 패리 (무기)
    guard: int                # 가드 (방패가 있으면 방패, 없으면 무기)
    armor_per_damage: int     # 방어구: 방어력으로 줄인 피해 1당
    warn_bp: tuple[int, ...]


@dataclass(slots=True, frozen=True)
class SizeRule:
    hitstun_bp: int
    breakable: bool
    decay: tuple[int, ...]


@dataclass(slots=True, frozen=True)
class AiRule:
    """지능(기억 길이)에서 적응·기억 값을 뽑는 계수."""
    person_rate: int          # 사람 적응 초당 증가 = 기억 길이(틱) × rate / 초당 틱
    person_cap_bp: int
    tech_rate: int            # 기술 적응 1회 목격당 증가 = 기억 길이(틱) × rate / 초당 틱
    tech_cap_bp: int
    def_person_bp: int        # 적응 → 적 태세 성공률 보너스 계수
    def_tech_bp: int
    trigger_step_bp: int      # 사람 적응이 이만큼 쌓일 때마다 즉시 반응하는 공격이 1개 늘어남
    retention_mult: int       # 최근 상대 기억 유지 시간 = 기억 길이 × mult
    capacity_per_t: int       # 기억 용량 = 1 + 기억 길이 / 이 값
    profile_carry_bp: int     # 같은 사람의 다른 패턴으로 이어지는 적응 비율


@dataclass(slots=True, frozen=True)
class AutoRule:
    """일반 공격에 대한 자동 대응과 딜레이 캐치."""
    base_bp: int              # 아군 자동 대응 기본 확률
    stat_bp: int              # 아군: 해당 스탯(가드=근력, 회피=민첩) 1당 추가
    weak_manual_bp: int       # 일반 공격에 수동 태세로 대응했을 때 성공률
    guard_catch_t: int        # 일반 공격을 가드당한 쪽이 굳는 시간 (막은 쪽의 일반 공격이 들어감)
    evade_catch_t: int        # 일반 공격을 회피당한 쪽이 굳는 시간 (선딜 짧은 소드 스킬이 들어감)
    ally_vision: int          # 아군(인간형) 자동 대응 방향 구역: 0 정면, 1 앞쪽 측면, 2 뒤쪽 측면, 3 후면까지


@dataclass(slots=True, frozen=True)
class HowlRule:
    startup_t: int
    recovery_t: int
    cooldown_t: int
    radius: int
    mode: str
    boost: int
    cut_bp: int


@dataclass(slots=True, frozen=True)
class PatternStep:
    kind: str                 # attack | wait
    attack_id: str
    wait_t: int


@dataclass(slots=True, frozen=True)
class EnemyDef:
    id: str
    name: str
    tier: str                 # 난이도 등급 (평가 도구용, 없으면 빈 문자열)
    size: str
    footprint: tuple[Hex, ...]
    intelligence: str
    sword_skill_user: bool
    high_grade: bool
    hp: int
    attack: int
    defense: int
    stances: tuple[str, ...]
    stance_bonus_bp: int
    vision: int               # 자동 대응 방향 구역 (0 정면 ~ 3 후면까지)
    auto_defense_bp: int      # 자동 대응 기본 확률 (적응 보너스가 더해짐)
    sector_damage_bp: tuple[int, ...]   # 받는 피해 배율: 정면, 앞쪽 측면, 뒤쪽 측면, 후면 (고유 특징: 단단한 정면, 약점 등)
    clash_bonus_bp: int       # 패리 위력 비교 보정
    passive: bool             # 비선공: 먼저 공격받기 전에는 공격하지 않는다
    exposed_on_defended: bool  # 자기 공격이 가드·회피로 막혀 굳어 있는 동안 방향 보정·방어력이 무시된다
    pack: bool                # 무리: 같은 종이 한 대상을 노리고, 정면이 잡혀 있으면 옆·뒤로 돈다
    getup_t: int              # 넘어졌을 때 기상 시간
    reaction_t: int
    move_t: int
    turn_speed_bp: int
    break_max: int
    break_down_t: int
    rise_attack: str
    surround_attack: str
    surround_count: int
    patterns: tuple[tuple[PatternStep, ...], ...]
    attacks: tuple[AttackDef, ...]

    def attack_by_id(self, attack_id: str) -> AttackDef:
        for a in self.attacks:
            if a.id == attack_id:
                return a
        raise KeyError(attack_id)

    @property
    def weak_attack(self) -> AttackDef | None:
        return next((a for a in self.attacks if a.weak), None)


@dataclass(slots=True, frozen=True)
class Rules:
    ticks_per_frame: int
    fps: int
    time_limit_t: int
    arena_radius: int
    ally_spawns: tuple[Hex, ...]
    enemy_spawns: tuple[Hex, ...]
    footprints: tuple[tuple[str, tuple[Hex, ...]], ...]
    base_hp: int
    hp_per_level: int
    slots_by_level: tuple[tuple[int, int], ...]
    level1_stat_points: int
    carry_limit: int
    move_cell_t: int
    move_agi_reduction_bp: int
    move_max_reduction_bp: int
    carry_over_penalty_bp: int
    move_max_cells: int
    wait_max_t: int
    stances: tuple[StanceRule, ...]
    min_success_bp: int
    max_success_bp: int
    parry: ParryRule
    heavy: HeavyRule
    knockdown: KnockdownRule
    wear: WearRule
    weight_stun_ticks: int
    weight_break: int
    sizes: tuple[tuple[str, SizeRule], ...]
    durability: DurabilityRule
    pouch_use_t: int
    inventory_use_t: int
    swap_t: int
    quick_swap_t: int
    approx_round_t: int
    front_hate: int
    turn_t: int
    reload_t: int
    reload_remembered_t: int
    rest_interval_t: int
    rest_regen: int
    ff_damage_bp: int
    ff_stun_t: int
    howl: HowlRule
    auto: AutoRule
    ai: AiRule
    intelligence: tuple[tuple[str, int], ...]   # 지능 → 기억 길이(틱)

    def stance(self, kind: str) -> StanceRule:
        for s in self.stances:
            if s.kind == kind:
                return s
        raise KeyError(kind)

    def size(self, size: str) -> SizeRule:
        for k, v in self.sizes:
            if k == size:
                return v
        raise KeyError(size)

    def memory_t(self, level: str) -> int:
        for k, v in self.intelligence:
            if k == level:
                return v
        raise KeyError(level)

    def footprint(self, key: str) -> tuple[Hex, ...]:
        for k, v in self.footprints:
            if k == key:
                return v
        raise KeyError(key)

    def slots_for_level(self, level: int) -> int:
        n = 0
        for lv, count in self.slots_by_level:
            if level >= lv:
                n = count
        return n

    @property
    def ticks_per_sec(self) -> int:
        return self.ticks_per_frame * self.fps


@dataclass(slots=True, frozen=True)
class GameData:
    rules: Rules
    actions: dict[str, AttackDef]
    weapons: dict[str, WeaponDef]
    armors: dict[str, ArmorDef]
    items: dict[str, ItemDef]
    slot_skills: dict[str, SlotSkillDef]
    enemies: dict[str, EnemyDef]


# ---------------------------------------------------------------- 파싱


def _parse_rules(raw: Any) -> Rules:
    r = _Reader(raw, "rules")
    tpf = r.int("ticks_per_frame")
    fps = r.int("fps")

    def t(frames: int) -> int:
        return frames * tpf

    ar = r.sub("arena")
    radius = ar.int("radius")
    ally_spawns, enemy_spawns = ar.hexes("ally_spawns"), ar.hexes("enemy_spawns")
    ar.done()
    for h in ally_spawns + enemy_spawns:
        if dist(h, (0, 0)) > radius:
            raise DataError(f"rules.arena: 시작 칸 {h}가 무대 밖입니다")

    fp_all = r.sub("footprints")
    footprints = tuple((k, fp_all.hexes(k)) for k in sorted(fp_all.raw) if not k.startswith("_"))
    fp_all.done()

    pl = r.sub("player")
    base_hp, hp_per_level = pl.int("base_hp"), pl.int("hp_per_level")
    slots = tuple((int(a), int(b)) for a, b in pl.get("skill_slots_by_level"))
    stat_points = pl.int("level1_stat_points")
    carry_limit = pl.int("carry_limit")
    pl.done()

    mv = r.sub("move")
    move = (t(mv.int("cell_frames")), mv.int("agi_reduction_bp"), mv.int("max_reduction_bp"),
            mv.int("carry_over_penalty_bp"), mv.int("max_cells"))
    mv.done()

    st_all = r.sub("stances")
    stances: list[StanceRule] = []
    for kind in STANCE_ORDER:
        s = st_all.sub(kind)
        succ, fail = s.sub("success"), s.sub("fail")
        roll = s.str("roll")
        if roll not in ("attack", "hit"):
            raise DataError(f"rules.stances.{kind}.roll: attack|hit 이어야 합니다")
        stances.append(StanceRule(
            kind=kind, name=s.str("name"),
            startup_t=t(s.int("startup_frames")), min_hold_t=t(s.int("min_hold_frames")),
            base_bp=s.int("base_bp"), str_bonus_bp=s.int("str_bonus_bp"), agi_bonus_bp=s.int("agi_bonus_bp"),
            requires_weapon=s.bool("requires_weapon"), roll_per_attack=(roll == "attack"),
            chip_bp=succ.int("chip_bp"), defender_stun_bp=succ.int("defender_stun_bp"),
            advantage_t=t(succ.int("advantage_frames")),
            fail_damage_bp=fail.int("damage_bp"), fail_extra_stun_t=t(fail.int("extra_stun_frames")),
        ))
        succ.done(); fail.done(); s.done()
    st_all.done()

    pa = r.sub("parry")
    parry = ParryRule(t(pa.int("window_frames")), pa.int("draw_margin_bp"), pa.int("str_bonus_bp"),
                      t(pa.int("draw_stun_frames")), t(pa.int("lose_stun_frames")), pa.int("lose_knockback_cells"))
    pa.done()

    hv = r.sub("heavy")
    heavy = HeavyRule(hv.int("chip_bp"), hv.int("guard_stun_bp"), hv.int("durability_bp"), hv.int("parry_power_bp"))
    hv.done()

    kd = r.sub("knockdown")
    knockdown = KnockdownRule(t(kd.int("getup_frames")), kd.int("wear_bp"))
    kd.done()

    we = r.sub("wear")
    wear = WearRule(we.int("base_limit"), we.int("per_str"), we.int("linear"), we.int("quad"))
    we.done()

    wt = r.sub("weight")
    weight_stun, weight_break = wt.int("stun_ticks_per_weight"), wt.int("break_per_weight")
    wt.done()

    sizes: list[tuple[str, SizeRule]] = []
    sz_all = r.sub("sizes")
    for key in ("player",) + SIZES:
        s = sz_all.sub(key)
        decay = tuple(int(x) for x in s.get("decay"))
        if not decay:
            raise DataError(f"rules.sizes.{key}.decay: 비어 있으면 안 됩니다")
        sizes.append((key, SizeRule(s.int("hitstun_bp"), s.bool("breakable"), decay)))
        s.done()
    sz_all.done()

    du = r.sub("durability")
    durability = DurabilityRule(du.int("skill_hit"), du.int("basic_hit"), du.int("parry"), du.int("guard"),
                                du.int("armor_per_damage"), tuple(int(x) for x in du.get("warn_bp")))
    du.done()

    it = r.sub("items")
    items = (t(it.int("pouch_use_frames")), t(it.int("inventory_use_frames")), t(it.int("swap_frames")), t(it.int("quick_swap_frames")))
    it.done()

    kn = r.sub("knowledge")
    approx_round = t(kn.int("approx_round_frames"))
    kn.done()

    ha = r.sub("hate")
    front_hate = ha.int("front")
    ha.done()

    rt = r.sub("retarget")
    retarget = (t(rt.int("turn_frames")), t(rt.int("reload_frames")), t(rt.int("reload_remembered_frames")))
    rt.done()

    rs = r.sub("rest")
    rest = (t(rs.int("interval_frames")), rs.int("regen"))
    rs.done()
    if rest[0] <= 0:
        raise DataError("rules.rest.interval_frames: 0보다 커야 합니다")

    ff = r.sub("friendly_fire")
    friendly = (ff.int("damage_bp"), t(ff.int("stun_frames")))
    ff.done()

    hw = r.sub("howl")
    howl = HowlRule(t(hw.int("startup_frames")), t(hw.int("recovery_frames")), t(hw.int("cooldown_frames")),
                    hw.int("radius"), hw.str("mode"), hw.int("boost"), hw.int("cut_bp"))
    hw.done()
    if howl.mode not in HOWL_MODES:
        raise DataError(f"rules.howl.mode: {HOWL_MODES} 중 하나여야 합니다")

    au = r.sub("auto_defense")
    auto = AutoRule(au.int("base_bp"), au.int("stat_bp"), au.int("weak_manual_bp"), t(au.int("guard_catch_frames")),
                    t(au.int("evade_catch_frames")), au.int("ally_vision"))
    au.done()

    ai = r.sub("ai")
    ai_rule = AiRule(ai.int("person_rate"), ai.int("person_cap_bp"), ai.int("tech_rate"), ai.int("tech_cap_bp"),
                     ai.int("def_person_bp"), ai.int("def_tech_bp"), ai.int("trigger_step_bp"),
                     ai.int("retention_mult"), t(ai.int("capacity_per_frames")), ai.int("profile_carry_bp"))
    ai.done()
    if ai_rule.trigger_step_bp <= 0 or ai_rule.capacity_per_t <= 0:
        raise DataError("rules.ai: trigger_step_bp, capacity_per_frames는 0보다 커야 합니다")

    intel: list[tuple[str, int]] = []
    in_all = r.sub("intelligence")
    for level in sorted(in_all.raw):
        if level.startswith("_"):
            continue
        p = in_all.sub(level)
        intel.append((level, t(p.int("memory_frames"))))
        p.done()
    in_all.done()

    rules = Rules(
        ticks_per_frame=tpf, fps=fps, time_limit_t=t(r.int("time_limit_frames")),
        arena_radius=radius, ally_spawns=ally_spawns, enemy_spawns=enemy_spawns, footprints=footprints,
        base_hp=base_hp, hp_per_level=hp_per_level, slots_by_level=slots, level1_stat_points=stat_points,
        carry_limit=carry_limit,
        move_cell_t=move[0], move_agi_reduction_bp=move[1], move_max_reduction_bp=move[2],
        carry_over_penalty_bp=move[3], move_max_cells=move[4],
        wait_max_t=t(r.int("wait_max_frames")),
        stances=tuple(stances), min_success_bp=r.int("min_success_bp"), max_success_bp=r.int("max_success_bp"),
        parry=parry, heavy=heavy, knockdown=knockdown, wear=wear,
        weight_stun_ticks=weight_stun, weight_break=weight_break,
        sizes=tuple(sizes), durability=durability,
        pouch_use_t=items[0], inventory_use_t=items[1], swap_t=items[2], quick_swap_t=items[3],
        approx_round_t=approx_round, front_hate=front_hate,
        turn_t=retarget[0], reload_t=retarget[1], reload_remembered_t=retarget[2],
        rest_interval_t=rest[0], rest_regen=rest[1],
        ff_damage_bp=friendly[0], ff_stun_t=friendly[1],
        howl=howl, auto=auto, ai=ai_rule, intelligence=tuple(intel),
    )
    r.done()
    return rules


def _parse_attack(raw: Any, attack_id: str, where: str, tpf: int, default_kind: str) -> AttackDef:
    r = _Reader(raw, where)

    def t(frames: int) -> int:
        return frames * tpf

    kind = r.str("kind", default_kind)
    if kind not in ATTACK_KINDS:
        raise DataError(f"{where}.kind: {ATTACK_KINDS} 중 하나여야 합니다")
    rng = r.get("range")
    if not (isinstance(rng, list) and len(rng) == 2 and 1 <= rng[0] <= rng[1]):
        raise DataError(f"{where}.range: [최소, 최대] 칸 거리 (1 이상) 형식이어야 합니다")
    active_t = t(r.int("active"))
    hits: list[HitDef] = []
    for i, h_raw in enumerate(r.get("hits")):
        h = _Reader(h_raw, f"{where}.hits[{i}]")
        at_t = t(h.int("at"))
        if not (0 <= at_t < active_t):
            raise DataError(f"{where}.hits[{i}].at: 0 이상, active 미만이어야 합니다")
        if hits and at_t <= hits[-1].at_t:
            raise DataError(f"{where}.hits: at은 증가 순서여야 합니다")
        hits.append(HitDef(at_t, h.int("power_bp"), t(h.int("hitstun")), h.int("break", 0)))
        h.done()
    if not hits:
        raise DataError(f"{where}.hits: 최소 1타가 필요합니다")
    targeting = r.str("targeting", "person")
    if targeting not in TARGETINGS:
        raise DataError(f"{where}.targeting: person|place 이어야 합니다")
    area = r.str("area", "single")
    if area not in AREAS:
        raise DataError(f"{where}.area: {AREAS} 중 하나여야 합니다")
    if targeting == "place" and area not in ("single", "disc1"):
        raise DataError(f"{where}.area: 장소 지정 공격은 single|disc1만 가능합니다")
    d = AttackDef(
        id=attack_id, name=r.str("name"), kind=kind, family=r.str("family", ""),
        required_proficiency=r.int("required_proficiency", 0),
        range_min=int(rng[0]), range_max=int(rng[1]), targeting=targeting, area=area,
        startup_t=t(r.int("startup")), active_t=active_t, recovery_t=t(r.int("recovery")),
        cooldown_t=t(r.int("cooldown")),
        lunge=r.bool("lunge", False), can_clash=r.bool("can_clash"), interrupts=r.bool("interrupts"),
        fixed_power=r.int("fixed_power", 0), hits=tuple(hits), weak=r.bool("weak", False),
        physical=r.physical("physical"), heavy=r.bool("heavy", False), knockdown_bp=r.int("knockdown_bp", 0),
    )
    r.done()
    return d


def _parse_enemy(k: str, v: Any, rules: Rules) -> EnemyDef:
    tpf = rules.ticks_per_frame
    where = f"enemies.{k}"
    r = _Reader(v, where)
    size = r.str("size")
    if size not in SIZES:
        raise DataError(f"{where}.size: {SIZES} 중 하나여야 합니다")
    intel = r.str("intelligence")
    if intel not in {lv for lv, _ in rules.intelligence}:
        raise DataError(f"{where}.intelligence: rules.intelligence에 없는 값 '{intel}'")
    fp_key = r.str("footprint")
    try:
        footprint = rules.footprint(fp_key)
    except KeyError:
        raise DataError(f"{where}.footprint: rules.footprints에 없는 값 '{fp_key}'") from None

    attacks: list[AttackDef] = []
    for i, a_raw in enumerate(r.get("attacks")):
        a_id = a_raw.get("id") if isinstance(a_raw, dict) else None
        if not isinstance(a_id, str):
            raise DataError(f"{where}.attacks[{i}]: id가 필요합니다")
        a_raw = {kk: vv for kk, vv in a_raw.items() if kk != "id"}
        attacks.append(_parse_attack(a_raw, a_id, f"{where}.attacks.{a_id}", tpf, "beast"))
    attack_ids = [a.id for a in attacks]

    def check_attack(key: str, value: str) -> None:
        if value and value not in attack_ids:
            raise DataError(f"{where}.{key}: attacks에 없는 공격 '{value}'")

    patterns: list[tuple[PatternStep, ...]] = []
    for pi, pat in enumerate(r.get("patterns")):
        steps: list[PatternStep] = []
        for s in pat:
            if not isinstance(s, str):
                raise DataError(f"{where}.patterns[{pi}]: 단계는 문자열이어야 합니다")
            if s.startswith("wait:"):
                steps.append(PatternStep("wait", "", int(s[5:]) * tpf))
            else:
                check_attack(f"patterns[{pi}]", s)
                steps.append(PatternStep("attack", s, 0))
        if not steps:
            raise DataError(f"{where}.patterns[{pi}]: 비어 있으면 안 됩니다")
        patterns.append(tuple(steps))
    if not patterns:
        raise DataError(f"{where}.patterns: 최소 한 개가 필요합니다")

    stances = tuple(r.get("stances"))
    for s in stances:
        if s not in STANCE_ORDER:
            raise DataError(f"{where}.stances: 적의 태세는 {STANCE_ORDER}뿐입니다 (적은 패리를 쓰지 않는다): '{s}'")
    user, high = r.bool("sword_skill_user"), r.bool("high_grade", False)
    limit = 2 if user or high else 1
    if len(stances) > limit:
        raise DataError(f"{where}.stances: 이 적은 태세를 최대 {limit}개까지 쓸 수 있습니다")

    if sum(1 for a in attacks if a.weak) > 1:
        raise DataError(f"{where}.attacks: 일반 공격(weak)은 몬스터마다 하나만 둘 수 있습니다")
    vision = r.int("vision")
    if not 0 <= vision <= 3:
        raise DataError(f"{where}.vision: 0(정면)~3(후면까지) 이어야 합니다")
    sector = tuple(int(x) for x in r.get("sector_damage_bp", [10000] * SECTORS))
    if len(sector) != SECTORS:
        raise DataError(f"{where}.sector_damage_bp: [정면, 앞쪽 측면, 뒤쪽 측면, 후면] 4개여야 합니다")
    rise, surround = r.str("rise_attack", ""), r.str("surround_attack", "")
    check_attack("rise_attack", rise)
    check_attack("surround_attack", surround)
    e = EnemyDef(
        id=k, name=r.str("name"), tier=r.str("tier", ""), size=size, footprint=footprint, intelligence=intel,
        sword_skill_user=user, high_grade=high,
        hp=r.int("hp"), attack=r.int("attack"), defense=r.int("defense"),
        stances=stances, stance_bonus_bp=r.int("stance_bonus_bp", 0), vision=vision,
        auto_defense_bp=r.int("auto_defense_bp"), sector_damage_bp=sector, clash_bonus_bp=r.int("clash_bonus_bp", 0),
        passive=r.bool("passive", False), exposed_on_defended=r.bool("exposed_on_defended", False), pack=r.bool("pack", False), getup_t=r.int("getup_frames") * tpf,
        reaction_t=r.int("reaction_frames") * tpf, move_t=r.int("move_frames") * tpf,
        turn_speed_bp=r.int("turn_speed_bp", 0),
        break_max=r.int("break_max", 0), break_down_t=r.int("break_down_frames", 0) * tpf,
        rise_attack=rise, surround_attack=surround, surround_count=r.int("surround_count", 0),
        patterns=tuple(patterns), attacks=tuple(attacks),
    )
    r.done()
    if rules.size(size).breakable and e.break_max <= 0:
        raise DataError(f"{where}: {size} 크기는 무력화 대상이므로 break_max > 0 이어야 합니다")
    if surround and e.surround_count <= 0:
        raise DataError(f"{where}.surround_count: surround_attack이 있으면 1 이상이어야 합니다")
    return e


def parse_game_data(raw: dict[str, Any]) -> GameData:
    """raw: {"rules", "skills", "weapons", "armors", "items", "slot_skills", "enemies"} 각 파일의 dict."""
    rules = _parse_rules(raw["rules"])
    tpf = rules.ticks_per_frame

    def entries(section: str) -> list[tuple[str, Any]]:
        return [(k, v) for k, v in raw[section].items() if not k.startswith("_")]

    actions: dict[str, AttackDef] = {}
    for k, v in entries("skills"):
        a = _parse_attack(v, k, f"skills.{k}", tpf, "sword_skill")
        if a.kind == "beast":
            raise DataError(f"skills.{k}.kind: 플레이어 행동에 beast는 쓸 수 없습니다")
        actions[k] = a
    for needed in ("basic_attack", "throw"):
        if needed not in actions:
            raise DataError(f"skills: '{needed}' 정의가 필요합니다")

    weapons: dict[str, WeaponDef] = {}
    for k, v in entries("weapons"):
        r = _Reader(v, f"weapons.{k}")
        weapons[k] = WeaponDef(k, r.str("name"), r.str("family"), r.physical("physical"), r.int("attack"),
                               r.int("speed_bp"), r.int("weight_stat"), r.int("wear_weight"), r.int("min_str"), r.int("durability"))
        r.done()

    armors: dict[str, ArmorDef] = {}
    for k, v in entries("armors"):
        r = _Reader(v, f"armors.{k}")
        slot = r.str("slot")
        if slot not in ("body", "shield"):
            raise DataError(f"armors.{k}.slot: body|shield 이어야 합니다")
        armors[k] = ArmorDef(k, r.str("name"), slot, r.int("defense"), r.int("wear_weight"), r.int("durability"),
                             r.str("requires", ""), r.int("guard_bonus_bp", 0))
        r.done()

    items: dict[str, ItemDef] = {}
    for k, v in entries("items"):
        r = _Reader(v, f"items.{k}")
        kind = r.str("kind")
        if kind != "regen_boost":
            raise DataError(f"items.{k}.kind: 현재는 regen_boost만 지원합니다")
        items[k] = ItemDef(k, r.str("name"), kind, r.int("boost_bp"), r.int("duration") * tpf, r.int("weight"))
        r.done()

    slot_skills: dict[str, SlotSkillDef] = {}
    for k, v in entries("slot_skills"):
        r = _Reader(v, f"slot_skills.{k}")
        kind = r.str("kind")
        bonus_raw = r.get("stance_bonus", {})
        for s in bonus_raw:
            if s not in STANCE_ORDER:
                raise DataError(f"slot_skills.{k}.stance_bonus: 알 수 없는 태세 '{s}'")
        slot_skills[k] = SlotSkillDef(k, r.str("name"), kind, r.str("family", ""),
                                      tuple((s, int(bonus_raw[s])) for s in STANCE_ORDER if s in bonus_raw))
        r.done()

    enemies = {k: _parse_enemy(k, v, rules) for k, v in entries("enemies")}
    return GameData(rules, actions, weapons, armors, items, slot_skills, enemies)
