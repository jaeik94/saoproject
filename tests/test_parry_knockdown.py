"""패리(상쇄 통합), 넘어짐, 방향별 피해, 비선공, 무리, 여러 칸 돌진, 착용 무게, 내구도."""
import unittest

from app.loader import DATA_DIR, DATA_FILES, read_json
from core import log as L
from core.battle import Battle
from core.combatant import MOVE, NO_TARGET, WAIT
from core.defs import parse_game_data
from core.formulas import BP, cells_distance, getup_ticks, wear_multiplier_bp
from core.hexgrid import DIRS, add
from core.loadout import build_player


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


def loadout() -> dict:
    return read_json(DATA_DIR / "loadout.json")


def rapier_loadout() -> dict:
    lo = loadout()
    lo["skill_slots"] = ["rapier", "acrobatics"]
    lo["proficiency"] = {"rapier": 0}
    lo["sword_skills"] = ["linear", "oblique"]
    lo["weapon"], lo["spare_weapons"] = "iron_rapier", []
    return lo


class Base(unittest.TestCase):
    def make(self, raw: dict | None = None, enemy: str = "dire_wolf", lo: dict | None = None, count: int = 1) -> Battle:
        data = parse_game_data(raw or raw_data())
        b = Battle(data, (build_player(data, lo or loadout()),), data.enemies[enemy], count, 1, "full")
        e = b.enemies[0]
        e.cell, e.facing = (0, 0), 3          # 서쪽을 본다
        b.allies[0].cell = (-1, 0) if len(e.footprint) == 1 else (-1, 0)
        return b

    def events(self, b: Battle, kind: str) -> list[L.LogEvent]:
        return [x for x in b.log if x.event == kind]

    def run_until(self, b: Battle, kind: str, limit: int = 200) -> None:
        for _ in range(limit):
            req = b.advance()
            if req is None or self.events(b, kind):
                return
            b.submit(next(o.choice for o in req.options if o.choice.kind == "wait"))


class ParryTest(Base):
    def test_full_parry_blocks_and_wins(self) -> None:
        raw = raw_data()
        raw["skills"]["slant"]["startup"] = 10          # 슬랜트 첫 타 12f = 물어뜯기 첫 타 12f
        b = self.make(raw)
        p, e = b.allies[0], b.enemies[0]
        b._begin_attack(p, b.data.actions["slant"], e.index)
        b._begin_attack(e, e.enemy_def.attack_by_id("bite"), p.index)
        self.run_until(b, L.CLASH)
        self.assertEqual([(x.value, x.info) for x in self.events(b, L.PARRY)], [(1, "1")])
        self.assertEqual([x.ref for x in self.events(b, L.CLASH)], ["win"])
        self.assertFalse([x for x in self.events(b, L.HIT)])     # 확률 없이 피해 0
        self.assertEqual(p.hp, p.max_hp)

    def test_partial_parry_only_blocks_matched_hits(self) -> None:
        b = self.make(with_wolf_attacks(raw_data(), TEST_FLURRY), lo=rapier_loadout())
        p, e = b.allies[0], b.enemies[0]
        # 오블리크 타격 18f·23f vs 연속 할퀴기 16f·23f·30f → 앞의 두 타만 막힌다
        b._begin_attack(p, b.data.actions["oblique"], e.index)
        b._begin_attack(e, e.enemy_def.attack_by_id("flurry"), p.index)
        self.run_until(b, L.HIT, 50)
        for _ in range(20):
            if [x for x in self.events(b, L.HIT) if x.actor == "enemy0"]:
                break
            req = b.advance()
            b.submit(next(o.choice for o in req.options if o.choice.kind == "wait"))
        self.assertEqual([x.value for x in self.events(b, L.PARRY)], [1, 2])
        self.assertFalse(self.events(b, L.CLASH))                 # 부분 패리는 위력 비교 없음
        self.assertEqual(len([x for x in self.events(b, L.HIT) if x.actor == "enemy0"]), 1)

    def test_losing_parry_knocks_back(self) -> None:
        raw = raw_data()
        next(a for a in raw["enemies"]["dire_wolf"]["attacks"] if a["id"] == "lunge")["startup"] = 8   # 도약 물기 첫 타 9f ≈ 리니어 9.5f
        b = self.make(raw, lo=rapier_loadout())
        p, e = b.allies[0], b.enemies[0]
        b._begin_attack(p, b.data.actions["linear"], e.index)
        b._begin_attack(e, e.enemy_def.attack_by_id("lunge"), p.index)
        self.run_until(b, L.CLASH)
        self.assertEqual([x.ref for x in self.events(b, L.CLASH)], ["lose"])   # 세검 리니어 < 강한 기술 도약 물기
        self.assertEqual(len(self.events(b, L.KNOCKBACK)), 1)
        self.assertEqual(cells_distance([p.cell], e.cells()), 2)

    def test_basic_attack_cannot_parry(self) -> None:
        raw = raw_data()
        raw["skills"]["basic_attack"]["startup"] = 11           # 일반 공격 첫 타 12f = 물어뜯기
        raw["skills"]["basic_attack"]["can_clash"] = True
        b = self.make(raw)
        p, e = b.allies[0], b.enemies[0]
        b._begin_attack(p, b.data.actions["basic_attack"], e.index)
        b._begin_attack(e, e.enemy_def.attack_by_id("bite"), p.index)
        self.run_until(b, L.HIT)
        self.assertFalse(self.events(b, L.PARRY))


