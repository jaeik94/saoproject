"""배치 실행용 아군 봇. 플레이 방식(사람이 할 법한 싸움법)마다 하나씩 두고, 같은 규칙에서 어느 방식이 유리한지 비교한다.

봇은 유저 화면에 보이는 것만 쓴다: 결정 요청(예고·선택지·미리보기), 타임라인 줄(적이 언제 다시 움직이는지 등, 도감이
있으면 패턴까지), 위치·방향·적의 현재 대상, 아군의 상태. 적의 헤이트 수치, 학습 상태, 쿨타임, 난수 결과는 보지 않는다.
턴제라서 보이는 정보 안에서는 프레임까지 계산해 최선을 고를 수 있다.

플레이 방식
- orig(원작형 교대): 방패를 든 사람이 탱커. 탱커는 정면을 지키고, 딜러는 옆·뒤에서 틈에만 친다. 노려진 딜러는 "스위치"를
  부르고, 받을 사람(탱커, 없으면 다른 딜러)이 정면 옆에서 기다리다 들어온다. 딜러는 틈에 정면을 비운다.
- melee(난전형): 예고는 막지만 위치·어그로는 신경 쓰지 않고, 쿨타임이 돌면 바로 가장 센 기술을 쓴다.
- spam(평타 연타형): 예고는 막고, 나머지는 일반 공격만.
- backspam: 원작형과 같지만 딜러가 틈과 관계없이 시야 밖에서 쿨타임마다 소드 스킬 (비교용).
- perfect(각자 솔로형): 각자 혼자 싸우듯 대응한다. 예고 대응, 틈에만 공격, 대상이 아니면 시야 밖에서 친다.
  자기와 아군의 범위 기술을 예상해 위치를 잡는다.
- party(단순)·basic(평타만)·random: 예전 평가 도구용 기준선.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Protocol
from weakref import WeakKeyDictionary

from core.battle import Battle
from core.combatant import ATTACK, MOVE, NO_TARGET, Combatant
from core.defs import STANCE_ORDER
from core.formulas import BP, cells_distance, first_hit_offset, stance_chance
from core.hexgrid import Hex, sector
from core.preview import UNKNOWN_END, ActionChoice, DecisionRequest, OptionView, RowView, ThreatView
from core.rng import Pcg32


class Bot(Protocol):
    def choose(self, b: Battle, req: DecisionRequest) -> ActionChoice: ...


def _power(b: Battle, o: OptionView) -> int:
    total = 0
    for h in b.data.actions[o.choice.ref].hits:
        total += h.power_bp
    return total


def _cell(o: OptionView) -> Hex:
    q, r = o.choice.ref.split(",")
    return (int(q), int(r))


def _enemy_dist(b: Battle, cell: Hex) -> int:
    ds = [cells_distance([cell], e.cells()) for e in b.enemies if e.alive]
    return min(ds) if ds else 0


def _kinds(req: DecisionRequest, *ks: str) -> list[OptionView]:
    return [o for o in req.options if o.choice.kind in ks]


def _wait(req: DecisionRequest) -> ActionChoice:
    return _kinds(req, "wait")[0].choice


def _approach(b: Battle, req: DecisionRequest, me: Combatant) -> ActionChoice | None:
    moves = _kinds(req, "move")
    if not moves:
        return None
    best = min(moves, key=lambda o: (_enemy_dist(b, _cell(o)), o.free_in_t))
    return best.choice if _enemy_dist(b, _cell(best)) < _enemy_dist(b, me.cell) else None


def _row(req: DecisionRequest, index: int) -> RowView | None:
    return next((r for r in req.rows if r.index == index), None)


def _target_of(req: DecisionRequest, e: Combatant) -> int:
    """화면에 보이는 적의 현재 대상 (대상 선)."""
    r = _row(req, e.index)
    return r.target if r is not None else NO_TARGET


def _next_attack_in(req: DecisionRequest, e: Combatant) -> int:
    """적이 다음 공격의 프리모션을 시작하기까지 (타임라인에서 읽음). 모르면 0 (언제든 올 수 있다고 본다)."""
    r = _row(req, e.index)
    if r is None:
        return 0
    for seg in r.line.segments:
        if seg.kind == "premotion" and seg.end_t > 0:
            return max(0, seg.start_t)
        if seg.kind == "idle" and seg.end_t == UNKNOWN_END:
            break
    return max(0, r.line.free_t) if r.line.free_t != UNKNOWN_END else 0


def _front_cells(b: Battle, e: Combatant) -> list[Hex]:
    from core.hexgrid import neighbors
    own = e.cells()
    out: list[Hex] = []
    for c in own:
        for n in neighbors(c):
            if n not in own and n not in out and n in b.arena and sector(own, e.facing, n) == 0:
                out.append(n)
    return out


def _stance_score(b: Battle, o: OptionView) -> int:
    """성공 시 딜레이 이득 - 실패 시 손해 (틱 단위의 대략적인 기대값)."""
    rule = b.rules.stance(o.choice.ref)
    p = (o.chance_lo + o.chance_hi) // 2
    loss = rule.fail_extra_stun_t + rule.fail_damage_bp // 10
    return p * rule.advantage_t - (BP - p) * loss


class RandomBot:
    """기준선: 가능한 행동 중 무작위."""

    def __init__(self, seed: int) -> None:
        self.rng = Pcg32.seeded(seed, 101)

    def choose(self, b: Battle, req: DecisionRequest) -> ActionChoice:
        return req.options[self.rng.below(len(req.options))].choice


class BasicBot:
    """평타 봇: 일반 공격과 이동만 한다 (태세·스킬 없음)."""

    def __init__(self, seed: int) -> None:
        self.seed = seed

    def choose(self, b: Battle, req: DecisionRequest) -> ActionChoice:
        me = b.fighters[req.actor]
        basics = _kinds(req, "basic")
        if basics:
            return min(basics, key=lambda o: (b.fighters[o.choice.arg].hp, o.choice.arg)).choice
        return _approach(b, req, me) or _wait(req)


class PartyBot:
    """단순 봇: 예고에는 태세·이동으로, 빈틈에는 공격으로, 평소엔 일반 공격 위주. 체력이 낮고 노려지지 않으면 뒤로 빠져 쉰다."""

    RETREAT_HP_BP = 4000
    SKILL_WHEN_NEUTRAL_BP = 3500
    CLASH_TRY_BP = 5000

    def __init__(self, seed: int) -> None:
        self.rng = Pcg32.seeded(seed, 102)

    def choose(self, b: Battle, req: DecisionRequest) -> ActionChoice:
        me = b.fighters[req.actor]
        targeted = any(e.alive and e.target == me.index for e in b.enemies)
        if me.weapon is None and _kinds(req, "swap"):
            return _kinds(req, "swap")[0].choice

        threat = req.threat
        if threat is not None:
            esc = [o for o in _kinds(req, "move") if o.escapes]
            if esc:
                return min(esc, key=lambda o: o.free_in_t).choice
            clash = [o for o in _kinds(req, "skill") if o.clash_timing]
            if clash and self.rng.roll_bp(self.CLASH_TRY_BP):
                return max(clash, key=lambda o: _power(b, o)).choice
            cut = [o for o in _kinds(req, "skill") if o.before_enemy_hit]
            if cut:
                return min(cut, key=lambda o: o.first_hit_in_t).choice
            stances = [o for o in _kinds(req, "stance") if o.stance_in_time is not False]
            if stances:
                return max(stances, key=lambda o: _stance_score(b, o)).choice
            return _wait(req)

        if me.hp * BP < me.max_hp * self.RETREAT_HP_BP and not targeted:
            if b.is_resting(me):
                items = [o for o in _kinds(req, "item") if me.boost_until <= b.now]
                return items[0].choice if items else _wait(req)
            away = [o for o in _kinds(req, "move") if _enemy_dist(b, _cell(o)) >= 2]
            if away:
                return max(away, key=lambda o: (_enemy_dist(b, _cell(o)), -o.free_in_t)).choice

        attacks = _kinds(req, "skill", "basic")
        punish = [o for o in attacks if o.punish]
        if punish:
            return max(punish, key=lambda o: _power(b, o)).choice
        if attacks:
            weakest = min((b.fighters[o.choice.arg] for o in attacks), key=lambda e: (e.hp, e.index))
            mine = [o for o in attacks if o.choice.arg == weakest.index]
            skills = [o for o in mine if o.choice.kind == "skill"]
            if skills and self.rng.roll_bp(self.SKILL_WHEN_NEUTRAL_BP):
                return max(skills, key=lambda o: _power(b, o)).choice
            basics = [o for o in mine if o.choice.kind == "basic"]
            return (basics or mine)[0].choice
        return _approach(b, req, me) or _wait(req)


class PerfectBot:
    """완벽 대처 봇 (정보 있음 전제). 대상일 때는 먼저 치지 않고 대응 → 딜레이 캐치·빈틈에만 공격한다.
    대상이 아니면 적의 시야 밖(자동 대응이 없는 방향)에서 공짜로 친다.
    대기 중인 적은 자동 대응이 켜져 있으므로, 그때의 일반 공격은 가끔만 넣는다 (임시 규칙)."""

    RETREAT_HP_BP = 4000
    WAIT_POKE_BP = 3000

    def __init__(self, seed: int) -> None:
        self.rng = Pcg32.seeded(seed, 103)

    def choose(self, b: Battle, req: DecisionRequest) -> ActionChoice:
        me = b.fighters[req.actor]
        req = self._safe(b, req)
        targeted = any(e.alive and e.target == me.index for e in b.enemies)
        if me.weapon is None and _kinds(req, "swap"):
            return _kinds(req, "swap")[0].choice

        threat = req.threat
        if threat is not None:
            return self._defend(b, req, me)

        attacks = _kinds(req, "skill", "basic")
        # 빈틈 (후딜, 경직, 딜레이 캐치, 대기): 가장 센 것부터
        punish = [o for o in attacks if o.punish]
        if any(o.choice.kind == "basic" and o.auto_risk_bp != -1 for o in punish) and not self.rng.roll_bp(self.WAIT_POKE_BP):
            punish = [o for o in punish if not (o.choice.kind == "basic" and o.auto_risk_bp != -1)]
        if punish:
            return max(punish, key=lambda o: (o.choice.kind == "skill", _power(b, o))).choice

        if me.hp * BP < me.max_hp * self.RETREAT_HP_BP and not targeted:
            if b.is_resting(me):
                items = [o for o in _kinds(req, "item") if me.boost_until <= b.now]
                return items[0].choice if items else _wait(req)
            away = [o for o in _kinds(req, "move") if _enemy_dist(b, _cell(o)) >= 2]
            if away:
                return max(away, key=lambda o: (_enemy_dist(b, _cell(o)), -o.free_in_t)).choice

        spot = self._reposition(b, req, me)
        if spot is not None:
            return spot

        if not targeted:
            # 대상이 아니면 공짜 딜. 자동 대응 위험이 있으면 시야 밖으로 돌아간다
            safe = [o for o in attacks if o.choice.kind == "basic" and o.auto_risk_bp == -1]
            if safe:
                return min(safe, key=lambda o: (b.fighters[o.choice.arg].hp, o.choice.arg)).choice
            flank = self._flank(b, req, me)
            if flank is not None:
                return flank
            skills = [o for o in attacks if o.choice.kind == "skill"]
            if skills:
                return max(skills, key=lambda o: _power(b, o)).choice
        if attacks:
            return _wait(req)     # 대상일 때는 먼저 찌르지 않는다 (예고를 기다렸다가 대응)
        if not targeted and any(e.alive and self._surround_risk(b, me, e) for e in b.enemies):
            return self._flank(b, req, me) or _wait(req)   # 세 번째로 붙어 포위 공격을 부르지 않는다
        return self._flank(b, req, me) or _approach(b, req, me) or _wait(req)

    def _defend(self, b: Battle, req: DecisionRequest, me: Combatant) -> ActionChoice:
        threat = req.threat
        assert threat is not None
        esc = [o for o in _kinds(req, "move") if o.escapes]
        if esc:
            return min(esc, key=lambda o: o.free_in_t).choice
        hit_in = threat.hit_in_t if threat.known else threat.approx_hit_in_t
        # 프리모션을 소드 스킬로 끊을 수 있으면 끊는다
        cut = [o for o in _kinds(req, "skill") if o.before_enemy_hit and o.choice.arg == threat.attacker]
        if cut:
            return max(cut, key=lambda o: _power(b, o)).choice
        current = me.stance.kind if me.stance is not None and me.busy == "stance_hold" else ""
        offered = {o.choice.ref: o for o in _kinds(req, "stance") if o.stance_in_time is not False}
        kinds = [k for k in STANCE_ORDER if k in offered or k == current]
        if not kinds:
            return _wait(req)
        if threat.weak and threat.known:
            # 약공격은 대응하면 거의 확정: 회피 딜캐에 들어갈 빠른 스킬이 있으면 회피, 아니면 가드
            fast = any(s.family == me.weapon_family and me.cooldowns.get(s.id, 0) <= b.now + hit_in
                       and first_hit_offset(b.rules, me, s) < b.rules.auto.evade_catch_t for s in me.sword_skills)
            order = ["evade", "guard"] if fast else ["guard", "evade"]
            want = next(k for k in order + kinds if k in kinds)
        else:
            want = max(kinds, key=lambda k: (self._score(b, me, k, threat), -STANCE_ORDER.index(k)))
        return _wait(req) if want == current else offered[want].choice

    @staticmethod
    def _score(b: Battle, me: Combatant, kind: str, threat: ThreatView) -> tuple[int, int]:
        """(받는 피해 기대값의 음수, 딜레이 이득 기대값). 피해를 먼저 줄이고, 같으면 이득이 큰 쪽."""
        rule = b.rules.stance(kind)
        e_act = b.fighters[threat.attacker].action
        p = stance_chance(b.data, me, kind, 0, e_act.attack if threat.known and e_act is not None else None)
        taken = (BP - p) * rule.fail_damage_bp + p * rule.chip_bp
        return -taken, p * rule.advantage_t

    @staticmethod
    def _surround_risk(b: Battle, me: Combatant, e: Combatant) -> bool:
        """이 적에게 새로 붙으면 포위 대응 공격을 부르는가 (도감으로 포위 조건을 안다. 적의 쿨타임은 보이지 않는다)."""
        ed = e.enemy_def
        if ed is None or not ed.surround_attack or b.knowledge != "full":
            return False
        others = sum(1 for a in b.allies if a.alive and a is not me and cells_distance([a.cell], e.cells()) == 1)
        return others + 1 >= ed.surround_count

    def _flank(self, b: Battle, req: DecisionRequest, me: Combatant) -> ActionChoice | None:
        """적에게 인접하면서 그 적의 자동 대응 시야 밖인 칸으로 이동 (포위 공격을 부르는 칸은 피한다)."""
        best: tuple[int, OptionView] | None = None
        for o in _kinds(req, "move"):
            cell = _cell(o)
            for e in b.enemies:
                if not e.alive or e.enemy_def is None or cells_distance([cell], e.cells()) != 1:
                    continue
                if cells_distance([me.cell], e.cells()) != 1 and self._surround_risk(b, me, e):
                    continue
                if not self._good_spot(b, me, cell):
                    continue
                if sector(e.cells(), e.facing, cell) > e.enemy_def.vision and (best is None or o.free_in_t < best[0]):
                    best = (o.free_in_t, o)
        return best[1].choice if best else None


    # ------------------------------------------------------------ 범위 기술과 위치
    @staticmethod
    def _pos(c: Combatant) -> Hex:
        return c.action.dest if c.busy == MOVE and c.action is not None else c.cell

    @staticmethod
    def _area_skills(c: Combatant) -> list:
        return [s for s in c.sword_skills if s.family == c.weapon_family and s.area != "single"]

    def _hits_ally(self, b: Battle, me: Combatant, skill_id: str, target: int, cell: Hex) -> bool:
        area = b.area_from(me, b.data.actions[skill_id], target, cell)
        return any(self._pos(o) in area for o in b.allies if o is not me and o.alive)

    def _danger(self, b: Battle, me: Combatant) -> set[Hex]:
        """다른 아군의 범위 기술이 닿을 칸 (지금 쓰는 중인 것 + 인접한 적에게 쓸 수 있는 것)."""
        out: set[Hex] = set()
        for o in b.allies:
            if o is me or not o.alive:
                continue
            act = o.action
            if o.busy == ATTACK and act is not None and act.attack is not None and act.attack.area != "single" and b.now < act.active_end:
                out.update(b.area_from(o, act.attack, act.target, o.cell))
            pos = self._pos(o)
            for s in self._area_skills(o):
                for e in b.enemies:
                    if e.alive and s.range_min <= cells_distance([pos], e.cells()) <= s.range_max:
                        out.update(b.area_from(o, s, e.index, pos))
        return out

    def _good_spot(self, b: Battle, me: Combatant, cell: Hex) -> bool:
        """아군 범위 밖이고, 내 범위 기술이 아군을 맞히지 않는 칸."""
        if cell in self._danger(b, me):
            return False
        for s in self._area_skills(me):
            for e in b.enemies:
                if e.alive and s.range_min <= cells_distance([cell], e.cells()) <= s.range_max and self._hits_ally(b, me, s.id, e.index, cell):
                    return False
        return True

    def _safe(self, b: Battle, req: DecisionRequest) -> DecisionRequest:
        """범위에 아군이 드는 기술은 고르지 않는다."""
        me = b.fighters[req.actor]
        keep = tuple(o for o in req.options
                     if not (o.choice.kind == "skill" and b.data.actions[o.choice.ref].area != "single"
                             and self._hits_ally(b, me, o.choice.ref, o.choice.arg, me.cell)))
        return req if len(keep) == len(req.options) else replace(req, options=keep)

    def _reposition(self, b: Battle, req: DecisionRequest, me: Combatant) -> ActionChoice | None:
        """아군 범위 안에 서 있거나 내 범위 기술이 아군 때문에 막혀 있으면, 적에게 붙은 채로 괜찮은 칸으로 옮긴다."""
        if _enemy_dist(b, me.cell) != 1 or self._good_spot(b, me, me.cell):
            return None
        spots = [o for o in _kinds(req, "move") if _enemy_dist(b, _cell(o)) == 1 and self._good_spot(b, me, _cell(o))]
        if not spots:
            return None
        return min(spots, key=lambda o: (o.free_in_t, _cell(o)[1], _cell(o)[0])).choice


class OrigBot(PerfectBot):
    """원작형 교대. 스위치는 "부르고 받는" 약속이다: 노려진 딜러가 부르고, 받을 사람이 정면 옆에서 기다리다 들어온다.
    받을 사람은 방패를 든 탱커, 탱커가 없으면 노려지지 않은 딜러 중 적에게 가장 가까운 사람."""

    _calls: "WeakKeyDictionary[Battle, dict[int, int]]" = WeakKeyDictionary()   # 전투별: 적 → 스위치를 부른 아군
    WAIT_POKE_BP = 0          # 대기 중인 적에게 일반 공격을 찌르지 않는다 (자동 대응 → 반격당함)

    def choose(self, b: Battle, req: DecisionRequest) -> ActionChoice:
        me = b.fighters[req.actor]
        req = self._safe(b, req)
        if req.threat is not None or (me.weapon is None and _kinds(req, "swap")):
            return super().choose(b, req)
        calls = self._calls.setdefault(b, {})
        for ei in list(calls):
            e = b.fighters[ei]
            if not e.alive or _target_of(req, e) != calls[ei]:
                del calls[ei]                      # 넘겨받았거나 대상이 바뀌었다
        enemies = [e for e in b.enemies if e.alive]
        if not enemies:
            return _wait(req)
        first = self._first_touch(b, req, me, enemies)
        if first is not None:
            return first
        req = self._leave_first_touch(b, req, me, enemies)
        chasing = [e for e in enemies if _target_of(req, e) == me.index]
        if chasing:
            if me.shield is not None:
                return super().choose(b, req)      # 탱커는 노려지면 정면을 지키며 대응
            return self._hand_over(b, req, me, chasing[0], calls)
        called = [e for e in enemies if e.index in calls and self._taker(b, req, e) is me]
        if called:
            return self._take(b, req, me, called[0])
        if me.shield is not None:
            return self._guard_post(b, req, me)
        return self._strike(b, req, me)

    # ------------------------------------------------------------ 첫 타격 (규칙을 아는 봇)
    # 대상이 없는 적(무력화에서 일어나 수색 중, 또는 아직 건드리지 않은 비선공)은 처음 피해를 준 아군을 대상으로 삼는다.
    # 그래서 첫 타격은 탱커가 넣는다. 다운 중에는 모두 극딜하되, 딜러는 기상 뒤에 맞을 공격을 쓰지 않는다.
    @staticmethod
    def _down_left(req: DecisionRequest, e: Combatant) -> int:
        """적이 무력화(다운)에서 일어나기까지 (타임라인에서 읽음). 다운 중이 아니면 0."""
        r = _row(req, e.index)
        if r is None or not r.line.segments:
            return 0
        s0 = r.line.segments[0]
        return s0.end_t if s0.kind == "broken" and s0.end_t != UNKNOWN_END else 0

    def _untargeted(self, req: DecisionRequest, e: Combatant) -> bool:
        return _target_of(req, e) == NO_TARGET and self._down_left(req, e) == 0

    def _tank_alive(self, b: Battle) -> bool:
        return any(a.alive and a.shield is not None for a in b.allies)

    def _first_touch(self, b: Battle, req: DecisionRequest, me: Combatant, enemies: list[Combatant]) -> ActionChoice | None:
        """탱커: 대상이 없는 적에게 가장 먼저 맞는 공격을 넣는다. 다운 중인 적에게는 기상 전에 자유로워지는 공격만."""
        if me.shield is None:
            return None
        loose = [e for e in enemies if self._untargeted(req, e)]
        if loose:
            ids = [e.index for e in loose]
            opts = [o for o in _kinds(req, "skill", "basic") if o.choice.arg in ids]
            if opts:
                return min(opts, key=lambda o: (o.first_hit_in_t, -_power(b, o), o.choice.ref)).choice
            return self._stage(b, req, me, loose[0]) or _approach(b, req, me)
        downs = [e for e in enemies if self._down_left(req, e) > 0]
        for e in downs:
            left = self._down_left(req, e)
            opts = [o for o in _kinds(req, "skill", "basic") if o.choice.arg == e.index and o.free_in_t <= left]
            if opts:
                return max(opts, key=lambda o: (_power(b, o), o.choice.ref)).choice
            if cells_distance([me.cell], e.cells()) == 1:
                return _wait(req)              # 기상 직후 첫 타를 넣을 수 있게 비워 둔다
        return None

    def _leave_first_touch(self, b: Battle, req: DecisionRequest, me: Combatant, enemies: list[Combatant]) -> DecisionRequest:
        """딜러: 탱커가 있으면, 대상이 없는 적은 치지 않고, 다운 중인 적에게는 기상 전에 다 맞는 공격만 쓴다."""
        if me.shield is not None or not self._tank_alive(b):
            return req
        loose = {e.index for e in enemies if self._untargeted(req, e)}
        downs = {e.index: self._down_left(req, e) for e in enemies if self._down_left(req, e) > 0}

        def ok(o: OptionView) -> bool:
            if o.choice.kind not in ("skill", "basic", "throw"):
                return True
            if o.choice.arg in loose:
                return False
            left = downs.get(o.choice.arg)
            return left is None or all(t < left for t in o.line.hits_t)
        keep = tuple(o for o in req.options if ok(o))
        return req if len(keep) == len(req.options) else replace(req, options=keep)

    # ------------------------------------------------------------ 역할
    def _taker(self, b: Battle, req: DecisionRequest, e: Combatant) -> Combatant | None:
        """스위치를 받을 사람: 탱커, 없으면 노려지지 않은 딜러 중 가장 가까운 사람."""
        cands = [a for a in b.allies if a.alive and _target_of(req, e) != a.index]
        tanks = [a for a in cands if a.shield is not None]
        pool = tanks or cands
        if not pool:
            return None
        return min(pool, key=lambda a: (cells_distance([self._pos(a)], e.cells()), a.index))

    def _hand_over(self, b: Battle, req: DecisionRequest, me: Combatant, e: Combatant, calls: dict[int, int]) -> ActionChoice:
        """노려진 딜러: 스위치를 부르고, 받을 사람이 적 옆에 와 있으면 적이 다음 공격을 시작하기 전에 정면을 비운다."""
        calls.setdefault(e.index, me.index)
        taker = self._taker(b, req, e)
        in_front = cells_distance([me.cell], e.cells()) == 1 and sector(e.cells(), e.facing, me.cell) == 0
        ready = taker is not None and cells_distance([self._pos(taker)], e.cells()) == 1
        if in_front and ready:
            window = _next_attack_in(req, e)
            cands = [o for o in _kinds(req, "move")
                     if o.free_in_t <= window and self._good_spot(b, me, _cell(o))
                     and (cells_distance([_cell(o)], e.cells()) > 1 or sector(e.cells(), e.facing, _cell(o)) > 0)]
            if cands:
                def key(o: OptionView) -> tuple[int, int, int, int]:
                    cell = _cell(o)
                    sec = sector(e.cells(), e.facing, cell) if cells_distance([cell], e.cells()) == 1 else 3
                    return (-sec, o.free_in_t, cell[1], cell[0])   # 시야 밖일수록, 빨리 갈수록
                return min(cands, key=key).choice
        return super().choose(b, req)

    def _take(self, b: Battle, req: DecisionRequest, me: Combatant, e: Combatant) -> ActionChoice:
        """받을 사람: 정면이 비어 있으면 들어가고, 아니면 정면 옆(시야 안)에서 기다린다. 기다리는 동안 틈에는 친다."""
        fronts = _front_cells(b, e)
        if me.cell in fronts:
            return self._hold(b, req, me)
        into = [o for o in _kinds(req, "move") if _cell(o) in fronts and self._good_spot(b, me, _cell(o))]
        if into:
            return min(into, key=lambda o: (o.free_in_t, _cell(o)[1], _cell(o)[0])).choice
        return self._stage(b, req, me, e) or self._hold(b, req, me)

    def _guard_post(self, b: Battle, req: DecisionRequest, me: Combatant) -> ActionChoice:
        """노려지지 않은 탱커: 적의 시야 안, 정면 옆에 붙어 있다가 틈에 친다."""
        e = min((x for x in b.enemies if x.alive), key=lambda x: (cells_distance([me.cell], x.cells()), x.index))
        fronts = _front_cells(b, e)
        if me.cell in fronts or self._seen(e, me.cell):
            return self._hold(b, req, me)
        return self._stage(b, req, me, e) or _approach(b, req, me) or _wait(req)

    def _strike(self, b: Battle, req: DecisionRequest, me: Combatant) -> ActionChoice:
        """노려지지 않은 딜러: 틈에 가장 센 것. 시야 안이면 시야 밖으로 옮겨 다음 틈을 기다린다."""
        attacks = _kinds(req, "skill", "basic")
        punish = [o for o in attacks if o.punish]
        if punish:
            return max(punish, key=lambda o: (o.choice.kind == "skill", _power(b, o))).choice
        if me.hp * BP < me.max_hp * self.RETREAT_HP_BP:
            return super().choose(b, req)          # 빠져서 쉰다
        if any(e.alive and e.enemy_def is not None and self._seen(e, me.cell) for e in b.enemies):
            flank = self._flank(b, req, me)
            if flank is not None:
                return flank
        if attacks:
            return _wait(req)                      # 틈이 아니면 치지 않는다 (일반 공격도 거의 쓰지 않음)
        return self._flank(b, req, me) or _approach(b, req, me) or _wait(req)

    def _hold(self, b: Battle, req: DecisionRequest, me: Combatant) -> ActionChoice:
        """자리를 지키며 틈에만 친다."""
        punish = [o for o in _kinds(req, "skill", "basic") if o.punish]
        if punish:
            return max(punish, key=lambda o: (o.choice.kind == "skill", _power(b, o))).choice
        return _wait(req)

    def _stage(self, b: Battle, req: DecisionRequest, me: Combatant, e: Combatant) -> ActionChoice | None:
        """정면 옆(정면 칸에 붙은, 적의 시야 안 칸)으로. 없으면 시야 안 아무 칸."""
        fronts = _front_cells(b, e)
        from core.hexgrid import dist
        if self._seen(e, me.cell) and min((dist(me.cell, f) for f in fronts), default=9) <= 1:
            return None                            # 이미 정면 옆에 있다
        cands = [o for o in _kinds(req, "move") if self._seen(e, _cell(o)) and self._good_spot(b, me, _cell(o))]
        if not cands:
            return None
        return min(cands, key=lambda o: (min((dist(_cell(o), f) for f in fronts), default=9), o.free_in_t,
                                         _cell(o)[1], _cell(o)[0])).choice

    @staticmethod
    def _seen(e: Combatant, cell: Hex) -> bool:
        """적에게 붙은 칸이 그 적의 시야 안인가 (시야 범위는 도감 정보)."""
        assert e.enemy_def is not None
        return cells_distance([cell], e.cells()) == 1 and sector(e.cells(), e.facing, cell) <= e.enemy_def.vision

    # ------------------------------------------------------------ 범위 기술과 자리 (원작형의 약속)
    # 1. 범위 기술을 쓰는 사람이 먼저, 자기 범위에 아군이 들지 않는 칸으로 옮긴다.
    # 2. 그런 칸이 없으면 다른 딜러들이 그 사람의 범위에서 비켜선다. 탱커는 범위 기술 때문에 자리를 옮기지 않는다.
    # 3. 비킬 칸도 없으면 아무도 옮기지 않고, 범위 기술은 쓰지 않는다 (_safe가 거른다).
    def _aoe_clear_cell_exists(self, b: Battle, u: Combatant) -> bool:
        """u가 범위 기술을 쓸 수 있는 칸(적에게 붙은 빈칸, 범위에 다른 아군 없음)이 하나라도 있는가."""
        skills = self._area_skills(u)
        if not skills:
            return True
        from core.hexgrid import neighbors
        others = [self._pos(a) for a in b.allies if a.alive and a is not u]
        for e in b.enemies:
            if not e.alive:
                continue
            for c in {n for x in e.cells() for n in neighbors(x)}:
                if c not in b.arena or c in e.cells() or (b.occupant(c) not in (None, u)) or c in others:
                    continue
                for s in skills:
                    if s.range_min <= cells_distance([c], e.cells()) <= s.range_max and \
                            not any(o in b.area_from(u, s, e.index, c) for o in others):
                        return True
        return False

    def _danger(self, b: Battle, me: Combatant) -> set[Hex]:
        """비켜서야 하는 칸: 탱커는 없음. 딜러는 '스스로 옮길 칸이 없는' 범위 기술 사용자의 범위만 (그 사람이 지금 쓰는 중인 범위 포함)."""
        if me.shield is not None:
            return set()
        out: set[Hex] = set()
        for u in b.allies:
            if u is me or not u.alive or u.shield is not None:
                continue
            act = u.action
            if u.busy == ATTACK and act is not None and act.attack is not None and act.attack.area != "single" and b.now < act.active_end:
                out.update(b.area_from(u, act.attack, act.target, u.cell))
            if self._aoe_clear_cell_exists(b, u):
                continue
            pos = self._pos(u)
            for s in self._area_skills(u):
                for e in b.enemies:
                    if e.alive and s.range_min <= cells_distance([pos], e.cells()) <= s.range_max:
                        out.update(b.area_from(u, s, e.index, pos))
        return out


class MeleeBot(PerfectBot):
    """난전형: 예고는 막지만 위치·어그로·범위는 신경 쓰지 않고, 쿨타임이 돌면 바로 가장 센 기술."""

    def choose(self, b: Battle, req: DecisionRequest) -> ActionChoice:
        me = b.fighters[req.actor]
        if me.weapon is None and _kinds(req, "swap"):
            return _kinds(req, "swap")[0].choice
        if req.threat is not None:
            return self._defend(b, req, me)
        skills = _kinds(req, "skill")
        if skills:
            return max(skills, key=lambda o: (_power(b, o), -o.choice.arg)).choice
        basics = _kinds(req, "basic")
        if basics:
            return min(basics, key=lambda o: (b.fighters[o.choice.arg].hp, o.choice.arg)).choice
        return _approach(b, req, me) or _wait(req)


class SpamBot(PerfectBot):
    """평타 연타형: 예고는 막고, 나머지는 일반 공격만."""

    def choose(self, b: Battle, req: DecisionRequest) -> ActionChoice:
        me = b.fighters[req.actor]
        if me.weapon is None and _kinds(req, "swap"):
            return _kinds(req, "swap")[0].choice
        if req.threat is not None:
            return self._defend(b, req, me)
        basics = _kinds(req, "basic")
        if basics:
            return min(basics, key=lambda o: (b.fighters[o.choice.arg].hp, o.choice.arg)).choice
        return _approach(b, req, me) or _wait(req)


class BackSpamBot(OrigBot):
    """원작형 교대와 같지만, 노려지지 않은 딜러가 틈과 관계없이 시야 밖에서 쿨타임마다 가장 센 소드 스킬을 쓴다.
    "틈에만 치기"와 비교해 강한 헤이트 규칙이 뒤에서 아무 때나 치는 것을 손해로 만드는지 확인하는 용도."""

    def _strike(self, b: Battle, req: DecisionRequest, me: Combatant) -> ActionChoice:
        skills = _kinds(req, "skill")
        if skills and not any(e.alive and e.enemy_def is not None and self._seen(e, me.cell) for e in b.enemies):
            return max(skills, key=lambda o: (_power(b, o), o.choice.ref)).choice
        return super()._strike(b, req, me)


BOTS = {"orig": OrigBot, "backspam": BackSpamBot, "melee": MeleeBot, "spam": SpamBot, "perfect": PerfectBot,
        "party": PartyBot, "basic": BasicBot, "random": RandomBot}
