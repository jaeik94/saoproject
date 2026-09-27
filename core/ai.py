"""적의 기억: 상대별 적응, 즉시 반응할 공격(트리거), 누적 피해(헤이트), 재로드.

지능은 기억 길이(memory_t) 하나이고, 나머지는 rules.ai 계수로 여기서 뽑는다.
행동 순서(패턴)와 대상 선택은 battle이 공간 정보와 함께 처리한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.defs import EnemyDef, Rules
from core.formulas import BP


@dataclass(slots=True)
class AdaptModel:
    """상대 한 명과 그 싸움 패턴(무기 계열)에 대한 모델."""
    key: str
    ally: int
    person_bp: int = 0
    person_rem: int = 0
    last_tick: int = 0
    seen_ids: list[str] = field(default_factory=list)      # 처음 본 순서
    seen_counts: list[int] = field(default_factory=list)


@dataclass(slots=True)
class ModelLoad:
    reload_t: int
    remembered: bool
    forgotten: list[str]


@dataclass(slots=True, frozen=True)
class DamageRecord:
    tick: int
    ally: int
    amount: int


class EnemyBrain:
    def __init__(self, enemy: EnemyDef, rules: Rules) -> None:
        self.enemy = enemy
        self.rules = rules
        ai = rules.ai
        tps = rules.ticks_per_sec
        self.memory_t = rules.memory_t(enemy.intelligence)
        self.person_gain_per_sec = self.memory_t * ai.person_rate // tps
        self.tech_gain = self.memory_t * ai.tech_rate // tps
        self.retention_t = self.memory_t * ai.retention_mult
        self.capacity = 1 + self.memory_t // ai.capacity_per_t
        self.models: list[AdaptModel] = []     # 오래된 것부터
        self.current: AdaptModel | None = None
        self.tech: dict[str, int] = {}          # 공격 id → 기술 적응(bp). 조회만 한다
        self.records: list[DamageRecord] = []

    # ------------------------------------------------------------ 적응
    def accrue(self, now: int) -> None:
        """지금 상대(대상)를 마주한 시간만큼 사람 적응이 오른다."""
        m = self.current
        if m is None:
            return
        elapsed = now - m.last_tick
        if elapsed > 0:
            total = elapsed * self.person_gain_per_sec + m.person_rem
            tps = self.rules.ticks_per_sec
            m.person_bp = min(self.rules.ai.person_cap_bp, m.person_bp + total // tps)
            m.person_rem = total % tps
        m.last_tick = now

    def load(self, key: str, ally: int, now: int) -> ModelLoad | None:
        """새 상대(또는 같은 상대의 다른 패턴)의 모델을 불러온다. 이미 지금 모델이면 None."""
        self.accrue(now)
        if self.current is not None and self.current.key == key:
            return None
        forgotten = self._forget(now)
        found = next((m for m in self.models if m.key == key), None)
        if found is not None:
            reload_t, remembered = self.rules.reload_remembered_t, True
            self.models.remove(found)
        else:
            reload_t, remembered = self.rules.reload_t, False
            carried = 0
            for m in self.models:
                if m.ally == ally:
                    carried = max(carried, m.person_bp * self.rules.ai.profile_carry_bp // BP)
            found = AdaptModel(key, ally, person_bp=carried)
        found.last_tick = now
        self.models.append(found)
        self.current = found
        while len(self.models) > self.capacity:
            forgotten.append(self.models.pop(0).key)
        return ModelLoad(reload_t, remembered, forgotten)

    def _forget(self, now: int) -> list[str]:
        out: list[str] = []
        keep: list[AdaptModel] = []
        for m in self.models:
            if m is not self.current and now - m.last_tick > self.retention_t:
                out.append(m.key)
            else:
                keep.append(m)
        self.models = keep
        return out

    def model(self, key: str) -> AdaptModel | None:
        return next((m for m in self.models if m.key == key), None)

    def observe(self, key: str, attack_id: str) -> None:
        """기억 중인 상대의 공격을 보면 기술 적응과 목격 횟수가 오른다."""
        m = self.model(key)
        if m is None:
            return
        if attack_id in m.seen_ids:
            m.seen_counts[m.seen_ids.index(attack_id)] += 1
        else:
            m.seen_ids.append(attack_id)
            m.seen_counts.append(1)
        self.tech[attack_id] = min(self.rules.ai.tech_cap_bp, self.tech.get(attack_id, 0) + self.tech_gain)

    def triggers(self, key: str) -> list[str]:
        """즉시 반응하는 공격: 사람 적응에 따라 개수가 늘고, 자주 본 공격부터 (동률이면 먼저 본 것)."""
        m = self.model(key)
        if m is None:
            return []
        n = m.person_bp // self.rules.ai.trigger_step_bp
        order = sorted(range(len(m.seen_ids)), key=lambda i: (-m.seen_counts[i], i))
        return [m.seen_ids[i] for i in order[:n]]

    def stance_bonus(self, key: str, attack_id: str) -> int:
        m = self.model(key)
        person = m.person_bp if m else 0
        ai = self.rules.ai
        return (person * ai.def_person_bp + self.tech.get(attack_id, 0) * ai.def_tech_bp) // BP

    # ------------------------------------------------------------ 헤이트
    def record(self, now: int, ally: int, amount: int) -> None:
        if amount > 0:
            self.records.append(DamageRecord(now, ally, amount))

    def prune(self, now: int) -> None:
        self.records = [r for r in self.records if now - r.tick <= self.memory_t]

    def accumulated(self, ally: int) -> int:
        total = 0
        for r in self.records:
            if r.ally == ally:
                total += r.amount
        return total

    def cut_others(self, keep_ally: int, cut_bp: int) -> None:
        self.records = [r if r.ally == keep_ally else DamageRecord(r.tick, r.ally, r.amount * (BP - cut_bp) // BP)
                        for r in self.records]