class KnockdownTest(Base):
    def test_boar_charge_knocks_down(self) -> None:
        b = self.make(enemy="frenzy_boar")
        p, e = b.allies[0], b.enemies[0]
        e.provoked, e.target = True, p.index
        b._begin_wait(p, 10 ** 6)
        b._begin_attack(e, e.enemy_def.attack_by_id("charge"), p.index)
        self.run_until(b, L.KNOCKDOWN)
        kd = self.events(b, L.KNOCKDOWN)
        self.assertEqual((kd[0].actor, kd[0].ref), ("ally0", "charge"))
        self.assertGreaterEqual(b.now, kd[0].tick + kd[0].value)   # 다음 차례는 기상한 뒤
        # 기상 시간은 착용 무게(스몰 소드 4 + 가죽 1 = 5.0)만큼 늘어난다
        self.assertEqual(kd[0].value, b.rules.knockdown.getup_t * (BP + 50 * b.rules.knockdown.wear_bp) // BP)
        self.assertEqual(getup_ticks(b.rules, p), kd[0].value)

    def test_guard_prevents_knockdown(self) -> None:
        raw = raw_data()
        raw["rules"]["max_success_bp"] = 10000
        raw["rules"]["stances"]["guard"]["base_bp"] = 10000
        b = self.make(raw, enemy="frenzy_boar")
        p, e = b.allies[0], b.enemies[0]
        e.provoked, e.target = True, p.index
        b._begin_stance(p, "guard", 10 ** 6)
        b._begin_attack(e, e.enemy_def.attack_by_id("charge"), p.index)
        self.run_until(b, L.STANCE_OK)
        self.assertTrue(self.events(b, L.STANCE_OK))
        self.assertFalse(self.events(b, L.KNOCKDOWN))


class TraitsTest(Base):
    def test_beetle_front_is_hard(self) -> None:
        b = self.make(enemy="grass_beetle")
        p, e = b.allies[0], b.enemies[0]
        e.busy = WAIT                                  # 가만히 (자동 대응은 일반 공격에만)
        front = b._sector_bp(p, e)
        p.cell = add(e.cell, DIRS[0])                  # 후면
        back = b._sector_bp(p, e)
        self.assertEqual((front, back), (5000, 10000))

    def test_beetle_exposed_after_guarded(self) -> None:
        """딱정벌레의 공격을 가드하면 굳어 있는 동안 정면에서 쳐도 껍질·방어력이 무시된다."""
        raw = raw_data()
        raw["rules"]["max_success_bp"] = 10000
        raw["rules"]["stances"]["guard"]["base_bp"] = 10000
        b = self.make(raw, enemy="grass_beetle")
        p, e = b.allies[0], b.enemies[0]
        b._begin_stance(p, "guard", 0)
        b._begin_attack(e, e.enemy_def.attack_by_id("horn"), p.index)
        self.run_until(b, L.EXPOSED)
        self.assertTrue(self.events(b, L.EXPOSED))
        self.assertLess(b.now, e.exposed_until)
        basic = b.data.actions["basic_attack"]             # 가드 뒤의 창에는 일반 공격(첫 타 9f)이 들어간다
        b.submit(next(o.choice for o in b._request.options if o.choice.kind == "basic"))
        self.run_until(b, L.HIT)
        hit = [x for x in self.events(b, L.HIT) if x.actor == "ally0"][0]
        self.assertIn("+exposed", hit.info)
        self.assertEqual(hit.value, p.power * basic.hits[0].power_bp // BP)   # 정면 50%·방어 2 무시 (평소엔 4)

    def test_boar_is_passive_until_attacked(self) -> None:
        b = self.make(enemy="frenzy_boar")
        p, e = b.allies[0], b.enemies[0]
        self.assertEqual(e.target, NO_TARGET)
        for _ in range(10):                              # 한동안 기다려도 공격하지 않는다
            req = b.advance()
            b.submit(next(o.choice for o in req.options if o.choice.kind == "wait"))
        self.assertFalse([x for x in b.log if x.actor == "enemy0" and x.event == L.START and x.info == "attack"])
        b.advance()
        b._begin_attack(p, b.data.actions["slant"], e.index)
        self.assertEqual([x.ref for x in self.events(b, L.PROVOKE)], ["ally0"])
        self.assertEqual(e.target, p.index)

    def test_boar_back_is_weak(self) -> None:
        b = self.make(enemy="frenzy_boar")
        e = b.enemies[0]
        self.assertEqual(e.enemy_def.sector_damage_bp[3], 15000)

    def test_multi_cell_charge_ends_adjacent(self) -> None:
        b = self.make(enemy="frenzy_boar")
        p, e = b.allies[0], b.enemies[0]
        p.cell = (-3, 0)                                 # 3칸 떨어짐
        e.provoked, e.target = True, p.index
        self.assertEqual(cells_distance([p.cell], e.cells()), 3)
        b._begin_wait(p, 10 ** 6)
        b._begin_attack(e, e.enemy_def.attack_by_id("charge"), p.index)
        self.run_until(b, L.HIT)
        self.assertEqual(cells_distance([p.cell], e.cells()), 1)

    def test_wolf_pack_flanks(self) -> None:
        b = self.make(count=2)
        p = b.allies[0]
        w1, w2 = b.enemies
        p.cell = (0, 0)
        w1.cell, w1.facing = (1, 0), 3                   # 정면(동쪽)을 잡은 울프
        w2.cell = (4, -2)
        p.focus = w1.index
        w1.target = w2.target = p.index
        self.assertEqual(b.facing_of(p), 0)
        self.assertEqual(b._hate_target(w2), p.index)      # 무리는 우두머리의 대상을 따른다
        w2.busy = "ready"
        w2.facing = 3
        b._enemy_decide(w2)
        self.assertEqual(w2.busy, MOVE)                    # 옆·뒤로 돌기 위해 이동
        goal_sector_cells = [add(p.cell, DIRS[3]), add(p.cell, DIRS[2]), add(p.cell, DIRS[4])]
        before = cells_distance([(4, -2)], goal_sector_cells)
        self.assertLess(cells_distance([w2.action.dest], goal_sector_cells), before)


class WearTest(unittest.TestCase):
    def test_wear_multiplier(self) -> None:
        data = parse_game_data(raw_data())
        lo = loadout()
        lo["stats"] = {"str": 2, "agi": 8}                  # 한도 6 + 2 × 0.6 = 7.2 (스몰 소드 최소 근력 2)
        b = Battle(data, (build_player(data, lo),), data.enemies["dire_wolf"], 1, 1)
        p = b.allies[0]
        self.assertEqual(p.wear_weight, 50)                 # 스몰 소드 4 + 가죽 1
        self.assertEqual(wear_multiplier_bp(data.rules, p), BP)   # 한도 7.2 이하
        p.armor = data.armors["bronze_plate"]               # 4 + 4 = 8.0 > 7.2
        r = 8 * BP // 72
        self.assertEqual(wear_multiplier_bp(data.rules, p), BP + 2 * r + 10 * r * r // BP)

    def test_hp_per_level(self) -> None:
        data = parse_game_data(raw_data())
        lo = loadout()
        lo["level"] = 3
        self.assertEqual(build_player(data, lo).max_hp, 55 + 42 * 2)


if __name__ == "__main__":
    unittest.main()
