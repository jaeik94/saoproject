"""배치 실행용 아군 봇. 미리보기(DecisionRequest)와 보이는 위치 정보로 고른다.

- perfect: 정보 있음 전제. 예고를 보고 최선의 대응 → 딜레이 캐치·빈틈에만 공격. 대상이 아니면 시야 밖에서 공짜 딜.
- party(단순): 예고에 태세, 빈틈에 공격, 평소엔 일반 공격 위주.
- basic(평타): 일반 공격과 이동만.
- random: 기준선.
"""
from __future__ import annotations

from typing import Protocol

from core.battle import Battle
from core.combatant import Combatant
from core.defs import STANCE_ORDER
from core.formulas import BP, cells_distance, stance_chance, stance_chance_range
from core.hexgrid import Hex, sector
from core.preview import ActionChoice, DecisionRequest, OptionView, ThreatView
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
    대상이 아니면 적의 시야 밖(자동 대응이 없는 방향)에서 공짜로 친다."""

    RETREAT_HP_BP = 4000

    def __init__(self, seed: int) -> None:
        self.seed = seed

    def choose(self, b: Battle, req: DecisionRequest) -> ActionChoice:
        me = b.fighters[req.actor]
        targeted = any(e.alive and e.target == me.index for e in b.enemies)
        if me.weapon is None and _kinds(req, "swap"):
            return _kinds(req, "swap")[0].choice

        threat = req.threat
        if threat is not None:
            return self._defend(b, req, me)

        attacks = _kinds(req, "skill", "basic")
        # 빈틈 (후딜, 경직, 딜레이 캐치, 대기): 가장 센 것부터
        punish = [o for o in attacks if o.punish]
        if punish:
            return max(punish, key=lambda o: (o.choice.kind == "skill", _power(b, o))).choice

        if me.hp * BP < me.max_hp * self.RETREAT_HP_BP and not targeted:
            if b.is_resting(me):
                items = [o for o in _kinds(req, "item") if me.boost_until <= b.now]
                return items[0].choice if items else _wait(req)
            away = [o for o in _kinds(req, "move") if _enemy_dist(b, _cell(o)) >= 2]
            if away:
                return max(away, key=lambda o: (_enemy_dist(b, _cell(o)), -o.free_in_t)).choice

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
                       and s.startup_t + s.hits[0].at_t < b.rules.auto.evade_catch_t for s in me.sword_skills)
            order = ["evade", "guard", "parry"] if fast else ["guard", "parry", "evade"]
            want = next(k for k in order + kinds if k in kinds)
        else:
            want = max(kinds, key=lambda k: (self._score(b, me, k, threat), -STANCE_ORDER.index(k)))
        return _wait(req) if want == current else offered[want].choice

    @staticmethod
    def _score(b: Battle, me: Combatant, kind: str, threat: ThreatView) -> tuple[int, int]:
        """(받는 피해 기대값의 음수, 딜레이 이득 기대값). 피해를 먼저 줄이고, 같으면 이득이 큰 쪽."""
        rule = b.rules.stance(kind)
        if threat.known:
            p = stance_chance(b.data, me, kind, threat.attributes)
        else:
            lo, hi = stance_chance_range(b.data, me, kind, [a.attributes for e in b.enemies if e.enemy_def for a in e.enemy_def.attacks])
            p = (lo + hi) // 2
        taken = (BP - p) * rule.fail_damage_bp + p * rule.chip_bp
        return -taken, p * rule.advantage_t

    @staticmethod
    def _surround_risk(b: Battle, me: Combatant, e: Combatant) -> bool:
        """이 적에게 새로 붙으면 포위 대응 공격을 부르는가 (정보 있음: 적의 포위 조건과 쿨링을 안다)."""
        ed = e.enemy_def
        if ed is None or not ed.surround_attack or e.cooldowns.get(ed.surround_attack, 0) > b.now:
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
                if sector(e.cells(), e.facing, cell) > e.enemy_def.vision and (best is None or o.free_in_t < best[0]):
                    best = (o.free_in_t, o)
        return best[1].choice if best else None


BOTS = {"perfect": PerfectBot, "party": PartyBot, "basic": BasicBot, "random": RandomBot}
