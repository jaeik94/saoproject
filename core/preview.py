"""아군 결정 요청과 타임라인 미리보기.

정보(knowledge)가 없으면 적의 기술 종류, 정확한 판정 시점, 속성, 타수, 후딜 종료 시점, 앞으로의 패턴은 보이지 않는다.
공격이 오고 있다는 사실, 대략적인 시점, 공격 대상(사람/장소), 적의 현재 대상과 대상 전환 진행은 항상 보인다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.combatant import (
    ATTACK, BROKEN, HITSTUN, HOWL, ITEM, KNOCKDOWN, MOVE, NO_TARGET, PREMOTION, RETARGET, STANCE, STANCE_HOLD, SWAP, TURN, WAIT,
    Combatant,
)
from core.defs import STANCE_ORDER, AttackDef, StanceRule
from core.formulas import attack_timing, hit_offsets, in_range, move_cell_ticks, stance_chance, unit_distance

if TYPE_CHECKING:
    from core.battle import Battle

UNKNOWN_END = -1
PROJECT_STEPS = 6


@dataclass(slots=True, frozen=True)
class ActionChoice:
    kind: str        # skill | basic | throw | stance | move | item | swap | howl | wait
    ref: str = ""    # 공격·태세·아이템 id, 이동은 "q,r"
    arg: int = 0     # 공격: 대상 fighters 인덱스, 이동: 걸음 수, 아이템: 출처, 교체: 예비 무기 인덱스


@dataclass(slots=True, frozen=True)
class Segment:
    """타임라인 한 구간. 시간은 지금 기준 상대 틱. end_t가 UNKNOWN_END면 끝을 모른다."""
    kind: str        # premotion | active | recovery | stance | move | item | swap | wait | hitstun | broken | idle | turn | reload
    start_t: int
    end_t: int
    exact: bool = True
    planned: bool = False    # 앞으로의 패턴 예측 (방해받으면 달라짐)
    label: str = ""


@dataclass(slots=True, frozen=True)
class Timeline:
    segments: tuple[Segment, ...] = ()
    hits_t: tuple[int, ...] = ()     # 타격 시점 (지금 행동)
    hits_exact: bool = True
    free_t: int = UNKNOWN_END        # 자유로워지는 시점 (모르면 UNKNOWN_END)


@dataclass(slots=True, frozen=True)
class RowView:
    """타임라인 한 줄 (전투원 하나)."""
    index: int
    name: str
    is_ally: bool
    line: Timeline
    target: int = NO_TARGET          # 적: 지금 노리는 아군
    retarget_left_t: int = 0         # 적: 대상 전환이 끝나기까지 (방향 전환·재로드 중 큰 쪽)
    resting: bool = False            # 아군: 휴식 중


@dataclass(slots=True, frozen=True)
class ThreatView:
    """나에게 다가오는 적 공격."""
    attacker: int
    targeting: str                   # person | place
    place_cells: tuple[tuple[int, int], ...]
    approx_hit_in_t: int             # 다음 타격까지 (반올림한 근사값, 항상 보임)
    known: bool
    name: str = ""
    heavy: bool = False              # 태그 (known일 때만): 중량
    style: str = ""                  # 소드 스킬 유형: single | combo | rush
    physical: str = ""               # 물리 속성
    hits_left: int = 0
    hit_in_t: int = -1               # 정확한 값 (known일 때만)
    enemy_free_in_t: int = -1        # 적 후딜 종료까지 (known일 때만)
    weak: bool = False               # 약공격 (known일 때만)


@dataclass(slots=True, frozen=True)
class OptionView:
    choice: ActionChoice
    label: str
    free_in_t: int                       # 방해받지 않으면 내 다음 차례까지
    first_hit_in_t: int = -1
    before_enemy_hit: bool | None = None  # 내 첫 타격이 대상 적의 프리모션 안에 들어감 (판정 시작 전 = 끊기 후보)
    clash_timing: bool | None = None      # 패리로 적 기술의 모든 타를 막을 수 있음
    parry_hits: int = -1                  # 패리로 막을 수 있는 적 타 수 / 남은 적 타 수 (정보 있음일 때만)
    parry_total: int = -1
    punish: bool | None = None            # 대상 적이 자유로워지기 전에 명중
    chance_lo: int = -1                   # 태세 성공률 (정보가 있으면 lo == hi)
    chance_hi: int = -1
    stance_in_time: bool | None = None    # 가장 빠른 위협보다 태세가 먼저 발동하는가
    escapes: bool | None = None           # 이동으로 장소 지정 공격을 벗어남
    auto_risk_bp: int = -1                # 약공격: 대상 적의 자동 대응 확률 (-1 없음, -2 가능하지만 확률 모름)
    note: str = ""
    line: Timeline = Timeline()           # 이 행동을 고르면 내 타임라인


@dataclass(slots=True, frozen=True)
class DecisionRequest:
    tick: int
    actor: int                            # 결정할 아군 (fighters 인덱스)
    threat: ThreatView | None             # 가장 먼저 닿는 위협
    threats: tuple[ThreatView, ...]
    options: tuple[OptionView, ...]
    rows: tuple[RowView, ...]             # 타임라인 (아군 → 적 순)


def _round(t: int, step: int) -> int:
    return (t + step // 2) // step * step if step > 0 else t


# ------------------------------------------------------------ 적의 공개 정보

def enemy_free_in(b: Battle, e: Combatant) -> int:
    """보이는 경우에만 적이 자유로워지기까지의 틱, 아니면 -1."""
    if e.busy in (HITSTUN, BROKEN, KNOCKDOWN, RETARGET, TURN) or (e.busy in (ATTACK, WAIT) and b.knowledge == "full"):
        return e.free_at - b.now
    return -1


def _threat(b: Battle, e: Combatant, me: Combatant) -> ThreatView | None:
    act = e.action
    pending = e.pending_hits(b.now)
    if not pending or act is None or act.attack is None:
        return None
    a = act.attack
    if a.targeting == "place":
        if me.cell not in act.place_cells:
            return None
    elif act.target != me.index and not (a.area == "ring1" and unit_distance(e, me) == 1):
        return None
    hit_in = act.hit_ticks[pending[0]] - b.now
    approx = _round(hit_in, b.rules.approx_round_t)
    cells = tuple(act.place_cells)
    if b.knowledge == "full":
        return ThreatView(e.index, a.targeting, cells, approx, True, a.name, a.heavy, a.style, a.physical, len(pending), hit_in,
                          e.free_at - b.now, a.weak)
    return ThreatView(e.index, a.targeting, cells, approx, False)


def _simple_line(kind: str, t: int) -> Timeline:
    return Timeline((Segment(kind, 0, t),), free_t=t)


def _attack_segments(start: int, startup: int, active: int, recovery: int, label: str, planned: bool) -> list[Segment]:
    return [Segment("premotion", start, start + startup, True, planned, label),
            Segment("active", start + startup, start + startup + active, True, planned),
            Segment("recovery", start + startup + active, start + startup + active + recovery, True, planned)]


def _current_line(b: Battle, c: Combatant, exact: bool) -> Timeline:
    """지금 하고 있는 행동. exact가 아니면 적 공격은 대략적인 타격 시점까지만."""
    now, act = b.now, c.action
    if c.busy == ATTACK and act is not None and act.attack is not None:
        pending = c.pending_hits(now)
        if exact:
            segs: list[Segment] = []
            if now < act.active_start:
                segs.append(Segment("premotion", 0, act.active_start - now, label=act.attack.name))
            if now < act.active_end:
                segs.append(Segment("active", max(0, act.active_start - now), act.active_end - now))
            segs.append(Segment("recovery", max(0, act.active_end - now), c.free_at - now))
            return Timeline(tuple(segs), tuple(act.hit_ticks[i] - now for i in pending), True, c.free_at - now)
        phase = c.attack_phase(now)
        if pending:
            approx = _round(act.hit_ticks[pending[0]] - now, b.rules.approx_round_t)
            if phase == PREMOTION:
                return Timeline((Segment(phase, 0, approx, False), Segment("recovery", approx, UNKNOWN_END, False)), (approx,), False)
            return Timeline((Segment(phase, 0, UNKNOWN_END, False),), (approx,), False)
        return Timeline((Segment(phase, 0, UNKNOWN_END, False),))
    if c.busy in (HITSTUN, BROKEN, KNOCKDOWN):
        return _simple_line(c.busy, c.free_at - now)
    if c.busy == RETARGET and act is not None:
        turn_left = max(0, act.turn_done - now)
        segs = [Segment("reload", 0, c.free_at - now, label="재로드")]
        if turn_left > 0:
            segs.insert(0, Segment("turn", 0, turn_left, label="방향 전환"))
        return Timeline(tuple(segs), free_t=c.free_at - now)
    if c.busy == TURN:
        return Timeline((Segment("turn", 0, c.free_at - now, label="방향 전환"),), free_t=c.free_at - now)
    kinds = {MOVE: "move", ITEM: "item", SWAP: "swap", HOWL: "item", STANCE: "stance", STANCE_HOLD: "stance", WAIT: "wait"}
    if c.busy in kinds:
        if exact or c.is_ally:
            end = UNKNOWN_END if c.busy == STANCE_HOLD else c.free_at - now
            return Timeline((Segment(kinds[c.busy], 0, end, end != UNKNOWN_END),), free_t=end)
        return Timeline((Segment(kinds[c.busy], 0, UNKNOWN_END, False),))
    return Timeline() if c.is_ally else Timeline((Segment("idle", 0, UNKNOWN_END, False),))


def _enemy_line(b: Battle, e: Combatant) -> Timeline:
    """정보가 있으면 지금 행동 뒤로 반복 패턴을 이어 그린다 (방해받지 않을 때의 예측)."""
    full = b.knowledge == "full"
    cur = _current_line(b, e, full)
    if not full or e.enemy_def is None:
        return cur
    ed = e.enemy_def
    segs = [s for s in cur.segments if s.end_t != UNKNOWN_END]
    t = max((s.end_t for s in segs), default=0)
    pattern = ed.patterns[e.pattern]
    step = e.step
    for _ in range(PROJECT_STEPS):
        segs.append(Segment("idle", t, t + ed.reaction_t, True, True))
        t += ed.reaction_t
        s = pattern[step % len(pattern)]
        step += 1
        if s.kind == "wait":
            segs.append(Segment("wait", t, t + s.wait_t, True, True, "대기"))
            t += s.wait_t
        else:
            a = ed.attack_by_id(s.attack_id)
            su, ac, rc = attack_timing(b.rules, e, a)
            segs.extend(_attack_segments(t, su, ac, rc, a.name, True))
            t += su + ac + rc
    return Timeline(tuple(segs), cur.hits_t, cur.hits_exact, cur.free_t)


def rows(b: Battle) -> tuple[RowView, ...]:
    out: list[RowView] = []
    for c in b.fighters:
        if not c.alive:
            continue
        if c.is_ally:
            out.append(RowView(c.index, c.name, True, _current_line(b, c, True), resting=b.is_resting(c)))
        else:
            left = c.free_at - b.now if c.busy == RETARGET else 0
            out.append(RowView(c.index, c.name, False, _enemy_line(b, c), c.target, left))
    return tuple(out)


# ------------------------------------------------------------ 선택지

def _attack_option(b: Battle, me: Combatant, a: AttackDef, kind: str, e: Combatant, label: str) -> OptionView:
    startup, active, recovery = attack_timing(b.rules, me, a)
    offsets = hit_offsets(b.rules, me, a)
    first = offsets[0]
    before = clash = None
    parry_hits = parry_total = -1
    if b.knowledge == "full":
        pending = e.pending_hits(b.now)
        if pending and e.action is not None and e.action.attack is not None:
            if b.now < e.action.active_start:
                before = a.interrupts and first < e.action.active_start - b.now
            if a.kind == "sword_skill" and e.action.attack.can_clash and e.action.target == me.index:
                parry_hits, parry_total = _parry_match(offsets, [e.action.hit_ticks[i] - b.now for i in pending],
                                                       b.rules.parry.window_t)
                clash = parry_hits == parry_total
    free_in = enemy_free_in(b, e)
    punish = first < free_in if free_in >= 0 else None
    risk = -1
    if a.weak and b.can_auto(e, me):
        risk = b.auto_chance(e, me, a.id) if b.knowledge == "full" else -2
    cd = f"쿨링 {a.cooldown_t // b.rules.ticks_per_frame}f" if a.cooldown_t else ""
    total = startup + active + recovery
    line = Timeline(tuple(_attack_segments(0, startup, active, recovery, a.name, False)), tuple(offsets), True, total)
    return OptionView(ActionChoice(kind, a.id, e.index), label, total, first, before, clash, parry_hits, parry_total, punish,
                      auto_risk_bp=risk,
                      note=cd, line=line)


def _parry_match(mine: list[int], theirs: list[int], window: int) -> tuple[int, int]:
    """내 타격 시점과 적의 남은 타격 시점을 창 안에서 하나씩 짝지어, (막을 수 있는 적 타 수, 적 타 수)."""
    used = [False] * len(mine)
    matched = 0
    for t in theirs:
        for i, m in enumerate(mine):
            if not used[i] and abs(m - t) <= window:
                used[i] = True
                matched += 1
                break
    return matched, len(theirs)


def _stance_line(rule: StanceRule) -> Timeline:
    st, hold = rule.startup_t, rule.startup_t + rule.min_hold_t
    return Timeline((Segment("premotion", 0, st), Segment("stance", st, hold)), free_t=hold)


def build_request(b: Battle, actor: int) -> DecisionRequest:
    me, now, r = b.fighters[actor], b.now, b.rules
    enemies = [e for e in b.enemies if e.alive]
    multi = len(b.enemies) > 1
    threats = [t for t in (_threat(b, e, me) for e in enemies) if t is not None]
    threats.sort(key=lambda t: (t.hit_in_t if t.known else t.approx_hit_in_t, t.attacker))
    threat = threats[0] if threats else None
    opts: list[OptionView] = []

    for e in enemies:
        d = unit_distance(me, e)
        suffix = f" → {e.name}" if multi else ""
        if me.weapon is not None:
            for s in me.sword_skills:
                if s.family == me.weapon_family and me.cooldowns.get(s.id, 0) <= now and in_range(s, d):
                    opts.append(_attack_option(b, me, s, "skill", e, s.name + suffix))
            basic = b.data.actions["basic_attack"]
            if in_range(basic, d):
                opts.append(_attack_option(b, me, basic, "basic", e, basic.name + suffix))
        throw = b.data.actions["throw"]
        if in_range(throw, d):
            opts.append(_attack_option(b, me, throw, "throw", e, throw.name + suffix))

    # 태세 (공격 태그와의 상성 규칙은 미정이라 반영하지 않는다)
    for kind in STANCE_ORDER:
        rule = r.stance(kind)
        if rule.requires_weapon and me.weapon is None and not (kind == "guard" and me.shield is not None):
            continue
        if me.busy == STANCE_HOLD and me.stance is not None and me.stance.kind == kind:
            continue
        if threat is not None and threat.known and threat.weak:
            lo = hi = r.auto.weak_manual_bp
        elif threat is not None and threat.known:
            lo = hi = stance_chance(b.data, me, kind)
        else:
            lo = stance_chance(b.data, me, kind)          # 정보가 없으면 일반 공격일 가능성까지 범위로
            hi = max(lo, r.auto.weak_manual_bp)
        in_time = rule.startup_t <= threat.hit_in_t if threat is not None and threat.known else None
        opts.append(OptionView(ActionChoice("stance", kind), rule.name, rule.startup_t + rule.min_hold_t,
                               chance_lo=lo, chance_hi=hi, stance_in_time=in_time, line=_stance_line(rule)))

    hold = me.busy == STANCE_HOLD and me.stance is not None
    opts.append(OptionView(ActionChoice("wait"), f"{r.stance(me.stance.kind).name} 유지" if hold and me.stance else "기다리기",
                           r.wait_max_t, note="적이 행동을 시작하면 다시 차례가 온다",
                           line=_simple_line("stance" if hold else "wait", r.wait_max_t)))

    if me.shield is not None and me.cooldowns.get("howl", 0) <= now:
        h = r.howl
        effect = f"내 헤이트 +{h.boost}" if h.mode == "boost" else f"다른 공격자 누적 피해 -{h.cut_bp // 100}%"
        opts.append(OptionView(ActionChoice("howl"), "하울", h.startup_t + h.recovery_t, note=f"{h.radius}칸 안의 적 · {effect}",
                               line=Timeline((Segment("premotion", 0, h.startup_t), Segment("recovery", h.startup_t, h.startup_t + h.recovery_t)),
                                             free_t=h.startup_t + h.recovery_t)))

    # 이동 (목적지 칸)
    step_t = move_cell_ticks(r, me)
    for cell, steps in b.reachable(me):
        t = steps * step_t
        escapes = None
        if threat is not None and threat.targeting == "place":
            hit_in = threat.hit_in_t if threat.known else threat.approx_hit_in_t
            escapes = t < hit_in and cell not in threat.place_cells
        opts.append(OptionView(ActionChoice("move", f"{cell[0]},{cell[1]}", steps), f"이동 {cell}", t, escapes=escapes,
                               note=f"{steps}칸", line=_simple_line("move", t)))

    # 아이템
    seen: list[str] = []
    for src, stacks, t in ((0, me.pouch, r.pouch_use_t), (1, me.inventory, r.inventory_use_t)):
        seen.clear()
        for st in stacks:
            if st.count > 0 and st.item_id not in seen:
                seen.append(st.item_id)
                name = b.data.items[st.item_id].name
                opts.append(OptionView(ActionChoice("item", st.item_id, src), f"{name} ({'파우치' if src == 0 else '인벤토리'})", t,
                                       note=f"{st.count}개 · 휴식 회복 가속", line=_simple_line("item", t)))

    # 무기 교체
    swap_t = r.quick_swap_t if me.quick_change else r.swap_t
    for i, ow in enumerate(me.spares):
        opts.append(OptionView(ActionChoice("swap", "", i), f"무기 교체: {ow.weapon.name}", swap_t,
                               note=f"내구도 {ow.durability}/{ow.weapon.durability}", line=_simple_line("swap", swap_t)))

    return DecisionRequest(now, actor, threat, tuple(threats), tuple(opts), rows(b))
