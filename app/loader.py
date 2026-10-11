"""데이터 파일 읽기 (경계 레이어)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.battle import SetupError
from core.defs import EnemyDef, GameData, parse_game_data
from core.loadout import PlayerSpec, build_party, build_player

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
DATA_FILES = ("rules", "skills", "weapons", "armors", "items", "slot_skills", "enemies")


def read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def read_raw(data_dir: Path = DATA_DIR) -> dict[str, Any]:
    """데이터 파일 원본 dict (평가 도구가 일부 값을 바꿔 비교할 때)."""
    return {name: read_json(data_dir / f"{name}.json") for name in DATA_FILES}


def load_game_data(data_dir: Path = DATA_DIR) -> GameData:
    return parse_game_data(read_raw(data_dir))


def parse_enemies(data: GameData, spec: str) -> tuple[EnemyDef, ...]:
    """적 구성 문자열 → 한 마리마다 정의 하나. 예: "dire_wolf:2,frenzy_boar" (수를 빼면 1마리)."""
    out: list[EnemyDef] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        eid, _, n = part.partition(":")
        if eid not in data.enemies:
            raise SetupError(f"적 '{eid}'가 없습니다. 가능: {sorted(data.enemies)}")
        count = int(n) if n else 1
        if count < 1:
            raise SetupError(f"적 수는 1 이상: {part}")
        out += [data.enemies[eid]] * count
    if not out:
        raise SetupError("적 구성이 비었습니다")
    return tuple(out)


def load_setup(data_dir: Path = DATA_DIR, party: Path | None = None, solo: bool = False) -> tuple[GameData, tuple[PlayerSpec, ...]]:
    """solo면 loadout.json 한 명, 아니면 party.json (또는 party로 준 파일)."""
    data = load_game_data(data_dir)
    if solo:
        return data, (build_player(data, read_json(party or data_dir / "loadout.json")),)
    return data, build_party(data, read_json(party or data_dir / "party.json"))
