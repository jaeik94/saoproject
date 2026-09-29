"""규칙 단위 확인. 전투 상태를 직접 세팅하므로 코어 내부 메서드를 쓴다."""
import unittest

from app.loader import DATA_DIR, DATA_FILES, read_json
from core import log as L
from core.battle import Battle
from core.combatant import ATTACK, BROKEN, HITSTUN, READY, RETARGET, SWAP, WAIT
from core.defs import DataError, parse_game_data
from core.formulas import stance_chance
from core.hexgrid import DIRS, add
from core.loadout import LoadoutError, build_party, build_player


def raw_data() -> dict:
    return {name: read_json(DATA_DIR / f"{name}.json") for name in DATA_FILES}


# 1구획 몬스터는 기술 3종뿐이라, 엔진 규칙(장소 지정·포위 대응·3연격)을 시험할 때만 쓰는 공격
TEST_SWEEP = {"id": "sweep", "name": "시험용 휩쓸기", "physical": "blunt", "range": [1, 2], "startup": 30, "active": 6,
              "recovery": 24, "cooldown": 0, "targeting": "place", "area": "disc1", "can_clash": False, "interrupts": True,
              "hits": [{"at": 2, "power_bp": 26000, "hitstun": 30}]}
TEST_SPIN = {"id": "spin", "name": "시험용 몸부림", "physical": "slash", "range": [1, 1], "startup": 16, "active": 6,
             "recovery": 26, "cooldown": 240, "area": "ring1", "can_clash": False, "interrupts": True,
             "hits": [{"at": 2, "power_bp": 26000, "hitstun": 24}]}
TEST_FLURRY = {"id": "flurry", "name": "시험용 3연격", "physical": "slash", "range": [1, 1], "startup": 16, "active": 20,
               "recovery": 22, "cooldown": 0, "can_clash": True, "interrupts": True,
               "hits": [{"at": 0, "power_bp": 10000, "hitstun": 10}, {"at": 7, "power_bp": 8600, "hitstun": 10},
                        {"at": 14, "power_bp": 10000, "hitstun": 16}]}


def with_wolf_attacks(raw: dict, *attacks: dict) -> dict:
    raw["enemies"]["dire_wolf"]["attacks"].extend(dict(a) for a in attacks)
    return raw


def raw_loadout() -> dict:
    return read_json(DATA_DIR / "loadout.json")


def raw_party() -> dict:
    return read_json(DATA_DIR / "party.json")


class DataTest(unittest.TestCase):
    def test_default_data_loads(self) -> None:
        data = parse_game_data(raw_data())
        build_player(data, raw_loadout())
        self.assertEqual(len(build_party(data, raw_party())), 3)

    def test_unknown_key_rejected(self) -> None:
        raw = raw_data()
        raw["skills"]["slant"]["startpu"] = 3
        with self.assertRaises(DataError):
            parse_game_data(raw)

    def test_hit_outside_active_rejected(self) -> None:
        raw = raw_data()
        raw["skills"]["slant"]["hits"][0]["at"] = raw["skills"]["slant"]["active"]
        with self.assertRaises(DataError):
            parse_game_data(raw)

    def test_beast_has_one_stance(self) -> None:
        raw = raw_data()
        raw["enemies"]["dire_wolf"]["stances"] = ["guard", "evade"]
        with self.assertRaisesRegex(DataError, "최대 1개"):
            parse_game_data(raw)

    def test_enemies_never_parry(self) -> None:
        raw = raw_data()
        w = raw["enemies"]["dire_wolf"]
        w["sword_skill_user"], w["high_grade"], w["stances"] = True, True, ["guard", "parry"]
        with self.assertRaisesRegex(DataError, "패리"):
            parse_game_data(raw)
        w["stances"] = ["guard", "evade"]
        parse_game_data(raw)

    def test_pattern_must_use_known_attack(self) -> None:
        raw = raw_data()
        raw["enemies"]["dire_wolf"]["patterns"][0].append("no_such")
        with self.assertRaises(DataError):
            parse_game_data(raw)


