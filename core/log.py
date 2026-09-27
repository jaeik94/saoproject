"""사건 기록. 코어는 기록만 쌓고, 파일 저장과 문장화는 경계 레이어가 한다."""
from __future__ import annotations

from dataclasses import dataclass

# event 종류
START = "start"              # 행동 시작 (ref=행동 id, info=종류)
HIT = "hit"                  # 명중 (value=피해)
MISS_ZONE = "miss_zone"      # 장소 지정 공격이 빗나감
STANCE_OK = "stance_ok"      # 태세 성공 (ref=태세, value=성공률)
STANCE_FAIL = "stance_fail"
CLASH = "clash"              # 상쇄 (ref=win|draw|lose, 공격측 기준)
INTERRUPT = "interrupt"      # 프리모션 중단 (ref=중단된 행동)
AUTO_OK = "auto_ok"          # 약공격 자동 대응 성공 (actor=방어측, ref=태세, value=확률, info=공격)
AUTO_FAIL = "auto_fail"
CATCH = "catch"              # 딜레이 캐치 창 (actor=굳은 공격측, ref=막은 쪽, value=틱, info=태세)
REACT = "react"              # 적이 트리거 공격을 보고 태세로 대응 (ref=태세, info=공격)
RETARGET = "retarget"        # 적의 대상 전환 (ref=새 대상, value=걸리는 틱, info=turn/reload)
TURN = "turn"                # 적이 대상 쪽으로 방향 전환
FRIENDLY_FIRE = "friendly_fire"  # 아군 기술에 아군이 맞음 (ref=공격, info=맞은 아군, value=피해)
HOWL = "howl"
BREAK = "break"              # 무력화
RISE = "rise"                # 무력화에서 일어섬 (어그로 초기화)
HEAL = "heal"                # 휴식 회복
ITEM_USED = "item_used"
SWAP = "swap"
DURABILITY_WARN = "durability_warn"
EQUIP_BROKEN = "equip_broken"
RELOAD = "reload"            # 적 재로드 (value=틱)
FORGET = "forget"            # 적이 기억에서 상대 모델을 지움
MOVE_DONE = "move_done"
DEATH = "death"
END = "end"


@dataclass(slots=True, frozen=True)
class LogEvent:
    tick: int
    actor: str
    event: str
    ref: str = ""
    value: int = 0
    info: str = ""


@dataclass(slots=True, frozen=True)
class BattleResult:
    winner: str          # party | enemies | timeout
    end_tick: int
    ally_hp: tuple[int, ...]
    enemy_hp: tuple[int, ...]
