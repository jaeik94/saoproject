"""파티 전투 창 UI (tkinter).

    python -m app.gui

- 육각 무대: 초록 칸을 클릭하면 지금 차례인 아군이 그 칸으로 이동한다.
- 행동 버튼은 숫자 키 1~9, 0으로도 고를 수 있다. 버튼에 마우스를 올리면 타임라인의 내 줄에 그 행동의 프레임이 그려진다.
- 적에서 나가는 선은 적의 현재 대상이다 (주황 점선은 대상 전환 중).
"""
from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk

from app.loader import load_setup
from app.text import EventText
from core.battle import Battle
from core.combatant import Combatant
from core.hexgrid import DIRS, Hex, disc
from core.preview import UNKNOWN_END, DecisionRequest, OptionView, Timeline

FONT = ("Malgun Gothic", 10)
FONT_S = ("Malgun Gothic", 8)
FONT_B = ("Malgun Gothic", 10, "bold")
HEX = 23
SQ3 = math.sqrt(3)

TL_LEFT = 170
TL_ROW_H = 26
TL_TOP = 22
TL_MIN_FRAMES, TL_MAX_FRAMES, TL_STEP = 120, 300, 30
UNKNOWN_DRAW_FRAMES = 14
SEG_COLOR = {
    "premotion": "#5cb85c", "active": "#e0443c", "recovery": "#3f6fc4",
    "stance": "#e8b93c", "move": "#9b7fd0", "item": "#b08a5a", "swap": "#b08a5a",
    "wait": "#d8d8d8", "idle": "#eeeeee", "hitstun": "#f0a040", "broken": "#666666",
    "turn": "#8e6bbf", "reload": "#c9a7e6",
}
LEGEND = (("premotion", "발생"), ("active", "판정"), ("recovery", "후딜"), ("stance", "태세"), ("hitstun", "경직"),
          ("move", "이동"), ("turn", "방향 전환"), ("reload", "재로드"), ("wait", "대기"))
BUSY_NAMES = {
    "ready": "대기", "wait": "기다림", "stance": "태세", "stance_hold": "태세 유지", "attack": "공격", "move": "이동",
    "item": "아이템", "swap": "무기 교체", "howl": "하울", "turn": "방향 전환", "retarget": "대상 전환",
    "hitstun": "경직", "broken": "무력화", "dead": "쓰러짐", "premotion": "프리모션", "active": "판정", "recovery": "후딜",
}


def lighten(color: str, keep: float = 0.45) -> str:
    """예측 구간용 옅은 색 (흰색과 섞음)."""
    r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
    return "#%02x%02x%02x" % tuple(int(v * keep + 255 * (1 - keep)) for v in (r, g, b))


def option_detail(o: OptionView, tx: EventText) -> str:
    bits = [f"다음 차례 {tx.frames(o.free_in_t)}f"]
    if o.first_hit_in_t >= 0:
        bits.append(f"첫 타 {tx.frames(o.first_hit_in_t)}f")
    if o.before_enemy_hit is not None:
        bits.append("프리모션 끊기 가능" if o.before_enemy_hit else "끊기 불가")
    if o.clash_timing:
        bits.append("상쇄 타이밍!")
    if o.punish is not None:
        bits.append("반격 성립" if o.punish else "반격 늦음")
    if o.chance_lo >= 0:
        bits.append(f"성공률 {o.chance_lo / 100:.0f}%" if o.chance_lo == o.chance_hi else f"성공률 {o.chance_lo / 100:.0f}~{o.chance_hi / 100:.0f}%")
    if o.stance_in_time is False:
        bits.append("발동 늦음")
    if o.auto_risk_bp >= 0:
        bits.append(f"자동 대응 위험 {o.auto_risk_bp / 100:.0f}%")
    elif o.auto_risk_bp == -2:
        bits.append("자동 대응 위험 있음")
    if o.note:
        bits.append(o.note)
    return " · ".join(bits)