class LoadoutTest(unittest.TestCase):
    def setUp(self) -> None:
        self.data = parse_game_data(raw_data())

    def test_stat_sum_level1(self) -> None:
        lo = raw_loadout()
        lo["stats"] = {"str": 6, "agi": 6}
        with self.assertRaisesRegex(LoadoutError, "스탯 합"):
            build_player(self.data, lo)

    def test_too_many_slots(self) -> None:
        lo = raw_loadout()
        lo["skill_slots"] = ["one_handed_sword", "weapon_defense", "acrobatics"]
        with self.assertRaisesRegex(LoadoutError, "최대 2개"):
            build_player(self.data, lo)

    def test_proficiency_required(self) -> None:
        lo = raw_loadout()
        lo["sword_skills"] = ["slant", "rage_spike"]
        with self.assertRaisesRegex(LoadoutError, "숙련도"):
            build_player(self.data, lo)

    def test_shield_needs_skill(self) -> None:
        lo = raw_loadout()
        lo["shield"] = "small_buckler"
        with self.assertRaisesRegex(LoadoutError, "shield_equip"):
            build_player(self.data, lo)


class CombatTest(unittest.TestCase):
    def make(self, raw: dict | None = None, enemy: str = "dire_wolf", count: int = 1, solo: bool = False) -> Battle:
        data = parse_game_data(raw or raw_data())
        party = (build_player(data, raw_loadout()),) if solo else build_party(data, raw_party())
        return Battle(data, party, data.enemies[enemy], count, 1, "full")

    def place(self, b: Battle, enemy_cell=(0, 0), facing: int = 3, allies=()) -> None:
        """적을 enemy_cell에 facing 방향으로, 아군을 allies 칸에 둔다 (남는 아군은 멀리)."""
        e = b.enemies[0]
        e.cell, e.facing = enemy_cell, facing
        far = [(-5, 5), (-4, 5), (-5, 4)]
        for i, a in enumerate(b.allies):
            a.cell = allies[i] if i < len(allies) else far[i]

    def events(self, b: Battle, kind: str) -> list[L.LogEvent]:
        return [e for e in b.log if e.event == kind]

    def run_until(self, b: Battle, kind: str, limit: int = 200) -> None:
        """다른 아군은 기다리게 하면서 kind 사건이 날 때까지 진행."""
        for _ in range(limit):
            req = b.advance()
            if req is None or self.events(b, kind):
                return
            b.submit(next(o.choice for o in req.options if o.choice.kind == "wait"))

    def test_stance_chance_uses_stats(self) -> None:
        b = self.make(solo=True)
        # 가드: 기본 6500 + 근력 5 × 150 + 무기 방어 800 / 회피: 기본 3500 + 민첩 5 × 100 (상성은 미정이라 없음)
        self.assertEqual(stance_chance(b.data, b.allies[0], "guard"), 8050)
        self.assertEqual(stance_chance(b.data, b.allies[0], "evade"), 4000)

    def test_decision_order_by_agi(self) -> None:
        b = self.make()
        req = b.advance()
        self.assertEqual(b.fighters[req.actor].name, "딜러A")  # 민첩 6

    def test_sword_skill_interrupts_premotion(self) -> None:
        b = self.make(solo=True)
        self.place(b, allies=[(-1, 0)])
        p, e = b.allies[0], b.enemies[0]
        b._begin_attack(e, e.enemy_def.attack_by_id("lunge"), p.index)    # 발생 20f (첫 타 21f)
        b._begin_attack(p, b.data.actions["slant"], e.index)             # 첫 타 16f
        b.advance()
        self.assertEqual([x.ref for x in self.events(b, L.INTERRUPT)], ["lunge"])
        self.assertEqual([x.actor for x in self.events(b, L.HIT)], ["ally0"])

    def test_basic_attack_does_not_interrupt(self) -> None:
        b = self.make(solo=True)
        self.place(b, allies=[(-1, 0)])
        p, e = b.allies[0], b.enemies[0]
        b._begin_attack(e, e.enemy_def.attack_by_id("bite"), p.index)     # 첫 타 12f
        b._begin_attack(p, b.data.actions["basic_attack"], e.index)      # 첫 타 9f
        b.advance()
        self.assertFalse(self.events(b, L.INTERRUPT))
        self.assertEqual([x.actor for x in self.events(b, L.HIT)], ["ally0", "enemy0"])

    def test_clash_when_hits_align(self) -> None:
        raw = raw_data()
        raw["skills"]["slant"]["startup"] = 10   # 첫 타 12f = 물어뜯기 첫 타
        b = self.make(raw, solo=True)
        self.place(b, allies=[(-1, 0)])
        p, e = b.allies[0], b.enemies[0]
        b._begin_attack(p, b.data.actions["slant"], e.index)
        b._begin_attack(e, e.enemy_def.attack_by_id("bite"), p.index)
        b.advance()
        clashes = self.events(b, L.CLASH)
        self.assertEqual([x.ref for x in clashes], ["win"])
        self.assertFalse(self.events(b, L.HIT))

    def test_place_attack_misses_after_moving_out(self) -> None:
        b = self.make(with_wolf_attacks(raw_data(), TEST_SWEEP), solo=True)
        self.place(b, allies=[(-1, 0)])
        p, e = b.allies[0], b.enemies[0]
        b._begin_attack(e, e.enemy_def.attack_by_id("sweep"), p.index)   # 대상 칸 + 주위 1칸, 첫 타 32f
        b._begin_move(p, (-3, 0), 2 * 925)                                # 2칸 약 18.5f
        while b.advance() is not None and not self.events(b, L.MISS_ZONE):
            b.submit(next(o.choice for o in b._request.options if o.choice.kind == "wait"))
        self.assertEqual(len(self.events(b, L.MISS_ZONE)), 1)
        self.assertEqual(p.hp, p.max_hp)

    def test_front_hate_holds_until_damage_exceeds(self) -> None:
        b = self.make()
        tank, dealer = b.allies[0], b.allies[1]
        self.place(b, allies=[add((0, 0), DIRS[3]), add((0, 0), DIRS[0])])   # 탱커는 정면(서), 딜러는 등 뒤
        e = b.enemies[0]
        e.target = tank.index
        br = b.brain(e)
        br.record(b.now, dealer.index, b.rules.front_hate)
        self.assertEqual(b._hate_target(e), tank.index)          # 같으면 지금 대상 유지
        br.record(b.now, dealer.index, 1)
        self.assertEqual(b._hate_target(e), dealer.index)        # 정면 헤이트를 넘으면 돌아선다

    def test_accumulated_damage_forgotten_after_memory(self) -> None:
        b = self.make()
        tank, dealer = b.allies[0], b.allies[1]
        self.place(b, allies=[add((0, 0), DIRS[3]), add((0, 0), DIRS[0])])
        e = b.enemies[0]
        e.target = tank.index
        b.brain(e).record(0, dealer.index, 999)
        b.now = b.brain(e).memory_t + 1
        self.assertEqual(b._hate_target(e), tank.index)

    def test_retarget_takes_max_of_turn_and_reload(self) -> None:
        b = self.make()
        tank, dealer = b.allies[0], b.allies[1]
        self.place(b, allies=[add((0, 0), DIRS[3]), add((0, 0), DIRS[0])])   # 딜러는 등 뒤
        e = b.enemies[0]
        e.target, e.busy = tank.index, READY
        b.brain(e).record(b.now, dealer.index, 100)
        b._enemy_decide(e)
        self.assertEqual(e.busy, RETARGET)
        self.assertEqual(e.target, dealer.index)
        turn = b.rules.turn_t
        self.assertEqual(e.free_at - b.now, max(turn, b.rules.reload_t))
        self.assertEqual([x.ref for x in self.events(b, L.RETARGET)], [dealer.cid])

    def test_trigger_attack_makes_enemy_react(self) -> None:
        b = self.make(solo=True)
        self.place(b, allies=[(-1, 0)])
        p, e = b.allies[0], b.enemies[0]
        m = b.brain(e).model(b._key(p))
        m.person_bp = b.rules.ai.trigger_step_bp            # 트리거 1개
        m.seen_ids, m.seen_counts = ["slant"], [3]
        e.busy = WAIT
        b._begin_attack(p, b.data.actions["slant"], e.index)
        b.advance()
        reacts = self.events(b, L.REACT)
        self.assertEqual([(x.ref, x.info) for x in reacts], [("evade", "slant")])
        self.assertTrue(self.events(b, L.STANCE_OK) or self.events(b, L.STANCE_FAIL))

    def test_untriggered_attack_no_reaction(self) -> None:
        b = self.make(solo=True)
        self.place(b, allies=[(-1, 0)])
        p, e = b.allies[0], b.enemies[0]
        e.busy = WAIT
        b._begin_attack(p, b.data.actions["slant"], e.index)
        b.advance()
        self.assertFalse(self.events(b, L.REACT))

    def test_pattern_steps_and_interrupt_skips(self) -> None:
        b = self.make(solo=True)
        self.place(b, allies=[(-1, 0)])
        p, e = b.allies[0], b.enemies[0]
        e.pattern, e.step, e.busy = 0, 0, READY
        e.target = p.index
        b._enemy_decide(e)
        first = e.enemy_def.patterns[0][0].attack_id
        self.assertEqual((e.busy, e.action.ref, e.step), (ATTACK, first, 1))
        b._apply_stun(e, b.now + 100)                # 프리모션 중 끊김 → 다음 단계로
        self.assertEqual(e.step, 1)

    def test_friendly_fire_halves_and_interrupts(self) -> None:
        b = self.make()
        a0, a1 = b.allies[0], b.allies[1]
        self.place(b, allies=[(-1, 0), (-1, 1)])     # a1은 a0의 정면 3칸 부채꼴 안
        e = b.enemies[0]
        e.busy = WAIT
        b._begin_stance(a1, "guard", 0)
        b._begin_attack(a0, b.data.actions["horizontal"], e.index)
        self.run_until(b, L.FRIENDLY_FIRE)
        ff = self.events(b, L.FRIENDLY_FIRE)
        self.assertEqual([x.info for x in ff], [a1.cid])
        full = b.data.actions["horizontal"].hits[0].power_bp * a0.power // 10000 - a1.defense
        self.assertLess(ff[0].value, full)
        self.assertIn(a1.busy, (HITSTUN, READY))
        self.assertIsNone(a1.stance)

    def test_rest_regen_only_when_not_targeted(self) -> None:
        b = self.make()
        tank, dealer = b.allies[0], b.allies[1]
        self.place(b, allies=[(-1, 0), (-4, 2)])
        e = b.enemies[0]
        e.target = tank.index
        e.busy = WAIT
        e.free_token += 1
        for a in (tank, dealer):
            a.hp = 10
            b._begin_wait(a, 10 ** 6)
        b.now = 0
        b._on_rest()
        self.assertEqual(tank.hp, 10)
        self.assertEqual(dealer.hp, 10 + b.rules.rest_regen)
        dealer.boost_until, dealer.boost_bp = 10 ** 6, 40000
        b._on_rest()
        self.assertEqual(dealer.hp, 10 + b.rules.rest_regen * 5)

    def test_surround_attack(self) -> None:
        raw = with_wolf_attacks(raw_data(), TEST_SPIN)
        raw["enemies"]["dire_wolf"]["surround_attack"], raw["enemies"]["dire_wolf"]["surround_count"] = "spin", 3
        b = self.make(raw)
        self.place(b, allies=[add((0, 0), DIRS[3]), add((0, 0), DIRS[2]), add((0, 0), DIRS[4])])
        e = b.enemies[0]
        e.target, e.busy = b.allies[0].index, READY
        b.brain(e).records.clear()
        b._enemy_decide(e)
        self.assertEqual(e.action.ref, e.enemy_def.surround_attack)

    def test_large_footprint_blocks_movement(self) -> None:
        b = self.make(enemy="frenzy_boar")
        e = b.enemies[0]
        self.assertEqual(len(e.cells()), 3)
        for a in b.allies:
            reach = {c for c, _ in b.reachable(a)}
            self.assertFalse(reach & set(e.cells()))

    def test_break_then_rise_resets_aggro(self) -> None:
        b = self.make(enemy="frenzy_boar")
        e = b.enemies[0]
        self.assertTrue(b._add_break(e, e.enemy_def.break_max))
        self.assertEqual(e.busy, BROKEN)
        b.brain(e).record(b.now, 1, 50)
        b._rise(e)
        self.assertEqual(self.events(b, L.RISE)[0].info, "aggro_reset")
        self.assertEqual(b.brain(e).records, [])

    def test_weapon_swap_triggers_reload(self) -> None:
        lo = raw_loadout()
        lo["skill_slots"] = ["one_handed_sword", "rapier"]
        lo["spare_weapons"] = ["iron_rapier"]
        lo["quick_change"] = True
        data = parse_game_data(raw_data())
        b = Battle(data, (build_player(data, lo),), data.enemies["dire_wolf"], 1, 1, "full")
        p = b.allies[0]
        b._begin_simple(p, "swap", SWAP, b.rules.quick_swap_t, "", 0)
        b.advance()
        reloads = self.events(b, L.RELOAD)
        self.assertEqual([x.info for x in reloads], ["new"])
        self.assertEqual(p.weapon.weapon.id, "iron_rapier")


if __name__ == "__main__":
    unittest.main()
