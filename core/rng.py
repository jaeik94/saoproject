"""PCG32 난수 생성기 (pcg32_random_r, XSH-RR). 언어 기본 난수를 쓰지 않기 위한 직접 구현."""
from __future__ import annotations

from dataclasses import dataclass

MASK32 = (1 << 32) - 1
MASK64 = (1 << 64) - 1
MULTIPLIER = 6364136223846793005
BP_SCALE = 10000


@dataclass(slots=True)
class Pcg32:
    state: int = 0
    inc: int = 1

    @staticmethod
    def seeded(seed: int, stream: int) -> Pcg32:
        rng = Pcg32(0, ((stream << 1) | 1) & MASK64)
        rng.next_u32()
        rng.state = (rng.state + (seed & MASK64)) & MASK64
        rng.next_u32()
        return rng

    def next_u32(self) -> int:
        old = self.state
        self.state = (old * MULTIPLIER + self.inc) & MASK64
        xorshifted = (((old >> 18) ^ old) >> 27) & MASK32
        rot = old >> 59
        return ((xorshifted >> rot) | (xorshifted << ((-rot) & 31))) & MASK32

    def below(self, bound: int) -> int:
        """[0, bound) 균등 정수 (편향 없는 거부 샘플링)."""
        if bound <= 0:
            raise ValueError("bound must be positive")
        threshold = ((1 << 32) - bound) % bound
        while True:
            r = self.next_u32()
            if r >= threshold:
                return r % bound

    def range_inclusive(self, lo: int, hi: int) -> int:
        return lo + self.below(hi - lo + 1)

    def roll_bp(self, chance_bp: int) -> bool:
        """만분율 확률 판정. 스트림을 일정하게 유지하려고 확률과 무관하게 항상 한 번 뽑는다."""
        return self.below(BP_SCALE) < chance_bp

    def pick_weighted(self, weights: list[int]) -> int:
        """가중치 목록에서 인덱스 하나. 가중치 합이 0이면 -1."""
        total = 0
        for w in weights:
            total += w
        if total <= 0:
            return -1
        r = self.below(total)
        for i, w in enumerate(weights):
            if r < w:
                return i
            r -= w
        return len(weights) - 1
