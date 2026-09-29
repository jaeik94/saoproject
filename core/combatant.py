"""전투 중 변하는 상태."""
from __future__ import annotations

from dataclasses import dataclass, field

from core.defs import ArmorDef, AttackDef, EnemyDef, WeaponDef
from core.hexgrid import Hex, add
from core.loadout import ItemStack

# busy 값
READY = "ready"            # 자유. 결정 대기 (아군) 또는 반응 대기 (적)
WAIT = "wait"              # 기다리기. 적이 행동을 시작하면 깨어남
STANCE = "stance"          # 태세 진입 ~ 최소 유지 시간 (묶여 있음)
STANCE_HOLD = "stance_hold"  # 태세 유지 중 자유 (깨어날 수 있음)
ATTACK = "attack"
MOVE = "move"
ITEM = "item"
SWAP = "swap"
HOWL = "howl"
TURN = "turn"              # 적: 대상 쪽으로 방향 전환
RETARGET = "retarget"      # 적: 대상 전환 (방향 전환 + 재로드)
HITSTUN = "hitstun"
BROKEN = "broken"          # 무력화 (다운)
KNOCKDOWN = "knockdown"    # 넘어짐 (기상할 때까지 행동·가드 불가)
DEAD = "dead"

WAKEABLE = (READY, WAIT, STANCE_HOLD)
PREMOTION, ACTIVE, RECOVERY = "premotion", "active", "recovery"
NO_TARGET = -1


@dataclass(slots=True)
class ActionState:
    action_id: int
    kind: str                 # attack | stance | move | item | swap | howl | wait | turn | retarget
    start: int
    end: int                  # 방해받지 않으면 자유로워지는 틱 (후딜 연장 포함)
    attack: AttackDef | None = None
    target: int = NO_TARGET   # 공격 대상 / 대상 전환의 새 대상
    facing: int = 0           # 공격 방향 (범위 모양 기준) / 전환 후 방향
    active_start: int = 0
    active_end: int = 0
    hit_ticks: list[int] = field(default_factory=list)
    consumed: list[bool] = field(default_factory=list)
    place_cells: list[Hex] = field(default_factory=list)  # 장소 지정 공격의 영향 칸 (시작 시 고정)
    defended: bool = False    # 방어측 딜레이 이득을 이미 적용했는가 (공격당 1회)
    guard_locked: list[int] = field(default_factory=list)  # 가드 성공한 방어자 (나머지 타격은 판정 없이 막힘)
    parried: list[bool] = field(default_factory=list)      # 이 공격의 타격 중 패리당한 것
    parrier: int = NO_TARGET                                # 패리한 아군
    parry_power_self: int = 0                               # 패리당한 타격들의 위력 합
    parry_power_other: int = 0                              # 패리에 쓰인 아군 타격들의 위력 합
    ref: str = ""             # 태세 종류, 아이템 id 등
    arg: int = 0              # 아이템 출처(0 파우치, 1 인벤토리), 예비 무기 인덱스
    dest: Hex = (0, 0)        # 이동 목적지
    turn_done: int = 0        # 대상 전환: 방향 전환이 끝나는 틱


@dataclass(slots=True)
class StanceState:
    kind: str
    active_from: int


@dataclass(slots=True)
class OwnedWeapon:
    weapon: WeaponDef
    durability: int


@dataclass(slots=True)
class Combatant:
    index: int                # fighters 안의 위치
    cid: str                  # ally0.., enemy0..
    name: str
    is_ally: bool
    size: str                 # rules.sizes 키
    max_hp: int
    hp: int
    cell: Hex                 # 아군: 서 있는 칸, 적: 기준 칸
    footprint: tuple[Hex, ...]
    str_: int
    agi: int
    base_power: int           # 적: 공격력. 아군: 무기 공격력을 씀
    defense: int
    clash_bonus_bp: int
    rank: int = 0             # 같은 틱 결정 순서 (작을수록 먼저)
    facing: int = 0
    busy: str = READY
    action: ActionState | None = None
    stance: StanceState | None = None
    free_at: int = 0
    free_token: int = 0
    decide_token: int = 0
    combo: int = 0
    cooldowns: dict[str, int] = field(default_factory=dict)
    break_gauge: int = 0
    next_action_id: int = 1
    # 아군
    weapon: OwnedWeapon | None = None
    spares: list[OwnedWeapon] = field(default_factory=list)
    armor: ArmorDef | None = None
    armor_dur: int = 0
    shield: ArmorDef | None = None
    shield_dur: int = 0
    pouch: list[ItemStack] = field(default_factory=list)
    inventory: list[ItemStack] = field(default_factory=list)
    focus: int = NO_TARGET    # 가장 최근에 공격한 적 (아군의 시선 기준)
    boost_until: int = 0      # 포션: 휴식 회복 가속이 끝나는 틱
    boost_bp: int = 0
    sword_skills: tuple[AttackDef, ...] = ()
    slot_skills: tuple[str, ...] = ()
    quick_change: bool = False
    carry_weight: int = 0     # 인벤토리 소지 무게
    # 적
    enemy_def: EnemyDef | None = None
    target: int = NO_TARGET
    pattern: int = 0
    step: int = 0
    reaction: int = NO_TARGET  # 반응할 공격을 시작한 아군 (없으면 NO_TARGET)
    catch_target: int = NO_TARGET  # 일반 공격을 막아 낸 뒤 딜레이 캐치할 상대
    provoked: bool = True     # 비선공 적은 공격받기 전까지 False
    exposed_until: int = 0    # 이 틱까지 방향 보정·방어력 무시 (공격이 막혀 굳은 동안)

    @property
    def alive(self) -> bool:
        return self.busy != DEAD

    @property
    def power(self) -> int:
        if self.is_ally:
            return self.weapon.weapon.attack if self.weapon else 0
        return self.base_power

    @property
    def wear_weight(self) -> int:
        """착용 무게 (0.1 단위): 무기·방패·방어구."""
        total = self.weapon.weapon.wear_weight if self.weapon else 0
        for a in (self.armor, self.shield):
            if a is not None:
                total += a.wear_weight
        return total

    @property
    def weapon_family(self) -> str:
        return self.weapon.weapon.family if self.weapon else "unarmed"

    def cells(self) -> list[Hex]:
        return [add(self.cell, o) for o in self.footprint]

    def attack_phase(self, now: int) -> str:
        a = self.action
        if self.busy != ATTACK or a is None:
            return ""
        if now < a.active_start:
            return PREMOTION
        if now < a.active_end:
            return ACTIVE
        return RECOVERY

    def pending_hits(self, now: int) -> list[int]:
        """아직 판정되지 않은 타격의 인덱스 (현재 공격)."""
        a = self.action
        if self.busy != ATTACK or a is None:
            return []
        return [i for i in range(len(a.hit_ticks)) if not a.consumed[i] and a.hit_ticks[i] >= now]
