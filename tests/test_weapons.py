"""무기 6종: 빌드 유효성, 최소 근력, 무기별 기술의 사거리·범위·태그, 속도·착용 무게."""
import unittest

from app.loader import DATA_DIR, DATA_FILES, read_json
from core import log as L
from core.battle import Battle
from core.defs import parse_game_data
from core.formulas import BP, attack_timing, wear_limit, wear_multiplier_bp
from core.loadout import LoadoutError, build_player


def raw_data() -> dict:
    return {name: read_json(DATA_DIR / f"{name}.json") for name in DATA_FILES}


def builds() -> list[dict]:
    return read_json(DATA_DIR / "builds.json")["builds"]


def build_named(name: str) -> dict:
    return next(b for b in builds() if b["name"] == name)


class WeaponTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = parse_game_data(raw_data())

    def battle(self, build: str, allies: int = 1) -> Battle:
        spec = build_player(self.data, build_named(build))
        party = (spec,) + tuple(build_player(self.data, build_named("한손검")) for _ in range(allies - 1))
        b = Battle(self.data, party, self.data.enemies["dire_wolf"], 1, 1, "full")
        e = b.enemies[0]
        e.cell, e.facing = (0, 0), 3
        return b

    def events(self, b: Battle, kind: str) -> list[L.LogEvent]:
        return [x for x in b.log if x.event == kind]

    def test_all_builds_valid_and_within_wear_limit(self) -> None:
        families = set()
        for raw in builds():
            spec = build_player(self.data, raw)
            b = Battle(self.data, (spec,), self.data.enemies["dire_wolf"], 1, 1)
            p = b.allies[0]
            families.add(p.weapon_family)
            self.assertEqual(len(p.sword_skills), 2, raw["name"])
            self.assertLessEqual(p.wear_weight, wear_limit(self.data.rules, p), raw["name"])
        self.assertEqual(len(families), 6)

    def test_min_str(self) -> None:
        raw = build_named("양손도끼")
        raw["stats"] = {"str": 3, "agi": 7}
        with self.assertRaisesRegex(LoadoutError, "근력 4"):
            build_player(self.data, raw)

    def test_speed_changes_frames(self) -> None:
        rapier = self.battle("세검").allies[0]
        axe = self.battle("양손도끼").allies[0]
        linear, chop = self.data.actions["linear"], self.data.actions["heavy_chop"]
        self.assertEqual(attack_timing(self.data.rules, rapier, linear)[0], linear.startup_t * (BP - 1500) // BP)
        self.assertEqual(attack_timing(self.data.rules, axe, chop)[0], chop.startup_t * (BP + 1500) // BP)

    def test_overweight_axe_is_slow(self) -> None:
        raw = build_named("양손도끼")
        raw["stats"] = {"str": 4, "agi": 6}                  # 한도 8.4 < 착용 11
        p = Battle(self.data, (build_player(self.data, raw),), self.data.enemies["dire_wolf"], 1, 1).allies[0]
        self.assertGreater(wear_multiplier_bp(self.data.rules, p), BP)

    def test_spear_long_strike_only_at_two_cells(self) -> None:
        b = self.battle("창")
        p = b.allies[0]
        p.cell = (-1, 0)                                      # 붙어 있음
        req = b.advance()
        refs = {o.choice.ref for o in req.options if o.choice.kind == "skill"}
        self.assertIn("thrust", refs)
        self.assertNotIn("long_strike", refs)
        b2 = self.battle("창")
        b2.allies[0].cell = (-2, 0)                           # 2칸
        req2 = b2.advance()
        refs2 = {o.choice.ref for o in req2.options if o.choice.kind == "skill"}
        self.assertEqual(refs2, {"thrust", "long_strike"})

    def test_long_strike_pierces_through_ally(self) -> None:
        b = self.battle("창", allies=2)
        spear, front = b.allies
        e = b.enemies[0]
        spear.cell, front.cell = (-2, 0), (-1, 0)             # 창 뒤에서 아군 너머로 찌른다
        e.busy = "wait"
        b._begin_wait(front, 10 ** 6)
        b._begin_attack(spear, self.data.actions["long_strike"], e.index)
        for _ in range(50):
            req = b.advance()
            if self.events(b, L.FRIENDLY_FIRE) or req is None:
                break
            b.submit(next(o.choice for o in req.options if o.choice.kind == "wait"))
        self.assertEqual([x.info for x in self.events(b, L.FRIENDLY_FIRE)], [front.cid])

    def test_wide_sweep_hits_side_ally(self) -> None:
        b = self.battle("양손검", allies=2)
        gs, side = b.allies
        e = b.enemies[0]
        gs.cell, side.cell = (-1, 0), (-1, 1)                 # 옆 아군은 정면 부채꼴 안
        e.busy = "wait"
        b._begin_wait(side, 10 ** 6)
        b._begin_attack(gs, self.data.actions["wide_sweep"], e.index)
        for _ in range(50):
            req = b.advance()
            if self.events(b, L.FRIENDLY_FIRE) or req is None:
                break
            b.submit(next(o.choice for o in req.options if o.choice.kind == "wait"))
        self.assertEqual([x.info for x in self.events(b, L.FRIENDLY_FIRE)], [side.cid])

    def test_two_handed_hits_harder(self) -> None:
        def dmg(build: str, skill: str) -> int:
            p = self.battle(build).allies[0]
            a = self.data.actions[skill]
            return sum(p.power * h.power_bp // BP for h in a.hits)
        self.assertGreater(dmg("양손검", "blast"), dmg("한손검", "vertical"))
        self.assertGreater(dmg("양손도끼", "ground_crash"), dmg("한손 둔기", "heavy_crush"))
        self.assertTrue(self.data.actions["ground_crash"].knockdown_bp > 0)


if __name__ == "__main__":
    unittest.main()
