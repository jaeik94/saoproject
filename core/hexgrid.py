"""육각 그리드 (axial 좌표 q, r). 모두 정수 연산."""
from __future__ import annotations

Hex = tuple[int, int]

# 반시계 방향: 동, 북동, 북서, 서, 남서, 남동
DIRS: tuple[Hex, ...] = ((1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1))
DIR_NAMES: tuple[str, ...] = ("동", "북동", "북서", "서", "남서", "남동")

# 방향 구역: 정면, 앞쪽 측면, 뒤쪽 측면, 후면
FRONT, FRONT_SIDE, BACK_SIDE, BACK = 0, 1, 2, 3
SECTOR_NAMES: tuple[str, ...] = ("정면", "앞쪽 측면", "뒤쪽 측면", "후면")


def add(a: Hex, b: Hex) -> Hex:
    return (a[0] + b[0], a[1] + b[1])


def dist(a: Hex, b: Hex) -> int:
    dq, dr = a[0] - b[0], a[1] - b[1]
    return (abs(dq) + abs(dr) + abs(dq + dr)) // 2


def neighbors(h: Hex) -> list[Hex]:
    return [add(h, d) for d in DIRS]


def disc(center: Hex, radius: int) -> list[Hex]:
    """중심에서 radius 이내의 칸 (r, q 순으로 정렬)."""
    out: list[Hex] = []
    for r in range(-radius, radius + 1):
        for q in range(-radius, radius + 1):
            h = (center[0] + q, center[1] + r)
            if dist(h, center) <= radius:
                out.append(h)
    out.sort(key=lambda h: (h[1], h[0]))
    return out


def cube(h: Hex) -> tuple[int, int, int]:
    return (h[0], h[1], -h[0] - h[1])


def _dot(v: tuple[int, int, int], d: Hex) -> int:
    c = cube(d)
    return v[0] * c[0] + v[1] * c[1] + v[2] * c[2]


def centroid_scaled(cells: list[Hex]) -> tuple[tuple[int, int, int], int]:
    """(중심 × n, n). 칸 수 n으로 곱해 정수로 유지한다."""
    x = y = z = 0
    for c in cells:
        cx, cy, cz = cube(c)
        x, y, z = x + cx, y + cy, z + cz
    return (x, y, z), len(cells)


def direction_toward(cells: list[Hex], target: Hex, prefer: int = 0) -> int:
    """cells의 중심에서 target을 향하는 방향 인덱스. 동률이면 prefer에 가까운 쪽."""
    (sx, sy, sz), n = centroid_scaled(cells)
    tx, ty, tz = cube(target)
    v = (tx * n - sx, ty * n - sy, tz * n - sz)
    best, best_dot, best_turn = 0, None, 99
    for i in range(6):
        d = _dot(v, DIRS[i])
        turn = min((i - prefer) % 6, (prefer - i) % 6)
        if best_dot is None or d > best_dot or (d == best_dot and turn < best_turn):
            best, best_dot, best_turn = i, d, turn
    return best


def sector(cells: list[Hex], facing: int, target: Hex) -> int:
    """cells를 차지하고 facing을 바라보는 적에게 target 칸이 어느 방향 구역인지."""
    i = direction_toward(cells, target, facing)
    return min((i - facing) % 6, (facing - i) % 6)


def rotate(d: int, k: int) -> int:
    return (d + k) % 6


def shape_cells(origin: Hex, facing: int, shape: str) -> list[Hex]:
    """방향 기준 범위 모양. single은 빈 목록 (대상 하나만)."""
    if shape == "single":
        return []
    f = DIRS[facing]
    if shape == "arc3":
        return [add(origin, DIRS[rotate(facing, k)]) for k in (-1, 0, 1)]
    if shape == "ring1":
        return neighbors(origin)
    if shape == "line2":
        return [add(origin, f), add(add(origin, f), f)]
    raise ValueError(shape)


SHAPES: tuple[str, ...] = ("single", "arc3", "ring1", "line2")
