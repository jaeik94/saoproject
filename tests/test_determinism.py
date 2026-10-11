"""같은 시드와 같은 입력은 항상 같은 결과를 내야 한다."""
import hashlib
import os
import subprocess
import sys
import unittest
from pathlib import Path

from app.batch import run_one
from app.loader import load_setup
from core.log import LogEvent

ROOT = Path(__file__).resolve().parent.parent


def digest(events: list[LogEvent]) -> str:
    h = hashlib.sha256()
    for e in events:
        h.update(f"{e.tick}|{e.actor}|{e.event}|{e.ref}|{e.value}|{e.info}\n".encode())
    return h.hexdigest()


class DeterminismTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data, cls.party = load_setup()
        cls.solo = load_setup(solo=True)[1]

    def run_battle(self, seed: int, bot: str = "party", knowledge: str = "none", enemy: str = "dire_wolf",
                   count: int = 1, solo: bool = False) -> list[LogEvent]:
        return run_one(self.data, self.solo if solo else self.party, (self.data.enemies[enemy],) * count, seed, bot, knowledge)[1]

    def test_same_seed_same_log(self) -> None:
        for enemy in sorted(self.data.enemies):
            for count in (1, 2):
                for bot in ("party", "random"):
                    for knowledge in ("none", "full"):
                        for seed in (1, 7):
                            a = self.run_battle(seed, bot, knowledge, enemy, count)
                            b = self.run_battle(seed, bot, knowledge, enemy, count)
                            self.assertEqual(a, b, f"{enemy}x{count}/{bot}/{knowledge}/seed {seed}")

    def test_solo_same_seed_same_log(self) -> None:
        for seed in (1, 2, 3):
            self.assertEqual(self.run_battle(seed, solo=True), self.run_battle(seed, solo=True))

    def test_different_seeds_differ(self) -> None:
        logs = {digest(self.run_battle(s, count=2)) for s in range(1, 11)}
        self.assertGreater(len(logs), 5)

    def test_ticks_are_ordered_ints(self) -> None:
        for seed in range(1, 6):
            events = self.run_battle(seed, knowledge="full", count=3)
            prev = 0
            for e in events:
                self.assertIsInstance(e.tick, int)
                self.assertIsInstance(e.value, int)
                self.assertGreaterEqual(e.tick, prev)
                prev = e.tick

    def test_independent_of_python_hash_seed(self) -> None:
        """문자열 해시 순서에 결과가 의존하지 않는지 (다른 PYTHONHASHSEED로 별도 프로세스 실행)."""
        code = (
            "import hashlib; from app.batch import run_one; from app.loader import load_setup; "
            "d, p = load_setup(); h = hashlib.sha256(); "
            "[h.update(repr(e).encode()) for seed in range(1, 6) for e in run_one(d, p, (d.enemies['dire_wolf'],) * 2, seed, 'party', 'full')[1]]; "
            "print(h.hexdigest())"
        )
        outs = set()
        for hs in ("1", "2", "12345"):
            env = dict(os.environ, PYTHONHASHSEED=hs)
            r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
            outs.add(r.stdout.strip())
        self.assertEqual(len(outs), 1)


if __name__ == "__main__":
    unittest.main()


class MixedEnemiesTest(unittest.TestCase):
    """다른 종이 섞인 전투: 배치, 이름, 무리는 같은 종끼리만, 결정론."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.data, cls.party = load_setup()

    def mix(self) -> tuple:
        from app.loader import parse_enemies
        return parse_enemies(self.data, "dire_wolf:2,frenzy_boar,plains_wasp:2")

    def test_setup(self) -> None:
        from core.battle import Battle
        b = Battle(self.data, self.party, self.mix(), 1, "full")
        self.assertEqual([e.name for e in b.enemies], ["다이어 울프", "다이어 울프 2", "프렌지 보어", "초원 말벌", "초원 말벌 2"])
        cells = [c for f in b.fighters for c in f.cells()]
        self.assertEqual(len(cells), len(set(cells)))                         # 칸이 겹치지 않는다
        wasp = b.enemies[3]
        self.assertIs(b._pack_leader(b.enemies[1]), b.enemies[0])              # 울프 무리
        self.assertFalse(wasp.enemy_def.pack)
        self.assertFalse(b.enemies[2].provoked)                                # 보어는 비선공 그대로

    def test_same_seed_same_log(self) -> None:
        for seed in (1, 2, 3):
            a = run_one(self.data, self.party, self.mix(), seed, "party", "full")[1]
            b = run_one(self.data, self.party, self.mix(), seed, "party", "full")[1]
            self.assertEqual(a, b)


class HuntTest(unittest.TestCase):
    """연속 사냥: 시작 체력 지정, 같은 시드면 같은 결과."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.data, cls.party = load_setup()

    def test_start_hp(self) -> None:
        from core.battle import Battle, SetupError
        e = (self.data.enemies["dire_wolf"],)
        hp = tuple(p.max_hp - 10 for p in self.party)
        b = Battle(self.data, self.party, e, 1, "full", hp)
        self.assertEqual(tuple(a.hp for a in b.allies), hp)
        with self.assertRaises(SetupError):
            Battle(self.data, self.party, e, 1, "full", (0,) * len(self.party))

    def test_hunt_same_seed_same_result(self) -> None:
        from app.hunt import run_hunt
        e = (self.data.enemies["dire_wolf_elite"],)
        a = run_hunt(self.data, self.party, e, 3, 7000, 5, ["orig"])
        b = run_hunt(self.data, self.party, e, 3, 7000, 5, ["orig"])
        self.assertEqual(a, b)
