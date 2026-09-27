"""약공격 자동 대응, 시야각, 수동 대응, 딜레이 캐치."""
import unittest

from app.loader import DATA_DIR, DATA_FILES, read_json
from core import log as L
from core.battle import Battle
from core.combatant import ATTACK, WAIT
from core.defs import parse_game_data
from core.hexgrid import DIRS, add
from core.loadout import build_party, build_player


def raw_data(auto_bp: int | None = None) -> dict:
    raw = {name: read_json(DATA_DIR / f"{name}.json") for name in DATA_FILES}
    if auto_bp is not None:   # 확률 판정을 확정으로 만들어 규칙만 확인
        raw["rules"]["max_success_bp"] = 10000
        raw["rules"]["auto_defense"]["base_bp"] = auto_bp
        raw["rules"]["auto_defense"]["stat_bp"] = 0
        for e in raw["enemies"].values():
            if isinstance(e, dict):
                e["auto_defense_bp"] = auto_bp
    return raw


class AutoDefenseTest(unittest.TestCase):
    def make(self, auto_bp: int | None = 10000, solo: bool = True) -> Battle:
        data = parse_game_data(raw_data(auto_bp))
        party = (build_player(data, read_json(DATA_DIR / "loadout.json")),) if solo else build_party(data, read_json(DATA_DIR / "party.json"))
        b = Battle(data, party, data.enemies["dire_wolf"], 1, 1, "full")
        e = b.enemies[0]
        e.cell, e.facing = (0, 0), 3                  # 서쪽을 본다
        far = [(-5, 5), (-4, 5), (-5, 4)]
        for i, a in enumerate(b.allies):
            a.cell = far[i]
        return b

    def events(self, b: Battle, kind: str) -> list[L.LogEvent]:
        return [x for x in b.log if x.event == kind]

    def run_until(self, b: Battle, kind: str, limit: int = 200) -> None:
        for _ in range(limit):
            req = b.advance()
            if req is None or self.events(b, kind):
                return
            b.submit(next(o.choice for o in req.options if o.choice.kind == "wait"))

    def idle_enemy(self, b: Battle) -> None:
        e = b.enemies[0]
        b._begin_wait(e, 10 ** 6)

    def test_enemy_auto_evades_basic_and_catch_follows(self) -> None:
        b = self.make()
        p, e = b.allies[0], b.enemies[0]
        p.cell = add(e.cell, DIRS[3])                 # 정면
        self.idle_enemy(b)
        b._begin_attack(p, b.data.actions["basic_attack"], e.index)
        self.run_until(b, L.HIT)
        ok = self.events(b, L.AUTO_OK)
        self.assertEqual([(x.actor, x.ref) for x in ok], [("enemy0", "evade")])
        catch = self.events(b, L.CATCH)
        self.assertEqual((catch[0].actor, catch[0].value), ("ally0", b.rules.auto.evade_catch_t))
        # 딜레이 캐치: 굳은 플레이어에게 적의 약공격이 들어간다
        hits = [x for x in self.events(b, L.HIT) if x.actor == "enemy0"]
        self.assertEqual(hits[0].ref, "snap")
        self.assertFalse([x for x in self.events(b, L.AUTO_OK) if x.actor == "ally0"])

    def test_guarding_enemy_catches_with_its_weak_attack(self) -> None:
        """가드하는 적(보어: 반응 8f, 들이받기 첫 타 14f)도 막아 낸 뒤 반드시 되받아친다."""
        data = parse_game_data(raw_data(10000))
        b = Battle(data, (build_player(data, read_json(DATA_DIR / "loadout.json")),), data.enemies["frenzy_boar"], 1, 1, "full")
        p, e = b.allies[0], b.enemies[0]
        e.cell, e.facing = (0, 0), 3
        p.cell = add(e.cell, DIRS[3])
        b._begin_wait(e, 3000)
        b._begin_attack(p, data.actions["basic_attack"], e.index)
        self.run_until(b, L.HIT)
        self.assertEqual([(x.actor, x.ref) for x in self.events(b, L.AUTO_OK)], [("enemy0", "guard")])
        hits = self.events(b, L.HIT)
        self.assertEqual((hits[0].actor, hits[0].ref), ("enemy0", "gore"))
        self.assertIn(":recovery", hits[0].info)

    def test_no_auto_from_behind(self) -> None:
        b = self.make()
        p, e = b.allies[0], b.enemies[0]
        p.cell = add(e.cell, DIRS[0])                 # 후면 (울프 시야는 뒤쪽 측면까지)
        self.idle_enemy(b)
        b._begin_attack(p, b.data.actions["basic_attack"], e.index)
        self.run_until(b, L.HIT)
        self.assertFalse(self.events(b, L.AUTO_OK) + self.events(b, L.AUTO_FAIL))
        self.assertEqual(self.events(b, L.HIT)[0].actor, "ally0")

    def test_back_side_is_within_beast_vision(self) -> None:
        b = self.make()
        p, e = b.allies[0], b.enemies[0]
        p.cell = add(e.cell, DIRS[1])                 # 뒤쪽 측면
        self.idle_enemy(b)
        b._begin_attack(p, b.data.actions["basic_attack"], e.index)
        self.run_until(b, L.AUTO_OK)
        self.assertEqual(len(self.events(b, L.AUTO_OK)), 1)

    def test_sword_skill_not_auto_defended(self) -> None:
        b = self.make()
        p, e = b.allies[0], b.enemies[0]
        p.cell = add(e.cell, DIRS[3])
        self.idle_enemy(b)
        b._begin_attack(p, b.data.actions["slant"], e.index)
        self.run_until(b, L.HIT)
        self.assertFalse(self.events(b, L.AUTO_OK))
        self.assertEqual(self.events(b, L.HIT)[0].ref, "slant")

    def test_ally_auto_guard_by_strength_and_catch(self) -> None:
        b = self.make(solo=False)
        tank, e = b.allies[0], b.enemies[0]                    # 탱커: 근력 6 > 민첩 4
        tank.cell = add(e.cell, DIRS[3])
        tank.focus = e.index
        b._begin_wait(tank, 10 ** 6)
        for a in b.allies[1:]:
            b._begin_wait(a, 10 ** 6)
        b._begin_attack(e, e.enemy_def.attack_by_id("snap"), tank.index)
        self.run_until(b, L.CATCH)
        self.assertEqual([(x.actor, x.ref) for x in self.events(b, L.AUTO_OK)], [("ally0", "guard")])
        self.assertEqual(self.events(b, L.CATCH)[0].value, b.rules.auto.guard_catch_t)
        req = b.advance()
        self.assertEqual(req.actor, tank.index)                # 막은 쪽에게 곧바로 차례
        self.assertEqual(e.busy, ATTACK)
        self.assertTrue(any(o.choice.kind == "basic" and o.punish for o in req.options))

    def test_ally_prefers_evade_with_higher_agi(self) -> None:
        b = self.make(solo=False)
        self.assertEqual(b.auto_kind(b.allies[0]), "guard")    # 근력 6 / 민첩 4
        self.assertEqual(b.auto_kind(b.allies[1]), "evade")    # 근력 4 / 민첩 6
        self.assertEqual(b.auto_kind(b.allies[2]), "guard")    # 5 / 5 동률 → 가드

    def test_manual_stance_vs_weak_is_near_certain(self) -> None:
        b = self.make(auto_bp=0)
        p, e = b.allies[0], b.enemies[0]
        p.cell = add(e.cell, DIRS[3])
        b._begin_stance(p, "evade", 0)                          # 회피 기본은 낮지만 약공격이면 거의 확정
        b._begin_attack(e, e.enemy_def.attack_by_id("snap"), p.index)
        self.run_until(b, L.STANCE_OK)
        self.assertEqual([x.value for x in self.events(b, L.STANCE_OK)], [b.rules.auto.weak_manual_bp])

    def test_ally_facing_unchanged_by_move(self) -> None:
        b = self.make(solo=False)
        a, e = b.allies[1], b.enemies[0]
        a.cell = (-2, 0)
        a.focus = e.index
        before = b.facing_of(a)
        self.assertEqual(before, 0)                            # 동쪽의 적을 본다
        a.cell = (-2, 2)                                       # 옆으로 비켜서도 시선은 적에게
        after = b.facing_of(a)
        self.assertEqual(after, b.facing_of(a))
        self.assertNotEqual(after, 3)


if __name__ == "__main__":
    unittest.main()
