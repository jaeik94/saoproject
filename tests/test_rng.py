import unittest

from core.rng import Pcg32


class Pcg32Test(unittest.TestCase):
    def test_reference_vector(self) -> None:
        # pcg32-demo: pcg32_srandom_r(&rng, 42u, 54u)
        rng = Pcg32.seeded(42, 54)
        got = [rng.next_u32() for _ in range(6)]
        self.assertEqual(got, [0xA15C02B7, 0x7B47F409, 0xBA1D3330, 0x83D2F293, 0xBFA4784B, 0xCBED606E])

    def test_below_in_range(self) -> None:
        rng = Pcg32.seeded(7, 1)
        for bound in (1, 2, 3, 10, 10000, 2**31 + 11):
            for _ in range(200):
                v = rng.below(bound)
                self.assertTrue(0 <= v < bound)

    def test_roll_bp_extremes(self) -> None:
        rng = Pcg32.seeded(3, 1)
        self.assertFalse(any(rng.roll_bp(0) for _ in range(500)))
        self.assertTrue(all(rng.roll_bp(10000) for _ in range(500)))

    def test_pick_weighted_skips_zero(self) -> None:
        rng = Pcg32.seeded(5, 1)
        picks = {rng.pick_weighted([0, 3, 0, 1]) for _ in range(300)}
        self.assertEqual(picks, {1, 3})
        self.assertEqual(rng.pick_weighted([0, 0]), -1)


if __name__ == "__main__":
    unittest.main()
