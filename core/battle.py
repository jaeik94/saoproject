"""파티 전투 엔진 (아군 N 대 적 M, 육각 그리드). 이벤트 큐 기반 타임라인 턴제.

사용법:
    battle = Battle(data, party_specs, enemy_def, enemy_count, seed)
    while (req := battle.advance()) is not None:   # 아군 한 명의 결정이 필요할 때까지 진행
        battle.submit(req.options[i].choice)       # req.actor가 결정할 아군
    battle.result

같은 틱의 이벤트 처리 순서: 타격 → 행동 종료 → 휴식 회복 → 적 결정 → 아군 결정(민첩 높은 순), 같은 우선순위는 등록 순서.
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field

from core import log as L
from core.ai import EnemyBrain
from core.combatant import (
    ACTIVE, ATTACK, BROKEN, DEAD, HITSTUN, HOWL, ITEM, KNOCKDOWN, MOVE, NO_TARGET, PREMOTION, READY, RETARGET,
    STANCE, STANCE_HOLD, SWAP, TURN, WAIT, WAKEABLE, ActionState, Combatant, OwnedWeapon, StanceState,
)
from core.defs import STANCE_ORDER, AttackDef, EnemyDef, GameData, HitDef
from core.formulas import (
    BP, attack_timing, cells_distance, clash_power, damage, first_hit_offset, getup_ticks, hit_offsets, hitstun,
    in_range, move_cell_ticks, raw_hit_power, stance_chance, turn_ticks, unit_distance,
)
from core.hexgrid import BACK, BACK_SIDE, DIRS, FRONT, Hex, add, direction_toward, disc, dist, neighbors, sector, shape_cells
from core.loadout import ItemStack, PlayerSpec
from core.preview import ActionChoice, DecisionRequest, build_request
from core.rng import Pcg32

PRIO_HIT, PRIO_FREE, PRIO_REST, PRIO_AI, PRIO_ALLY = 0, 1, 2, 3, 10
EV_HIT, EV_HOWL, EV_FREE, EV_REST, EV_AI, EV_ALLY = "hit", "howl", "free", "rest", "ai", "ally"
KNOWLEDGE_LEVELS = ("none", "full")
ENEMY_IDLE_T_FRAMES = 10


class SetupError(Exception):
    pass


@dataclass(slots=True, order=True)
class _Event:
    tick: int
    prio: int
    seq: int
    kind: str = field(compare=False)
    actor: int = field(compare=False)
    a: int = field(compare=False, default=0)
    b: int = field(compare=False, default=0)


class Battle:
    def __init__(self, data: GameData, party: tuple[PlayerSpec, ...] | list[PlayerSpec], enemy: EnemyDef,
                 enemy_count: int, seed: int, knowledge: str = "none") -> None:
        if knowledge not in KNOWLEDGE_LEVELS:
            raise ValueError(f"knowledge는 {KNOWLEDGE_LEVELS} 중 하나")
        if not party or enemy_count < 1:
            raise SetupError("아군과 적이 최소 1명씩 필요합니다")
        self.data = data
        self.rules = data.rules
        self.seed = seed
        self.knowledge = knowledge
        self.now = 0
        self.log: list[L.LogEvent] = []
        self.result: L.BattleResult | None = None
        self._queue: list[_Event] = []
        self._seq = 0
        self._request: DecisionRequest | None = None
        self.rng = Pcg32.seeded(seed, 1)
        self.arena: set[Hex] = set(disc((0, 0), self.rules.arena_radius))  # 포함 여부만 확인
        self.fighters: list[Combatant] = []
        for i, spec in enumerate(party):
            if i >= len(self.rules.ally_spawns):
                raise SetupError("rules.arena.ally_spawns가 부족합니다")
            self.fighters.append(self._make_ally(i, spec))
        order = sorted(range(len(self.fighters)), key=lambda i: (-self.fighters[i].agi, i))
        for rank, i in enumerate(order):
            self.fighters[i].rank = rank
        self.allies = list(self.fighters)
        self.enemies: list[Combatant] = []
        self.brains: list[EnemyBrain | None] = [None] * len(self.allies)
        spawns = iter(self.rules.enemy_spawns)
        for k in range(enemy_count):
            e = self._make_enemy(len(self.fighters), k, enemy)
            for anchor in spawns:
                cells = [add(anchor, o) for o in e.footprint]
                if all(c in self.arena and self.occupant(c) is None for c in cells):
                    e.cell = anchor
                    break
            else:
                raise SetupError(f"{enemy.name} {enemy_count}마리를 둘 자리가 없습니다 (rules.arena.enemy_spawns)")
            self.fighters.append(e)
            self.enemies.append(e)
            self.brains.append(EnemyBrain(enemy, self.rules))
        for e in self.enemies:
            t = self._nearest_ally(e)
            e.facing = direction_toward(e.cells(), t.cell)
            e.provoked = not enemy.passive
            e.target = t.index if e.provoked else NO_TARGET
            e.pattern = self.rng.below(len(enemy.patterns))
            self.brain(e).load(self._key(t), t.index, 0)
            self._schedule_decision(e, 0)
        for a in self.allies:
            self.facing_of(a)
            self._schedule_decision(a, 0)
        self._push(self.rules.rest_interval_t, PRIO_REST, EV_REST, 0)

    # ------------------------------------------------------------ 생성
    def _make_ally(self, i: int, spec: PlayerSpec) -> Combatant:
        c = Combatant(
            index=i, cid=f"ally{i}", name=spec.name, is_ally=True, size="player",
            max_hp=spec.max_hp, hp=spec.max_hp, cell=self.rules.ally_spawns[i], footprint=((0, 0),),
            str_=spec.str_, agi=spec.agi, base_power=0, defense=spec.armor.defense if spec.armor else 0, clash_bonus_bp=0,
        )
        c.weapon = OwnedWeapon(spec.weapon, spec.weapon.durability) if spec.weapon else None
        c.spares = [OwnedWeapon(w, w.durability) for w in spec.spare_weapons]
        c.armor, c.armor_dur = spec.armor, spec.armor.durability if spec.armor else 0
        c.shield, c.shield_dur = spec.shield, spec.shield.durability if spec.shield else 0
        c.pouch = [ItemStack(i, n) for i, n in spec.pouch]
        c.inventory = [ItemStack(i, n) for i, n in spec.inventory]
        c.sword_skills = spec.sword_skills
        c.slot_skills = spec.slot_skills
        c.quick_change = spec.quick_change
        c.carry_weight = spec.carry_weight
        return c

    def _make_enemy(self, index: int, k: int, e: EnemyDef) -> Combatant:
        return Combatant(
            index=index, cid=f"enemy{k}", name=e.name if k == 0 else f"{e.name} {k + 1}", is_ally=False, size=e.size,
            max_hp=e.hp, hp=e.hp, cell=(0, 0), footprint=e.footprint, str_=0, agi=0,
            base_power=e.attack, defense=e.defense, clash_bonus_bp=e.clash_bonus_bp, enemy_def=e,
        )

    # ------------------------------------------------------------ 공개 API
    def advance(self) -> DecisionRequest | None:
        """아군 결정이 필요하면 요청을, 전투가 끝났으면 None을 돌려준다."""
        while self.result is None:
            if self._request is not None:
                return self._request
            if not self._queue:
                self._finish("timeout")
                break
            ev = heapq.heappop(self._queue)
            if ev.tick > self.rules.time_limit_t:
                self.now = self.rules.time_limit_t
                self._finish("timeout")
                break
            self.now = ev.tick
            if ev.kind == EV_HIT:
                self._on_hit(ev)
            elif ev.kind == EV_HOWL:
                self._on_howl(ev)
            elif ev.kind == EV_FREE:
                self._on_free(ev)
            elif ev.kind == EV_REST:
                self._on_rest()
            elif ev.kind == EV_AI:
                self._on_ai(ev)
            elif ev.kind == EV_ALLY:
                self._on_ally(ev)
        return None

    def submit(self, choice: ActionChoice) -> None:
        req = self._request
        if req is None:
            raise RuntimeError("지금은 결정 차례가 아닙니다")
        if not any(o.choice == choice for o in req.options):
            raise ValueError(f"선택할 수 없는 행동: {choice}")
        self._request = None
        self._ally_act(self.fighters[req.actor], choice)

    def brain(self, e: Combatant) -> EnemyBrain:
        b = self.brains[e.index]
        assert b is not None
        return b

    def occupant(self, cell: Hex) -> Combatant | None:
        for f in self.fighters:
            if f.alive and cell in f.cells():
                return f
        return None

    def hostile(self, a: Combatant, b: Combatant) -> bool:
        return a.is_ally != b.is_ally

    def is_resting(self, c: Combatant) -> bool:
        """휴식: 어그로 대상이 아니고, 전투 행동 중이 아니며, 적과 인접하지 않음."""
        if not c.alive or not c.is_ally or c.busy not in (READY, WAIT):
            return False
        for e in self.enemies:
            if e.alive and (e.target == c.index or unit_distance(e, c) <= 1):
                return False
        return True

    def front_ally(self, e: Combatant) -> Combatant | None:
        """적의 정면 인접 칸에 선 아군 (여럿이면 지금 대상, 그다음 결정 순서)."""
        cands = [a for a in self.allies if a.alive and unit_distance(e, a) == 1 and sector(e.cells(), e.facing, a.cell) == FRONT]
        if not cands:
            return None
        for a in cands:
            if a.index == e.target:
                return a
        return min(cands, key=lambda a: a.rank)

    def facing_of(self, c: Combatant) -> int:
        """적은 바라보는 방향. 아군은 가장 최근에 공격한 적(없으면 가장 가까운 적) 쪽을 본다. 이동해도 시선은 적에게."""
        if not c.is_ally:
            return c.facing
        alive = [e for e in self.enemies if e.alive]
        if not alive:
            return c.facing
        if c.focus != NO_TARGET and self.fighters[c.focus].alive:
            focus = self.fighters[c.focus]
        else:
            focus = min(alive, key=lambda e: (unit_distance(c, e), e.index))
        c.facing = direction_toward(c.cells(), self._nearest_cell(focus.cells(), c.cells()), c.facing)
        return c.facing

    def auto_kind(self, c: Combatant) -> str:
        """약공격 자동 대응 태세. 적: 태세 목록의 첫 번째, 아군: 근력 >= 민첩이면 가드, 아니면 회피."""
        if c.enemy_def is not None:
            return c.enemy_def.stances[0] if c.enemy_def.stances else ""
        if c.str_ >= c.agi and (c.weapon is not None or c.shield is not None):
            return "guard"
        return "evade"

    def auto_chance(self, dfn: Combatant, atk: Combatant, attack_id: str) -> int:
        r = self.rules
        if dfn.enemy_def is not None:
            bonus = self.brain(dfn).stance_bonus(self._key(atk), attack_id) if atk.is_ally else 0
            bp = dfn.enemy_def.auto_defense_bp + bonus
        else:
            stat = dfn.str_ if self.auto_kind(dfn) == "guard" else dfn.agi
            bp = r.auto.base_bp + r.auto.stat_bp * stat
        return max(r.min_success_bp, min(r.max_success_bp, bp))

    def can_auto(self, dfn: Combatant, atk: Combatant) -> bool:
        """자동 대응 가능: 대기·기다리기·태세 유지·이동 중이고, 공격자가 시야 구역 안."""
        if dfn.busy not in (READY, WAIT, STANCE_HOLD, MOVE) or not self.auto_kind(dfn):
            return False
        vision = dfn.enemy_def.vision if dfn.enemy_def is not None else self.rules.auto.ally_vision
        return sector(dfn.cells(), self.facing_of(dfn), self._nearest_cell(atk.cells(), dfn.cells())) <= vision

    def reachable(self, c: Combatant) -> list[tuple[Hex, int]]:
        """이동 가능한 칸과 걸음 수. 아군 칸은 지나갈 수 있지만 멈출 수 없고, 적 칸은 지나갈 수 없다."""
        blocked: set[Hex] = set()
        allied: set[Hex] = set()
        for f in self.fighters:
            if f.alive and f is not c:
                for cell in f.cells():
                    (allied if f.is_ally == c.is_ally else blocked).add(cell)
        out: list[tuple[Hex, int]] = []
        seen = {c.cell}
        frontier = [c.cell]
        for step in range(1, self.rules.move_max_cells + 1):
            nxt: list[Hex] = []
            for h in frontier:
                for n in neighbors(h):
                    if n in seen or n not in self.arena or n in blocked:
                        continue
                    seen.add(n)
                    nxt.append(n)
                    if n not in allied:
                        out.append((n, step))
            frontier = nxt
        return out

    # ------------------------------------------------------------ 큐
    def _push(self, tick: int, prio: int, kind: str, actor: int, a: int = 0, b: int = 0) -> None:
        self._seq += 1
        heapq.heappush(self._queue, _Event(tick, prio, self._seq, kind, actor, a, b))

    def _set_free(self, c: Combatant, tick: int) -> None:
        c.free_at = tick
        c.free_token += 1
        c.decide_token += 1
        self._push(tick, PRIO_FREE, EV_FREE, c.index, c.free_token)

    def _schedule_decision(self, c: Combatant, tick: int, extra_t: int = 0, immediate: bool = False) -> None:
        """immediate: 적의 반응 시간 없이 바로 (딜레이 캐치)."""
        c.decide_token += 1
        if c.is_ally:
            self._push(tick, PRIO_ALLY + c.rank, EV_ALLY, c.index, c.decide_token)
        else:
            assert c.enemy_def is not None
            delay = 0 if immediate else c.enemy_def.reaction_t + extra_t
            self._push(tick + delay, PRIO_AI, EV_AI, c.index, c.decide_token)

    def _log(self, c: Combatant | None, event: str, ref: str = "", value: int = 0, info: str = "") -> None:
        self.log.append(L.LogEvent(self.now, c.cid if c else "", event, ref, value, info))

    # ------------------------------------------------------------ 이벤트 처리
    def _on_ally(self, ev: _Event) -> None:
        a = self.fighters[ev.actor]
        if ev.a != a.decide_token or a.busy not in WAKEABLE:
            return
        self._request = build_request(self, a.index)

    def _on_ai(self, ev: _Event) -> None:
        e = self.fighters[ev.actor]
        if ev.a != e.decide_token or e.busy != READY:
            return
        self._enemy_decide(e)

    def _on_free(self, ev: _Event) -> None:
        c = self.fighters[ev.actor]
        if ev.a != c.free_token or not c.alive:
            return
        act, prev = c.action, c.busy
        if act is not None:
            if act.kind == "move":
                self._finish_move(c, act)
            elif act.kind == "item":
                self._finish_item(c, act)
            elif act.kind == "swap":
                self._finish_swap(c, act)
            elif act.kind in ("turn", "retarget"):
                c.facing = act.facing
                if act.kind == "retarget":
                    assert c.enemy_def is not None
                    c.pattern = self.rng.below(len(c.enemy_def.patterns))
                    c.step = 0
        c.action = None
        c.combo = 0
        if prev == BROKEN:
            self._rise(c)
            return
        if c.is_ally and prev in (STANCE, STANCE_HOLD) and c.stance is not None:
            c.busy = STANCE_HOLD
        else:
            c.busy, c.stance = READY, None
        self._schedule_decision(c, self.now)

    def _on_hit(self, ev: _Event) -> None:
        c = self.fighters[ev.actor]
        act = c.action
        if act is None or act.action_id != ev.a or c.busy != ATTACK or act.consumed[ev.b]:
            return
        act.consumed[ev.b] = True
        self._resolve_hit(c, act, ev.b)

    def _on_howl(self, ev: _Event) -> None:
        c = self.fighters[ev.actor]
        act = c.action
        if act is None or act.action_id != ev.a or c.busy != HOWL:
            return
        h = self.rules.howl
        for e in self.enemies:
            if e.alive and unit_distance(e, c) <= h.radius:
                if h.mode == "boost":
                    self.brain(e).record(self.now, c.index, h.boost)
                else:
                    self.brain(e).cut_others(c.index, h.cut_bp)
                self._log(c, L.HOWL, e.cid, h.boost if h.mode == "boost" else h.cut_bp, h.mode)

    def _on_rest(self) -> None:
        r = self.rules
        for a in self.allies:
            if a.hp < a.max_hp and self.is_resting(a):
                boost = a.boost_bp if self.now < a.boost_until else BP
                amount = min(a.max_hp - a.hp, r.rest_regen * boost // BP)
                if amount > 0:
                    a.hp += amount
                    self._log(a, L.HEAL, "", amount, "boost" if boost != BP else "")
        self._push(self.now + r.rest_interval_t, PRIO_REST, EV_REST, 0)

    # ------------------------------------------------------------ 아군 행동
    def _ally_act(self, c: Combatant, ch: ActionChoice) -> None:
        r = self.rules
        if ch.kind in ("skill", "basic", "throw"):
            self._begin_attack(c, self.data.actions[ch.ref], ch.arg)
        elif ch.kind == "stance":
            self._begin_stance(c, ch.ref, 0)
        elif ch.kind == "move":
            q, rr = (int(x) for x in ch.ref.split(","))
            self._begin_move(c, (q, rr), ch.arg * move_cell_ticks(r, c))
        elif ch.kind == "item":
            self._begin_simple(c, "item", ITEM, r.pouch_use_t if ch.arg == 0 else r.inventory_use_t, ch.ref, ch.arg)
        elif ch.kind == "swap":
            self._begin_simple(c, "swap", SWAP, r.quick_swap_t if c.quick_change else r.swap_t, "", ch.arg)
        elif ch.kind == "howl":
            h = r.howl
            act = self._begin_simple(c, "howl", HOWL, h.startup_t + h.recovery_t, "howl", 0)
            c.cooldowns["howl"] = act.end + h.cooldown_t
            self._push(self.now + h.startup_t, PRIO_HIT, EV_HOWL, c.index, act.action_id)
        elif ch.kind == "wait":
            self._begin_wait(c, r.wait_max_t)
        else:
            raise ValueError(ch.kind)

    def _new_action(self, c: Combatant, kind: str, busy: str, end: int, keep_stance: bool = False) -> ActionState:
        if not keep_stance:
            c.stance = None
        act = ActionState(c.next_action_id, kind, self.now, end)
        c.next_action_id += 1
        c.action = act
        c.busy = busy
        self._set_free(c, end)
        return act

    def _begin_attack(self, c: Combatant, a: AttackDef, target: int) -> None:
        t = self.fighters[target]
        startup, active, recovery = attack_timing(self.rules, c, a)
        s = self.now
        act = self._new_action(c, "attack", ATTACK, s + startup + active + recovery)
        act.attack, act.ref, act.target = a, a.id, target
        act.active_start, act.active_end = s + startup, s + startup + active
        act.hit_ticks = [s + off for off in hit_offsets(self.rules, c, a)]
        act.consumed = [False] * len(a.hits)
        act.parried = [False] * len(a.hits)
        origin = self._nearest_cell(c.cells(), t.cells())
        aim = self._nearest_cell(t.cells(), [origin])
        act.facing = direction_toward([origin], aim, c.facing)
        if c.is_ally and not t.is_ally:
            c.focus, c.facing = target, act.facing
        if a.targeting == "place":
            act.place_cells = [aim] if a.area == "single" else disc(aim, 1)
        c.cooldowns[a.id] = act.end + a.cooldown_t
        for i, tick in enumerate(act.hit_ticks):
            self._push(tick, PRIO_HIT, EV_HIT, c.index, act.action_id, i)
        self._started(c, act)

    def _begin_stance(self, c: Combatant, kind: str, hold_until: int) -> None:
        rule = self.rules.stance(kind)
        act = self._new_action(c, "stance", STANCE, max(self.now + rule.startup_t + rule.min_hold_t, hold_until))
        act.ref = kind
        c.stance = StanceState(kind, self.now + rule.startup_t)
        self._started(c, act)

    def _begin_move(self, c: Combatant, dest: Hex, duration: int) -> None:
        act = self._new_action(c, "move", MOVE, self.now + duration)
        act.dest = dest
        act.ref = f"{dest[0]},{dest[1]}"
        self._started(c, act)

    def _begin_simple(self, c: Combatant, kind: str, busy: str, duration: int, ref: str, arg: int) -> ActionState:
        act = self._new_action(c, kind, busy, self.now + duration)
        act.ref, act.arg = ref, arg
        self._started(c, act)
        return act

    def _begin_wait(self, c: Combatant, duration: int) -> None:
        hold = c.busy == STANCE_HOLD and c.stance is not None
        act = self._new_action(c, "wait", STANCE_HOLD if hold else WAIT, self.now + duration, keep_stance=hold)
        act.ref = c.stance.kind if hold and c.stance else ""
        self._log(c, L.START, "hold" if hold else "wait", 0, "wait")

    def _started(self, c: Combatant, act: ActionState) -> None:
        self._log(c, L.START, act.ref, act.target if act.kind == "attack" else act.arg, act.kind)
        if c.is_ally:
            if act.kind == "attack" and act.attack is not None:
                key = self._key(c)
                for e in self.enemies:
                    if e.alive:
                        self.brain(e).observe(key, act.attack.id)
                target = self.fighters[act.target]
                if not target.is_ally and not target.provoked:
                    self._provoke(target, c)
                self._check_reaction(c, act)
        else:
            for a in self.allies:
                self._wake_ally(a)

    def _wake_ally(self, a: Combatant) -> None:
        """적이 행동을 시작하면 기다리던 아군이 반응할 기회를 얻는다."""
        if a.alive and a.busy in (WAIT, STANCE_HOLD):
            a.free_token += 1
            self._schedule_decision(a, self.now)

    # ------------------------------------------------------------ 적 행동
    def _provoke(self, e: Combatant, by: Combatant) -> None:
        """비선공 적이 공격받아 전투에 들어선다. 건드린 사람이 첫 대상."""
        e.provoked = True
        e.target = by.index
        self.brain(e).load(self._key(by), by.index, self.now)
        self._log(e, L.PROVOKE, by.cid)
        if e.busy in (READY, WAIT):
            e.free_token += 1
            e.action, e.busy = None, READY
            self._schedule_decision(e, self.now)

    def _pack_leader(self, e: Combatant) -> Combatant:
        return next(m for m in self.enemies if m.alive and m.enemy_def is e.enemy_def)

    def _key(self, ally: Combatant) -> str:
        return f"{ally.cid}:{ally.weapon_family}"

    def _nearest_ally(self, e: Combatant) -> Combatant:
        alive = [a for a in self.allies if a.alive]
        return min(alive, key=lambda a: (unit_distance(e, a), a.index))

    def _hate_target(self, e: Combatant) -> int:
        """정면 헤이트(정면의 아군) + 기억 길이 안의 누적 피해 헤이트. 가장 큰 쪽이 대상."""
        alive = [a for a in self.allies if a.alive]
        if not alive:
            return NO_TARGET
        if e.enemy_def is not None and e.enemy_def.pack:
            leader = self._pack_leader(e)
            if leader is not e and leader.target != NO_TARGET and self.fighters[leader.target].alive:
                return leader.target
        br = self.brain(e)
        br.prune(self.now)
        front = self.front_ally(e)
        best, best_score = NO_TARGET, -1
        for a in alive:
            score = br.accumulated(a.index) + (self.rules.front_hate if front is a else 0)
            better = score > best_score or (score == best_score and a.index == e.target)
            if better:
                best, best_score = a.index, score
        if best_score <= 0:
            cur = self.fighters[e.target] if e.target != NO_TARGET else None
            return cur.index if cur is not None and cur.alive else self._nearest_ally(e).index
        return best

    def _enemy_decide(self, e: Combatant) -> None:
        ed = e.enemy_def
        assert ed is not None
        br = self.brain(e)
        br.accrue(self.now)
        if e.reaction != NO_TARGET:
            if self._react(e):
                return
        if e.catch_target != NO_TARGET:
            victim = self.fighters[e.catch_target]
            e.catch_target = NO_TARGET
            weak = ed.weak_attack
            if weak is not None and victim.alive and victim.busy == ATTACK and in_range(weak, unit_distance(e, victim)):
                self._begin_attack(e, weak, victim.index)   # 딜레이 캐치 (패턴은 진행하지 않음)
                return
        if not e.provoked:
            self._begin_enemy_wait(e, ENEMY_IDLE_T_FRAMES * self.rules.ticks_per_frame)   # 비선공: 건드리기 전에는 가만히
            return
        target = self._hate_target(e)
        if target == NO_TARGET:
            self._begin_enemy_wait(e, ENEMY_IDLE_T_FRAMES * self.rules.ticks_per_frame)
            return
        if target != e.target:
            self._begin_retarget(e, target)
            return
        t = self.fighters[target]
        if sector(e.cells(), e.facing, self._nearest_cell(t.cells(), e.cells())) != FRONT:
            want = direction_toward(e.cells(), t.cell, e.facing)
            dur = turn_ticks(self.rules, e)
            if dur > 0:
                act = self._new_action(e, "turn", TURN, self.now + dur)
                act.facing, act.target = want, target
                self._log(e, L.TURN, t.cid, dur)
                self._wake_allies_all()
                return
            e.facing = want
        if ed.pack and self._pack_flank(e, t):
            return
        d = unit_distance(e, t)
        # 포위 대응
        if ed.surround_attack and e.cooldowns.get(ed.surround_attack, 0) <= self.now:
            adjacent = sum(1 for a in self.allies if a.alive and unit_distance(e, a) == 1)
            sa = ed.attack_by_id(ed.surround_attack)
            if adjacent >= ed.surround_count and in_range(sa, d):
                self._begin_attack(e, sa, target)
                return
        pattern = ed.patterns[e.pattern]
        step = pattern[e.step % len(pattern)]
        if step.kind == "wait":
            e.step = (e.step + 1) % len(pattern)
            self._begin_enemy_wait(e, step.wait_t)
            return
        a = ed.attack_by_id(step.attack_id)
        if d > a.range_max:
            if self._enemy_step_toward(e, t):
                return
            self._begin_enemy_wait(e, ENEMY_IDLE_T_FRAMES * self.rules.ticks_per_frame)
            return
        e.step = (e.step + 1) % len(pattern)
        if d < a.range_min:
            self._schedule_decision(e, self.now)  # 너무 가까워 쓸 수 없는 단계는 건너뛴다
            return
        self._begin_attack(e, a, target)

    def _wake_allies_all(self) -> None:
        for a in self.allies:
            self._wake_ally(a)

    def _begin_enemy_wait(self, e: Combatant, duration: int) -> None:
        self._new_action(e, "wait", WAIT, self.now + duration)
        self._log(e, L.START, "wait", duration, "wait")

    def _begin_retarget(self, e: Combatant, target: int) -> None:
        t = self.fighters[target]
        want = direction_toward(e.cells(), t.cell, e.facing)
        turn = turn_ticks(self.rules, e) if sector(e.cells(), e.facing, self._nearest_cell(t.cells(), e.cells())) != FRONT else 0
        load = self.brain(e).load(self._key(t), t.index, self.now)
        reload = load.reload_t if load else 0
        if load:
            for k in load.forgotten:
                self._log(e, L.FORGET, k)
        e.target = target
        act = self._new_action(e, "retarget", RETARGET, self.now + max(turn, reload, 1))
        act.facing, act.target, act.turn_done = want, target, self.now + turn
        self._log(e, L.RETARGET, t.cid, max(turn, reload), f"turn={turn} reload={reload}")
        self._wake_allies_all()

    def _enemy_step_toward(self, e: Combatant, t: Combatant) -> bool:
        return self._enemy_step_to(e, t.cells())

    def _enemy_step_to(self, e: Combatant, goal: list[Hex]) -> bool:
        """goal 칸들에 한 칸 가까워지도록 이동. 가까워질 수 없으면 False."""
        best: Hex | None = None
        best_d = cells_distance(e.cells(), goal)
        for d in DIRS:
            anchor = add(e.cell, d)
            cells = [add(anchor, o) for o in e.footprint]
            if not all(c in self.arena and (self.occupant(c) in (None, e)) for c in cells):
                continue
            nd = cells_distance(cells, goal)
            if nd < best_d:
                best, best_d = anchor, nd
        if best is None:
            return False
        act = self._new_action(e, "move", MOVE, self.now + move_cell_ticks(self.rules, e))
        act.dest = best
        act.ref = f"{best[0]},{best[1]}"
        self._started(e, act)
        return True

    def _pack_flank(self, e: Combatant, t: Combatant) -> bool:
        """무리: 다른 무리원이 대상의 정면을 잡고 있으면, 대상의 옆·뒤 칸(대상의 시선 기준)으로 돈다."""
        if len(e.footprint) != 1:
            return False
        tf = self.facing_of(t)
        mates = [m for m in self.enemies if m.alive and m is not e and m.enemy_def is e.enemy_def]
        if not any(unit_distance(m, t) == 1 and sector([t.cell], tf, m.cell) == FRONT for m in mates):
            return False
        free = [n for n in neighbors(t.cell) if n in self.arena and self.occupant(n) in (None, e)]
        if not free:
            return False
        best_sector = max(sector([t.cell], tf, n) for n in free)
        if best_sector < BACK_SIDE:
            return False
        if unit_distance(e, t) == 1 and sector([t.cell], tf, e.cell) >= best_sector:
            return False
        goals = [n for n in free if sector([t.cell], tf, n) == best_sector and n != e.cell]
        return bool(goals) and self._enemy_step_to(e, [min(goals, key=lambda n: (dist(n, e.cell), n[1], n[0]))])

    def _check_reaction(self, ally: Combatant, act: ActionState) -> None:
        """기억 중인 상대의 트리거 공격을 보면, 적이 자기 공격 도중이 아닐 때 반응 시간 뒤 태세로 대응한다."""
        if act.attack is None or act.target == NO_TARGET:
            return
        e = self.fighters[act.target]
        if e.is_ally or not e.alive or e.enemy_def is None or not e.enemy_def.stances:
            return
        if e.busy not in (READY, WAIT) or act.attack.id not in self.brain(e).triggers(self._key(ally)):
            return
        e.reaction = ally.index
        if e.busy == WAIT:
            e.free_token += 1
            e.action, e.busy = None, READY
        self._schedule_decision(e, self.now)

    def _react(self, e: Combatant) -> bool:
        ally = self.fighters[e.reaction]
        e.reaction = NO_TARGET
        act = ally.action
        if not ally.alive or ally.busy != ATTACK or act is None or act.attack is None or act.target != e.index:
            return False
        pending = ally.pending_hits(self.now)
        if not pending:
            return False
        assert e.enemy_def is not None
        bonus = self.brain(e).stance_bonus(self._key(ally), act.attack.id)
        best = max(e.enemy_def.stances,
                   key=lambda s: (stance_chance(self.data, e, s, bonus), -STANCE_ORDER.index(s)))
        self._log(e, L.REACT, best, 0, act.attack.id)
        self._begin_stance(e, best, act.hit_ticks[pending[-1]] + 1)
        return True

    def _rise(self, e: Combatant) -> None:
        """무력화에서 일어서면 광역 공격 후 어그로 초기화, 무작위 대상 (적응 기억은 유지)."""
        assert e.enemy_def is not None
        e.break_gauge = 0
        br = self.brain(e)
        br.records.clear()
        alive = [a for a in self.allies if a.alive]
        new = alive[self.rng.below(len(alive))] if alive else None
        self._log(e, L.RISE, new.cid if new else "", 0, "aggro_reset")
        e.busy = READY
        if new is not None and new.index != e.target:
            load = br.load(self._key(new), new.index, self.now)
            e.target = new.index
            if load:
                self._log(e, L.RELOAD, new.cid, load.reload_t, "remembered" if load.remembered else "new")
        rise = e.enemy_def.rise_attack
        if rise and new is not None:
            self._begin_attack(e, e.enemy_def.attack_by_id(rise), new.index)
        else:
            self._schedule_decision(e, self.now)

    # ------------------------------------------------------------ 행동 완료
    def _finish_move(self, c: Combatant, act: ActionState) -> None:
        cells = [add(act.dest, o) for o in c.footprint]
        if all(c2 in self.arena and self.occupant(c2) in (None, c) for c2 in cells):
            c.cell = act.dest
            self._log(c, L.MOVE_DONE, act.ref)
            if not c.is_ally and c.target != NO_TARGET:
                c.facing = direction_toward(c.cells(), self.fighters[c.target].cell, c.facing)
        else:
            self._log(c, L.MOVE_DONE, act.ref, 0, "blocked")

    def _finish_item(self, c: Combatant, act: ActionState) -> None:
        stacks = c.pouch if act.arg == 0 else c.inventory
        st = next((s for s in stacks if s.item_id == act.ref and s.count > 0), None)
        if st is None:
            return
        st.count -= 1
        if st.count == 0:
            stacks.remove(st)
        item = self.data.items[act.ref]
        c.boost_until, c.boost_bp = self.now + item.duration_t, item.boost_bp
        self._log(c, L.ITEM_USED, item.id, item.duration_t, "pouch" if act.arg == 0 else "inventory")

    def _finish_swap(self, c: Combatant, act: ActionState) -> None:
        if not (0 <= act.arg < len(c.spares)):
            return
        new, old = c.spares[act.arg], c.weapon
        if old is not None:
            c.spares[act.arg] = old
        else:
            c.spares.pop(act.arg)
        c.weapon = new
        self._log(c, L.SWAP, new.weapon.id, new.durability)
        self._pattern_changed(c)

    def _pattern_changed(self, ally: Combatant) -> None:
        """싸우는 패턴(무기 계열)이 바뀌면 그를 노리던 적이 부분 재로드 (솔로 스위치)."""
        for e in self.enemies:
            if not e.alive or e.target != ally.index:
                continue
            load = self.brain(e).load(self._key(ally), ally.index, self.now)
            if load is None:
                continue
            for k in load.forgotten:
                self._log(e, L.FORGET, k)
            self._log(e, L.RELOAD, self._key(ally), load.reload_t, "remembered" if load.remembered else "new")
            if e.busy in (READY, WAIT):
                e.free_token += 1
                e.action, e.busy = None, READY
                self._schedule_decision(e, self.now, load.reload_t)
            else:
                self._set_free(e, e.free_at + load.reload_t)

    # ------------------------------------------------------------ 타격 판정
    @staticmethod
    def _nearest_cell(cells: list[Hex], to: list[Hex]) -> Hex:
        return min(cells, key=lambda c: (min(dist(c, t) for t in to), c[1], c[0]))

    def _area_cells(self, atk: Combatant, act: ActionState) -> list[Hex]:
        a = act.attack
        assert a is not None
        if a.area == "single":
            return []
        own = atk.cells()
        if a.area == "ring1":
            out: list[Hex] = []
            for c in own:
                for n in neighbors(c):
                    if n not in own and n not in out:
                        out.append(n)
            return out
        t = self.fighters[act.target]
        origin = self._nearest_cell(own, t.cells())
        return shape_cells(origin, act.facing, a.area)

    def _resolve_hit(self, atk: Combatant, act: ActionState, idx: int) -> None:
        a = act.attack
        assert a is not None
        victims: list[Combatant] = []
        friends: list[Combatant] = []
        if a.targeting == "place":
            for f in self.fighters:
                if f.alive and self.hostile(atk, f) and any(c in act.place_cells for c in f.cells()):
                    victims.append(f)
            if not victims:
                self._log(atk, L.MISS_ZONE, a.id)
        else:
            main = self.fighters[act.target]
            if not main.alive:
                return
            if a.lunge and idx == 0 and self.hostile(atk, main):
                self._lunge(atk, main)
            victims.append(main)
            area = self._area_cells(atk, act)
            for f in self.fighters:
                if f is atk or f is main or not f.alive or not any(c in area for c in f.cells()):
                    continue
                (victims if self.hostile(atk, f) else friends).append(f)
        for i, v in enumerate(victims):
            if atk.alive and v.alive and self.result is None:
                self._hit_one(atk, act, idx, v, main=(a.targeting == "person" and i == 0))
        for f in friends:
            if f.alive and self.result is None:
                self._friendly_fire(atk, act, a.hits[idx], f)

    def _lunge(self, atk: Combatant, target: Combatant) -> None:
        """돌진: 대상에 인접한 빈자리 중 가장 가까운 곳으로 파고든다 (여러 칸 적은 차지 칸 전체가 들어갈 자리)."""
        if unit_distance(atk, target) <= 1:
            return
        anchors: list[Hex] = []
        for c in target.cells():
            for n in neighbors(c):
                for o in atk.footprint:
                    anchor = (n[0] - o[0], n[1] - o[1])
                    cells = [add(anchor, x) for x in atk.footprint]
                    if anchor not in anchors and all(x in self.arena and self.occupant(x) in (None, atk) for x in cells) \
                            and cells_distance(cells, target.cells()) == 1:
                        anchors.append(anchor)
        if anchors:
            atk.cell = min(anchors, key=lambda h: (dist(h, atk.cell), h[1], h[0]))

    def _hit_one(self, atk: Combatant, act: ActionState, idx: int, dfn: Combatant, main: bool) -> None:
        a = act.attack
        assert a is not None
        hit = a.hits[idx]
        if main and a.can_clash:
            j = self._find_clash(atk, act, dfn)
            if j >= 0:
                self._clash(atk, act, idx, dfn, j)
                return
        if dfn.busy in (BROKEN, KNOCKDOWN):
            self._land(atk, act, hit, dfn, BP, 0)
            return
        if dfn.stance is not None and dfn.busy in (STANCE, STANCE_HOLD) and self.now >= dfn.stance.active_from:
            self._stance_defend(atk, act, idx, dfn)
            return
        if a.weak and self.can_auto(dfn, atk) and self._auto_defend(atk, act, hit, dfn):
            return
        self._land(atk, act, hit, dfn, BP, 0)

    def _auto_defend(self, atk: Combatant, act: ActionState, hit: HitDef, dfn: Combatant) -> bool:
        """약공격 자동 가드/회피. 성공하면 공격측이 굳는다 (딜레이 캐치)."""
        a = act.attack
        assert a is not None
        kind = self.auto_kind(dfn)
        chance = self.auto_chance(dfn, atk, a.id)
        if not self.rng.roll_bp(chance):
            self._log(dfn, L.AUTO_FAIL, kind, chance, a.id)
            return False
        self._log(dfn, L.AUTO_OK, kind, chance, a.id)
        if kind == "guard":
            self._guard_wear(dfn, a)
        self._catch(atk, act, dfn, kind)
        return True

    def _catch(self, atk: Combatant, act: ActionState, dfn: Combatant, kind: str) -> None:
        """일반 공격이 막히면 공격측이 굳는다: 가드면 일반 공격이, 회피면 선딜 짧은 소드 스킬이 들어갈 만큼."""
        if act.defended or atk.busy != ATTACK or atk.action is not act:
            return
        act.defended = True
        window = self.rules.auto.guard_catch_t if kind == "guard" else self.rules.auto.evade_catch_t
        weak = dfn.enemy_def.weak_attack if dfn.enemy_def is not None else None
        if weak is not None:
            # 막은 적의 약공격이 반드시 들어가도록 (반응 시간 없이 바로 친다)
            window = max(window, first_hit_offset(self.rules, dfn, weak) + 1)
        until = max(atk.free_at, self.now + window)
        act.end = until
        self._set_free(atk, until)
        self._expose(atk)
        self._log(atk, L.CATCH, dfn.cid, window, kind)
        if dfn.is_ally:
            if dfn.busy in (WAIT, STANCE_HOLD):
                dfn.free_token += 1
                self._schedule_decision(dfn, self.now)
        else:
            dfn.catch_target = atk.index
            if dfn.busy in (READY, WAIT, STANCE, STANCE_HOLD):
                dfn.free_token += 1
                dfn.action, dfn.stance, dfn.busy = None, None, READY
                self._schedule_decision(dfn, self.now, immediate=True)

    def _find_clash(self, atk: Combatant, act: ActionState, dfn: Combatant) -> int:
        """패리: 아군 소드 스킬의 타격과 적 공격의 타격이 서로를 노리며 창 안에서 만나면, 그 적 타격의 인덱스."""
        da = dfn.action
        if atk.is_ally == dfn.is_ally or dfn.busy != ATTACK or da is None or da.attack is None or act.attack is None:
            return -1
        if da.target != atk.index or act.target != dfn.index:
            return -1
        ally_attack, enemy_attack = (act.attack, da.attack) if atk.is_ally else (da.attack, act.attack)
        if ally_attack.kind != "sword_skill" or not enemy_attack.can_clash:
            return -1
        for j, t in enumerate(da.hit_ticks):
            if not da.consumed[j] and self.now <= t <= self.now + self.rules.parry.window_t:
                return j
        return -1

    def _clash(self, atk: Combatant, act: ActionState, idx: int, dfn: Combatant, j: int) -> None:
        """패리 한 타: 확률 없이 피해 0. 적 기술의 모든 타를 막았으면 위력 비교로 승·무·패."""
        da = dfn.action
        assert da is not None and da.attack is not None and act.attack is not None
        da.consumed[j] = True
        if atk.is_ally:
            ally, al_act, al_i, enemy, en_act, en_i = atk, act, idx, dfn, da, j
        else:
            ally, al_act, al_i, enemy, en_act, en_i = dfn, da, j, atk, act, idx
        assert al_act.attack is not None and en_act.attack is not None
        r = self.rules
        en_act.parried[en_i] = True
        en_act.parrier = ally.index
        en_act.parry_power_self += clash_power(r, enemy, en_act.attack, en_act.attack.hits[en_i])
        en_act.parry_power_other += clash_power(r, ally, al_act.attack, al_act.attack.hits[al_i])
        done, total = sum(en_act.parried), len(en_act.parried)
        self._log(ally, L.PARRY, en_act.attack.id, done, str(total))
        self._wear_weapon(ally, r.durability.parry)
        if done < total or en_act.parrier != ally.index:
            return      # 부분 패리: 막은 타만 무효
        pa, pe = en_act.parry_power_other, en_act.parry_power_self
        info = f"{al_act.attack.id}={pa} vs {en_act.attack.id}={pe}"
        if abs(pa - pe) <= max(pa, pe) * r.parry.draw_margin_bp // BP:
            self._log(ally, L.CLASH, "draw", pa - pe, info)
            self._apply_stun(ally, self.now + r.parry.draw_stun_t)
            self._apply_stun(enemy, self.now + r.parry.draw_stun_t)
        elif pa > pe:
            self._log(ally, L.CLASH, "win", pa - pe, info)
            w = ally.weapon.weapon.weight_stat if ally.weapon else 0
            if not self._add_break(enemy, w * r.weight_break):
                self._apply_stun(enemy, self.now + r.parry.lose_stun_t + w * r.weight_stun_ticks)
        else:
            self._log(ally, L.CLASH, "lose", pa - pe, info)
            self._apply_stun(ally, self.now + r.parry.lose_stun_t)
            self._knockback(ally, enemy, r.parry.knockback_cells)

    def _knockback(self, c: Combatant, away_from: Combatant, cells: int) -> None:
        """밀려남: away_from에게서 멀어지는 빈칸으로 한 칸씩."""
        moved = 0
        for _ in range(cells):
            cur = cells_distance(c.cells(), away_from.cells())
            options = [n for n in neighbors(c.cell) if n in self.arena and self.occupant(n) is None
                       and cells_distance([n], away_from.cells()) > cur]
            if not options:
                break
            c.cell = min(options, key=lambda n: (-cells_distance([n], away_from.cells()), n[1], n[0]))
            moved += 1
        if moved:
            self._log(c, L.KNOCKBACK, away_from.cid, moved)

    def _guard_wear(self, dfn: Combatant, a: AttackDef) -> None:
        """가드: 방패가 있으면 방패가, 없으면 무기가 내구도를 떠안는다 (중량이면 더)."""
        if not dfn.is_ally:
            return
        cost = self.rules.durability.guard * (self.rules.heavy.durability_bp if a.heavy else BP) // BP
        if dfn.shield is not None:
            self._wear_shield(dfn, cost)
        else:
            self._wear_weapon(dfn, cost)

    def _stance_defend(self, atk: Combatant, act: ActionState, idx: int, dfn: Combatant) -> None:
        a = act.attack
        assert a is not None and dfn.stance is not None
        hit = a.hits[idx]
        kind = dfn.stance.kind
        rule = self.rules.stance(kind)
        if rule.roll_per_attack and dfn.index in act.guard_locked:
            ok, chance = True, -1
        else:
            if a.weak:
                chance = self.rules.auto.weak_manual_bp   # 약공격은 대응하면 거의 확정
            else:
                bonus = self.brain(dfn).stance_bonus(self._key(atk), a.id) if not dfn.is_ally and atk.is_ally else 0
                chance = stance_chance(self.data, dfn, kind, bonus)
            ok = self.rng.roll_bp(chance)
            if ok and rule.roll_per_attack:
                act.guard_locked.append(dfn.index)
        if not ok:
            self._log(dfn, L.STANCE_FAIL, kind, chance, a.id)
            dfn.stance = None
            self._land(atk, act, hit, dfn, rule.fail_damage_bp, rule.fail_extra_stun_t)
            return
        self._log(dfn, L.STANCE_OK, kind, chance, a.id)
        heavy = self.rules.heavy
        chip_bp = rule.chip_bp * (heavy.chip_bp if a.heavy else BP) // BP
        chip = raw_hit_power(atk, a, hit) * chip_bp // BP
        if chip > 0:
            self._deal(atk, dfn, chip, a.id, "chip")
            if not dfn.alive:
                return
        if kind == "guard":
            self._guard_wear(dfn, a)
        if a.weak:
            self._catch(atk, act, dfn, kind)
            return
        stun = hit.hitstun_t * rule.defender_stun_bp // BP
        if a.heavy and kind == "guard":
            stun = stun * heavy.guard_stun_bp // BP
        if dfn.stance is not None and stun > 0:
            until = max(dfn.free_at, self.now + stun) if dfn.busy == STANCE else self.now + stun
            dfn.busy = STANCE
            self._set_free(dfn, until)
        self._defense_advantage(atk, act, rule.advantage_t)

    def _defense_advantage(self, atk: Combatant, act: ActionState, ticks: int) -> None:
        """방어 성공으로 공격측 후딜이 늘어난다. 공격당 1회."""
        if act.defended or atk.busy != ATTACK or atk.action is not act:
            return
        act.defended = True
        act.end += ticks
        self._set_free(atk, atk.free_at + ticks)
        self._expose(atk)

    def _expose(self, e: Combatant) -> None:
        """공격이 막혀 굳은 적이 약점을 드러낸다 (적 고유 특징): 굳어 있는 동안 방향 보정·방어력 무시."""
        if e.enemy_def is not None and e.enemy_def.exposed_on_defended:
            e.exposed_until = e.free_at
            self._log(e, L.EXPOSED, "", e.free_at - self.now)

    def _deal(self, atk: Combatant, dfn: Combatant, amount: int, ref: str, info: str) -> None:
        """피해 적용과 누적 피해 헤이트 기록. 체력이 0이 되면 쓰러진다."""
        dfn.hp -= amount
        self._log(atk, L.HIT, ref, amount, f"{dfn.cid}:{info}")
        if atk.is_ally and not dfn.is_ally:
            self.brain(dfn).record(self.now, atk.index, amount)
            if not dfn.provoked and dfn.hp > 0:
                self._provoke(dfn, atk)
        if dfn.hp <= 0:
            dfn.hp = 0
            self._die(dfn)

    def _land(self, atk: Combatant, act: ActionState, hit: HitDef, dfn: Combatant, dmg_bp: int, extra_stun_t: int) -> None:
        a = act.attack
        assert a is not None
        du = self.rules.durability
        phase = dfn.attack_phase(self.now)
        exposed = self.now < dfn.exposed_until
        dmg_bp = dmg_bp * (max(BP, self._sector_bp(atk, dfn)) if exposed else self._sector_bp(atk, dfn)) // BP
        before_armor = raw_hit_power(atk, a, hit) * dmg_bp // BP
        state = (phase or dfn.busy) + ("+exposed" if exposed else "")
        self._deal(atk, dfn, damage(atk, a, hit, dfn, dmg_bp, ignore_defense=exposed), a.id, state)
        if atk.is_ally and atk.weapon is not None and a.kind in ("sword_skill", "basic"):
            self._wear_weapon(atk, du.skill_hit if a.kind == "sword_skill" else du.basic_hit)
        if dfn.is_ally and dfn.armor is not None and dfn.alive:
            reduced = min(dfn.defense, before_armor)
            if reduced > 0:
                self._wear_armor(dfn, reduced * du.armor_per_damage)
        if not dfn.alive or dfn.busy in (BROKEN, KNOCKDOWN):
            return
        if self._add_break(dfn, hit.break_value):
            return
        if dfn.busy == ATTACK:
            if phase == PREMOTION:
                if not a.interrupts:
                    return
                assert dfn.action is not None
                self._log(atk, L.INTERRUPT, dfn.action.ref, 0, dfn.cid)
            elif phase == ACTIVE:
                return  # 시스템 어시스트 중에는 끊기지 않는다
        if a.knockdown_bp > 0 and self.rng.roll_bp(a.knockdown_bp):
            self._knockdown(dfn, a)
            return
        in_combo = dfn.busy == HITSTUN
        dfn.combo = dfn.combo + 1 if in_combo else 0
        until = self.now + hitstun(self.rules, dfn, hit.hitstun_t + extra_stun_t, dfn.combo)
        if in_combo:
            until = max(until, dfn.free_at)
        self._apply_stun(dfn, until)

    def _friendly_fire(self, atk: Combatant, act: ActionState, hit: HitDef, f: Combatant) -> None:
        """아군 기술에 맞은 아군: 피해 감소, 행동 끊김. 적의 헤이트에는 영향 없음."""
        a = act.attack
        assert a is not None
        amount = damage(atk, a, hit, f, self.rules.ff_damage_bp)
        f.hp -= amount
        self._log(atk, L.FRIENDLY_FIRE, a.id, amount, f.cid)
        if f.hp <= 0:
            f.hp = 0
            self._die(f)
            return
        if (f.busy == ATTACK and f.attack_phase(self.now) == ACTIVE) or f.busy in (KNOCKDOWN, BROKEN):
            return
        self._apply_stun(f, max(self.now + self.rules.ff_stun_t, f.free_at if f.busy == HITSTUN else 0))

    def _sector_bp(self, atk: Combatant, dfn: Combatant) -> int:
        """적의 방향별 받는 피해 배율 (고유 특징). 넘어진 적은 방향이 풀려 후면으로 본다."""
        if dfn.enemy_def is None:
            return BP
        if dfn.busy == KNOCKDOWN:
            return dfn.enemy_def.sector_damage_bp[BACK]
        s = sector(dfn.cells(), dfn.facing, self._nearest_cell(atk.cells(), dfn.cells()))
        return dfn.enemy_def.sector_damage_bp[s]

    def _knockdown(self, c: Combatant, a: AttackDef) -> None:
        """넘어짐: 기상할 때까지 행동·가드·자동 대응 불가."""
        c.action, c.stance, c.combo = None, None, 0
        c.busy = KNOCKDOWN
        t = getup_ticks(self.rules, c)
        self._log(c, L.KNOCKDOWN, a.id, t)
        self._set_free(c, self.now + t)

    def _apply_stun(self, c: Combatant, until: int) -> None:
        c.action, c.stance = None, None
        c.busy = HITSTUN
        self._set_free(c, until)

    def _add_break(self, c: Combatant, amount: int) -> bool:
        """무력화 게이지. 가득 차면 다운시키고 True."""
        if amount <= 0 or c.enemy_def is None or not self.rules.size(c.size).breakable or c.busy == BROKEN:
            return False
        c.break_gauge += amount
        if c.break_gauge < c.enemy_def.break_max:
            return False
        c.break_gauge = 0
        c.action, c.stance, c.combo = None, None, 0
        c.busy = BROKEN
        self._log(c, L.BREAK)
        self._set_free(c, self.now + c.enemy_def.break_down_t)
        return True

    def _die(self, c: Combatant) -> None:
        c.action, c.stance = None, None
        c.busy = DEAD
        c.free_token += 1
        c.decide_token += 1
        self._log(c, L.DEATH)
        if not any(a.alive for a in self.allies):
            self._finish("enemies")
        elif not any(e.alive for e in self.enemies):
            self._finish("party")

    def _finish(self, winner: str) -> None:
        self.result = L.BattleResult(winner, self.now, tuple(a.hp for a in self.allies), tuple(e.hp for e in self.enemies))
        self._request = None
        self._log(None, L.END, winner, self.now)

    # ------------------------------------------------------------ 내구도
    def _warn(self, c: Combatant, name: str, before: int, after: int, maximum: int) -> None:
        for thr in self.rules.durability.warn_bp:
            if before * BP > thr * maximum >= after * BP:
                self._log(c, L.DURABILITY_WARN, name, after, f"{thr}")

    def _wear_weapon(self, c: Combatant, cost: int) -> None:
        ow = c.weapon
        if ow is None:
            return
        before = ow.durability
        ow.durability -= cost
        self._warn(c, ow.weapon.id, before, ow.durability, ow.weapon.durability)
        if ow.durability > 0:
            return
        self._log(c, L.EQUIP_BROKEN, ow.weapon.id)
        c.weapon = None
        act = c.action
        if c.busy == ATTACK and act is not None and act.attack is not None and act.attack.kind in ("sword_skill", "basic"):
            act.consumed = [True] * len(act.consumed)
        if c.stance is not None and self.rules.stance(c.stance.kind).requires_weapon and not (c.stance.kind == "guard" and c.shield):
            c.stance = None
            if c.busy in (STANCE, STANCE_HOLD):
                c.busy = WAIT
        self._pattern_changed(c)

    def _wear_shield(self, c: Combatant, cost: int) -> None:
        if c.shield is None:
            return
        before = c.shield_dur
        c.shield_dur -= cost
        self._warn(c, c.shield.id, before, c.shield_dur, c.shield.durability)
        if c.shield_dur <= 0:
            self._log(c, L.EQUIP_BROKEN, c.shield.id)
            c.shield = None
            if c.stance is not None and c.stance.kind == "guard" and c.weapon is None:
                c.stance = None
                if c.busy in (STANCE, STANCE_HOLD):
                    c.busy = WAIT

    def _wear_armor(self, c: Combatant, cost: int) -> None:
        if c.armor is None:
            return
        before = c.armor_dur
        c.armor_dur -= cost
        self._warn(c, c.armor.id, before, c.armor_dur, c.armor.durability)
        if c.armor_dur <= 0:
            self._log(c, L.EQUIP_BROKEN, c.armor.id)
            c.armor = None
            c.defense = 0