class BattleWindow:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.b: Battle | None = None
        self.tx: EventText | None = None
        self.req: DecisionRequest | None = None
        self.shown = 0
        self.hover: int | None = None
        self.moves: dict[Hex, int] = {}

        root.title("아인크라드 전투 프로토타입 — 파티")
        root.geometry("1420x960")

        top = ttk.Frame(root, padding=6)
        top.pack(fill="x")
        data, _ = load_setup()
        ttk.Label(top, text="적", font=FONT).pack(side="left")
        self.enemy_var = tk.StringVar(value="dire_wolf")
        ttk.Combobox(top, textvariable=self.enemy_var, values=sorted(data.enemies), width=14, state="readonly").pack(side="left", padx=4)
        ttk.Label(top, text="수", font=FONT).pack(side="left")
        self.count_var = tk.StringVar(value="1")
        ttk.Spinbox(top, from_=1, to=len(data.rules.enemy_spawns), textvariable=self.count_var, width=3).pack(side="left", padx=4)
        self.solo_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="솔로(loadout.json)", variable=self.solo_var).pack(side="left", padx=8)
        self.know_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="정보 있음(도감)", variable=self.know_var).pack(side="left", padx=8)
        ttk.Label(top, text="시드", font=FONT).pack(side="left")
        self.seed_var = tk.StringVar(value="1")
        ttk.Entry(top, textvariable=self.seed_var, width=6).pack(side="left", padx=4)
        ttk.Button(top, text="새 전투", command=self.new_battle).pack(side="left", padx=8)
        self.clock = ttk.Label(top, text="", font=FONT_B)
        self.clock.pack(side="right")

        mid = ttk.Frame(root)
        mid.pack(fill="x", padx=6)
        r = data.rules.arena_radius
        self.board = tk.Canvas(mid, width=int(HEX * SQ3 * (2 * r + 1.5)), height=int(HEX * (3 * r + 2.5)), bg="white",
                               highlightthickness=1, highlightbackground="#ccc")
        self.board.pack(side="left")
        self.board.bind("<Button-1>", self.on_board_click)
        right = ttk.Frame(mid)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.status = tk.Label(right, text="", font=FONT, anchor="nw", justify="left")
        self.status.pack(fill="x")
        self.threat = tk.Label(right, text="", font=FONT_B, anchor="w", justify="left", padx=8, pady=4)
        self.threat.pack(fill="x", pady=4)
        self.opt_frame = ttk.Frame(right)
        self.opt_frame.pack(fill="both", expand=True)

        self.timeline = tk.Canvas(root, height=200, bg="white", highlightthickness=1, highlightbackground="#ccc")
        self.timeline.pack(fill="x", padx=6, pady=6)
        self.timeline.bind("<Configure>", lambda _e: self.draw_timeline())

        log_frame = ttk.Frame(root)
        log_frame.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self.log = tk.Text(log_frame, font=FONT, wrap="word", state="disabled", height=8)
        sb = ttk.Scrollbar(log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self.log.tag_configure("enemy", foreground="#b3402a")
        self.log.tag_configure("ally", foreground="#2a5fb3")
        self.log.tag_configure("sys", foreground="#666666")

        root.bind("<Key>", self.on_key)
        self.new_battle()

    # ------------------------------------------------------------ 진행
    def new_battle(self) -> None:
        try:
            seed = int(self.seed_var.get())
            count = max(1, int(self.count_var.get()))
        except ValueError:
            seed, count = 1, 1
        data, party = load_setup(solo=self.solo_var.get())
        self.b = Battle(data, party, data.enemies[self.enemy_var.get()], count, seed, "full" if self.know_var.get() else "none")
        self.tx = EventText(self.b)
        self.shown = 0
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.step()

    def step(self) -> None:
        assert self.b is not None
        self.req = self.b.advance()
        self.hover = None
        self.render()

    def choose(self, i: int) -> None:
        if self.b is None or self.req is None or not (0 <= i < len(self.req.options)):
            return
        self.b.submit(self.req.options[i].choice)
        self.step()

    def visible_options(self) -> list[int]:
        return [i for i, o in enumerate(self.req.options) if o.choice.kind != "move"] if self.req else []

    def on_key(self, ev: tk.Event) -> None:
        if isinstance(ev.widget, (tk.Entry, ttk.Entry, ttk.Combobox, ttk.Spinbox)):
            return
        if ev.char.isdigit():
            n = int(ev.char)
            vis = self.visible_options()
            k = 9 if n == 0 else n - 1
            if k < len(vis):
                self.choose(vis[k])

    def on_board_click(self, ev: tk.Event) -> None:
        cell = self.pixel_to_hex(ev.x, ev.y)
        if cell in self.moves:
            self.choose(self.moves[cell])

    def set_hover(self, i: int | None) -> None:
        self.hover = i
        self.draw_timeline()
        self.draw_board()

    # ------------------------------------------------------------ 좌표
    def hex_to_pixel(self, h: Hex) -> tuple[float, float]:
        w, hgt = int(self.board["width"]), int(self.board["height"])
        return w / 2 + HEX * SQ3 * (h[0] + h[1] / 2), hgt / 2 + HEX * 1.5 * h[1]

    def pixel_to_hex(self, x: float, y: float) -> Hex:
        w, hgt = int(self.board["width"]), int(self.board["height"])
        px, py = x - w / 2, y - hgt / 2
        q = (SQ3 / 3 * px - py / 3) / HEX
        r = (2 / 3 * py) / HEX
        s = -q - r
        rq, rr, rs = round(q), round(r), round(s)
        dq, dr, ds = abs(rq - q), abs(rr - r), abs(rs - s)
        if dq > dr and dq > ds:
            rq = -rr - rs
        elif dr > ds:
            rr = -rq - rs
        return (int(rq), int(rr))

    def hex_poly(self, h: Hex, shrink: float = 0.0) -> list[float]:
        cx, cy = self.hex_to_pixel(h)
        pts: list[float] = []
        for k in range(6):
            a = math.radians(60 * k - 30)
            pts += [cx + (HEX - shrink) * math.cos(a), cy + (HEX - shrink) * math.sin(a)]
        return pts

    # ------------------------------------------------------------ 그리기
    def render(self) -> None:
        b, tx = self.b, self.tx
        assert b is not None and tx is not None
        actor = b.fighters[self.req.actor] if self.req else None
        self.clock.configure(text=f"{tx.frames(b.now)}f ({b.now / b.rules.ticks_per_sec:.1f}초)" +
                                  (f"  ·  차례: {actor.name}" if actor else ""))
        self.moves = {}
        if self.req:
            for i, o in enumerate(self.req.options):
                if o.choice.kind == "move":
                    q, r = o.choice.ref.split(",")
                    self.moves[(int(q), int(r))] = i
        self.draw_board()
        self.status.configure(text="\n".join(self.status_line(c) for c in b.fighters))

        self.log.configure(state="normal")
        for e in b.log[self.shown:]:
            tag = "ally" if e.actor.startswith("ally") else "enemy" if e.actor.startswith("enemy") else "sys"
            self.log.insert("end", f"[{tx.frames(e.tick):>7}f] {tx.sentence(e).strip()}\n", tag)
        self.shown = len(b.log)
        self.log.see("end")
        self.log.configure(state="disabled")

        for w in self.opt_frame.winfo_children():
            w.destroy()
        if self.req is None:
            r = b.result
            assert r is not None
            won = r.winner == "party"
            text = {"party": "승리", "enemies": "전멸", "timeout": "시간 초과"}[r.winner]
            self.threat.configure(text=f"전투 종료 — {text} · {r.end_tick / b.rules.ticks_per_sec:.1f}초  ('새 전투'로 다시)",
                                  bg="#dff0d8" if won else "#f2dede")
            self.draw_timeline()
            return
        self.threat.configure(text=self.threat_line(), bg="#fbe3d6" if self.req.threat else "#eeeeee")
        vis = self.visible_options()
        for n, i in enumerate(vis):
            o = self.req.options[i]
            key = str(n + 1) if n < 9 else ("0" if n == 9 else "")
            row = ttk.Frame(self.opt_frame)
            row.pack(fill="x", pady=1)
            btn = ttk.Button(row, text=f"{key + '. ' if key else ''}{o.label}", width=24, command=lambda i=i: self.choose(i))
            btn.pack(side="left")
            btn.bind("<Enter>", lambda _e, i=i: self.set_hover(i))
            btn.bind("<Leave>", lambda _e: self.set_hover(None))
            ttk.Label(row, text=option_detail(o, tx), font=FONT, wraplength=520).pack(side="left", padx=6)
        if self.moves:
            ttk.Label(self.opt_frame, text="이동: 무대의 초록 칸을 클릭", font=FONT, foreground="#2a7a2a").pack(anchor="w", pady=4)
        self.draw_timeline()

    def status_line(self, c: Combatant) -> str:
        b = self.b
        assert b is not None
        phase = c.attack_phase(b.now)
        state = BUSY_NAMES.get(phase or c.busy, c.busy)
        parts = [f"{'▶ ' if self.req and self.req.actor == c.index else '   '}{c.name}", f"HP {c.hp}/{c.max_hp}", state]
        if c.stance:
            parts.append(f"태세:{b.rules.stance(c.stance.kind).name}")
        if c.is_ally and c.alive:
            parts.append(f"자동 {b.rules.stance(b.auto_kind(c)).name}")
            w = c.weapon
            parts.append(f"{w.weapon.name} {w.durability}/{w.weapon.durability}" if w else "무기 없음")
            if b.is_resting(c):
                parts.append("휴식 중" + (" (포션)" if b.now < c.boost_until else ""))
            cds = [f"{s.name} {self.tx.frames(c.cooldowns[s.id] - b.now)}f" for s in c.sword_skills if c.cooldowns.get(s.id, 0) > b.now]
            if cds:
                parts.append("쿨링 " + ", ".join(cds))
        elif c.alive and c.enemy_def:
            t = b.fighters[c.target] if c.target >= 0 else None
            parts.append(f"대상 → {t.name if t else '-'}")
            if b.rules.size(c.size).breakable:
                parts.append(f"무력화 {c.break_gauge}/{c.enemy_def.break_max}")
        return " | ".join(parts)

    def threat_line(self) -> str:
        b, tx, req = self.b, self.tx, self.req
        assert b is not None and tx is not None and req is not None
        t = req.threat
        if t is None:
            return "나에게 다가오는 공격 없음"
        who = b.fighters[t.attacker].name
        target = "나" if t.targeting == "person" else "내가 선 칸 (장소 지정)"
        s = f"⚠ {who}의 공격: 약 {tx.frames(t.approx_hit_in_t)}f 후, 대상 {target}"
        if t.known:
            attrs = ", ".join(b.rules.attribute(a).name for a in t.attributes) or "속성 없음"
            s += f"\n   [정보] {t.name} ({attrs}), 남은 {t.hits_left}타, 정확히 {tx.frames(t.hit_in_t)}f 후, 후딜 종료 {tx.frames(t.enemy_free_in_t)}f 후"
        if len(req.threats) > 1:
            s += f"\n   외 {len(req.threats) - 1}개의 공격이 더 오고 있음"
        return s

    def draw_board(self) -> None:
        c = self.board
        c.delete("all")
        b = self.b
        if b is None:
            return
        req = self.req
        hovered = req.options[self.hover] if req and self.hover is not None else None
        danger: set[Hex] = set()
        for e in b.enemies:
            act = e.action
            if e.alive and e.busy == "attack" and act is not None and act.place_cells and e.pending_hits(b.now):
                danger.update(act.place_cells)
        for h in disc((0, 0), b.rules.arena_radius):
            fill = "#f6f6f6"
            if h in self.moves:
                fill = "#d6f0d0"
            if h in danger:
                fill = "#f7c9b6"
            c.create_polygon(*self.hex_poly(h), fill=fill, outline="#c8c8c8")
        # 적
        for e in b.enemies:
            if not e.alive:
                continue
            hl = hovered is not None and hovered.choice.kind in ("skill", "basic", "throw") and hovered.choice.arg == e.index
            for cell in e.cells():
                c.create_polygon(*self.hex_poly(cell, 2), fill="#e8a79a" if not hl else "#ff6a4d", outline="#b3402a", width=2 if hl else 1)
            xs = [self.hex_to_pixel(cell) for cell in e.cells()]
            cx, cy = sum(p[0] for p in xs) / len(xs), sum(p[1] for p in xs) / len(xs)
            dx, dy = self.hex_to_pixel(DIRS[e.facing])
            ox, oy = self.hex_to_pixel((0, 0))
            vx, vy = dx - ox, dy - oy
            ln = math.hypot(vx, vy)
            reach = HEX * (1.0 if len(e.footprint) == 1 else 1.9)
            c.create_line(cx, cy, cx + vx / ln * reach, cy + vy / ln * reach, fill="#7a1f10", width=3, arrow="last")
            c.create_text(cx, cy - 6, text=e.name.replace("다이어 ", ""), font=FONT_S, fill="#5a1508")
            if e.target >= 0 and b.fighters[e.target].alive:
                tx_, ty_ = self.hex_to_pixel(b.fighters[e.target].cell)
                retarget = e.busy == "retarget"
                c.create_line(cx, cy, tx_, ty_, fill="#e08a00" if retarget else "#c0392b", width=2, dash=(6, 4) if retarget else (), arrow="last")
        # 아군
        for a in b.allies:
            if not a.alive:
                continue
            x, y = self.hex_to_pixel(a.cell)
            acting = req is not None and req.actor == a.index
            fx, fy = self.hex_to_pixel(DIRS[b.facing_of(a)])
            ox, oy = self.hex_to_pixel((0, 0))
            ln = math.hypot(fx - ox, fy - oy)
            c.create_line(x, y, x + (fx - ox) / ln * 22, y + (fy - oy) / ln * 22, fill="#1d4f99", width=3, arrow="last")
            c.create_oval(x - 14, y - 14, x + 14, y + 14, fill="#3a7bd5", outline="#f2c200" if acting else "white", width=4 if acting else 2)
            c.create_text(x, y, text=a.name[:1] + a.name[-1:], fill="white", font=("Malgun Gothic", 8, "bold"))
            if b.is_resting(a):
                c.create_text(x + 14, y - 14, text="휴", fill="#2a7a2a", font=FONT_S)
        if hovered is not None and hovered.choice.kind == "move":
            q, r = hovered.choice.ref.split(",")
            c.create_polygon(*self.hex_poly((int(q), int(r)), 3), fill="", outline="#2a7a2a", width=3)

    def draw_timeline(self) -> None:
        """지금부터 앞으로의 프레임. 적 줄은 정보가 있으면 반복 패턴까지 이어 그린다."""
        c = self.timeline
        c.delete("all")
        b, req, tx = self.b, self.req, self.tx
        if b is None or tx is None or req is None:
            return
        tpf = b.rules.ticks_per_frame
        plan = req.options[self.hover] if self.hover is not None and self.hover < len(req.options) else None
        rows = list(req.rows)
        height = TL_TOP + TL_ROW_H * len(rows) + 30
        if int(c["height"]) != height:
            c.configure(height=height)
        frames = TL_MIN_FRAMES
        for row in rows:
            line = plan.line if plan and row.index == req.actor else row.line
            for sg in line.segments:
                end = sg.end_t if sg.end_t != UNKNOWN_END else sg.start_t + UNKNOWN_DRAW_FRAMES * tpf
                if not sg.planned:
                    frames = max(frames, end // tpf + 6)
        frames = min(TL_MAX_FRAMES, -(-frames // TL_STEP) * TL_STEP)
        width = max(200, c.winfo_width() - TL_LEFT - 12)
        px = width / (frames * tpf)

        def x(t: int) -> float:
            return TL_LEFT + min(max(t, 0), frames * tpf) * px

        bottom = TL_TOP + TL_ROW_H * len(rows)
        for f in range(0, frames + 1, 10):
            xx = x(f * tpf)
            c.create_line(xx, TL_TOP - 4, xx, bottom, fill="#eeeeee" if f % 30 else "#dddddd")
            c.create_text(xx, 10, text=f"+{f}" if f else "지금", fill="#888", font=FONT_S)

        # 내 줄 위치 (상쇄 창을 그리기 위해)
        my_y = None
        for i, row in enumerate(rows):
            if row.index == req.actor:
                my_y = TL_TOP + TL_ROW_H * i
        for i, row in enumerate(rows):
            y0 = TL_TOP + TL_ROW_H * i + 4
            y1 = y0 + TL_ROW_H - 8
            me = row.index == req.actor
            line: Timeline = plan.line if plan and me else row.line
            name = row.name + (" (휴식)" if row.resting else "")
            if not row.is_ally:
                t = b.fighters[row.target].name if row.target >= 0 else "-"
                name += f" → {t}" + (f" 전환 {tx.frames(row.retarget_left_t)}f" if row.retarget_left_t else "")
                if my_y is not None and line.hits_exact and row.target == req.actor:
                    w = b.rules.clash_window_t
                    for h in line.hits_t:
                        c.create_rectangle(x(h - w), my_y + 1, x(h + w), my_y + TL_ROW_H - 1, fill="#fde2e0", outline="")
            c.create_text(8, (y0 + y1) / 2, text=("▶ " if me else "") + name, anchor="w",
                          font=FONT_B if me else FONT, fill="#2a5fb3" if row.is_ally else "#b3402a")
            for sg in line.segments:
                end = sg.end_t if sg.end_t != UNKNOWN_END else sg.start_t + UNKNOWN_DRAW_FRAMES * tpf
                if sg.start_t >= frames * tpf:
                    continue
                color = SEG_COLOR.get(sg.kind, "#cccccc")
                if sg.planned:
                    c.create_rectangle(x(sg.start_t), y0, x(end), y1, fill=lighten(color), outline="white")
                elif sg.exact and sg.end_t != UNKNOWN_END:
                    c.create_rectangle(x(sg.start_t), y0, x(end), y1, fill=color, outline="white")
                else:
                    c.create_rectangle(x(sg.start_t), y0, x(end), y1, fill=color, outline="", stipple="gray50")
                    if sg.end_t == UNKNOWN_END:
                        c.create_text(x(end) + 8, (y0 + y1) / 2, text="?", font=FONT_B, fill="#888")
                if sg.label and x(end) - x(sg.start_t) > 30:
                    c.create_text(x(sg.start_t) + 3, (y0 + y1) / 2, text=sg.label, anchor="w", font=FONT_S,
                                  fill="white" if not sg.planned else "#333")
            for h in line.hits_t:
                c.create_polygon(x(h) - 5, y0 - 5, x(h) + 5, y0 - 5, x(h), y0 + 1, fill="#e0443c", outline="")
                if not row.is_ally and row.target == req.actor and my_y is not None:
                    c.create_line(x(h), my_y + 2, x(h), y1, fill="#e0443c", width=1, dash=() if line.hits_exact else (3, 3))
            if line.free_t != UNKNOWN_END:
                c.create_line(x(line.free_t), y0 - 2, x(line.free_t), y1 + 2, fill="black", width=2)

        y = bottom + 14
        if plan is None:
            c.create_text(TL_LEFT, y, text="행동 버튼에 마우스를 올리면 그 행동의 프레임이 내 줄(▶)에 표시됩니다. 흐린 칸은 앞으로의 패턴 예측",
                          anchor="w", font=FONT_S, fill="#888")
        lx = c.winfo_width() - 12
        for kind, name in reversed(LEGEND):
            item = c.create_text(lx, y, text=name, anchor="e", font=FONT_S, fill="#555")
            lx = c.bbox(item)[0] - 4
            c.create_rectangle(lx - 10, y - 5, lx, y + 5, fill=SEG_COLOR[kind], outline="")
            lx -= 18


def main() -> None:
    root = tk.Tk()
    BattleWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
