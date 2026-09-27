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
        return run_one(self.data, self.solo if solo else self.party, enemy, count, seed, bot, knowledge)[1]

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
            "[h.update(repr(e).encode()) for seed in range(1, 6) for e in run_one(d, p, 'dire_wolf', 2, seed, 'party', 'full')[1]]; "
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
