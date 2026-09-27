"""데이터 파일 읽기 (경계 레이어)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.defs import GameData, parse_game_data
from core.loadout import PlayerSpec, build_party, build_player

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
DATA_FILES = ("rules", "skills", "weapons", "armors", "items", "slot_skills", "enemies")


def read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_game_data(data_dir: Path = DATA_DIR) -> GameData:
    return parse_game_data({name: read_json(data_dir / f"{name}.json") for name in DATA_FILES})


def load_setup(data_dir: Path = DATA_DIR, party: Path | None = None, solo: bool = False) -> tuple[GameData, tuple[PlayerSpec, ...]]:
    """solo면 loadout.json 한 명, 아니면 party.json (또는 party로 준 파일)."""
    data = load_game_data(data_dir)
    if solo:
        return data, (build_player(data, read_json(party or data_dir / "loadout.json")),)
    return data, build_party(data, read_json(party or data_dir / "party.json"))
