"""사건 기록 → 문장 (템플릿)."""
from __future__ import annotations

from core import log as L
from core.battle import Battle
from core.log import LogEvent


class EventText:
    def __init__(self, b: Battle) -> None:
        data = b.data
        self.tpf = data.rules.ticks_per_frame
        self.who: dict[str, str] = {"": ""}
        for f in b.fighters:
            self.who[f.cid] = f.name
        self.index_name = {str(f.index): f.name for f in b.fighters}
        self.names: dict[str, str] = {}
        for k, a in data.actions.items():
            self.names[k] = a.name
        for e in b.enemies:
            if e.enemy_def:
                for a in e.enemy_def.attacks:
                    self.names[a.id] = a.name
        for k, w in data.weapons.items():
            self.names[k] = w.name
        for k, ar in data.armors.items():
            self.names[k] = ar.name
        for k, it in data.items.items():
            self.names[k] = it.name
        for s in data.rules.stances:
            self.names[s.kind] = s.name

    def frames(self, t: int) -> str:
        return f"{t // self.tpf}" if t % self.tpf == 0 else f"{t / self.tpf:.2f}"

    def n(self, ref: str) -> str:
        return self.names.get(ref, ref)

    def sentence(self, e: LogEvent) -> str:
        who = self.who.get(e.actor, e.actor)
        ev = e.event
        if ev == L.START:
            if e.info == "attack":
                return f"{who}: {self.n(e.ref)} 프리모션 → {self.index_name.get(str(e.value), '?')}"
            if e.info == "stance":
                return f"{who}: {self.n(e.ref)} 태세"
            if e.info == "move":
                return f"{who}: 이동 시작 → {e.ref}"
            if e.info == "item":
                return f"{who}: {self.n(e.ref)} 꺼내는 중"
            if e.info == "swap":
                return f"{who}: 무기 교체 중"
            if e.info == "howl":
                return f"{who}: 하울!"
            return f"{who}: {'태세 유지' if e.ref == 'hold' else '기다림'}"
        if ev == L.HIT:
            target, _, state = e.info.partition(":")
            if state == "chip":
                return f"  {who}의 {self.n(e.ref)}: {self.who.get(target, target)}이(가) 막았지만 {e.value} 피해"
            return f"  {who}의 {self.n(e.ref)} 명중! {self.who.get(target, target)}에게 {e.value} 피해 ({state})"
        if ev == L.MISS_ZONE:
            return f"  {who}의 {self.n(e.ref)}이(가) 빈 자리를 휩쓸었다"
        if ev == L.STANCE_OK:
            chance = "" if e.value < 0 else f" ({e.value / 100:.0f}%)"
            return f"  {who}: {self.n(e.ref)} 성공{chance}"
        if ev == L.STANCE_FAIL:
            return f"  {who}: {self.n(e.ref)} 실패 ({e.value / 100:.0f}%)"
        if ev == L.CLASH:
            res = {"win": "상쇄 승리", "draw": "상쇄 무승부", "lose": "상쇄 패배"}[e.ref]
            return f"  ⚔ {who}: {res} [{e.info}]"
        if ev == L.INTERRUPT:
            return f"  {who}이(가) {self.who.get(e.info, e.info)}의 {self.n(e.ref)} 프리모션을 끊었다!"
        if ev == L.AUTO_OK:
            return f"  {who}: 자동 {self.n(e.ref)}! ({self.n(e.info)}, {e.value / 100:.0f}%)"
        if ev == L.AUTO_FAIL:
            return f"  {who}: 자동 {self.n(e.ref)} 실패 ({e.value / 100:.0f}%)"
        if ev == L.CATCH:
            return f"  {who}이(가) 굳었다 — 딜레이 캐치 {self.frames(e.value)}f ({self.who.get(e.ref, e.ref)}의 {self.n(e.info)})"
        if ev == L.REACT:
            return f"  {who}이(가) {self.n(e.info)}을(를) 알아보고 {self.n(e.ref)}!"
        if ev == L.RETARGET:
            return f"  ◎ {who}이(가) {self.who.get(e.ref, e.ref)} 쪽으로 대상 전환 ({self.frames(e.value)}f: {e.info})"
        if ev == L.TURN:
            return f"  {who}: {self.who.get(e.ref, e.ref)} 쪽으로 방향 전환 ({self.frames(e.value)}f)"
        if ev == L.FRIENDLY_FIRE:
            return f"  ✖ 프렌들리 파이어: {who}의 {self.n(e.ref)}에 {self.who.get(e.info, e.info)}이(가) {e.value} 피해"
        if ev == L.HOWL:
            return f"  {who}의 하울: {self.who.get(e.ref, e.ref)} ({e.info} {e.value})"
        if ev == L.BREAK:
            return f"  ★ {who} 무력화! 쓰러졌다"
        if ev == L.RISE:
            return f"  {who}이(가) 일어섰다 (어그로 초기화 → {self.who.get(e.ref, e.ref)})"
        if ev == L.HEAL:
            return f"  {who} 휴식 회복 +{e.value}{' (포션)' if e.info == 'boost' else ''}"
        if ev == L.ITEM_USED:
            return f"  {who}: {self.n(e.ref)} 사용 (회복 가속 {self.frames(e.value)}f)"
        if ev == L.SWAP:
            return f"  {who}: {self.n(e.ref)}(으)로 교체 (내구도 {e.value})"
        if ev == L.DURABILITY_WARN:
            return f"  ⚠ {who}의 {self.n(e.ref)} 내구도 경고: {e.value}"
        if ev == L.EQUIP_BROKEN:
            return f"  ✖ {who}의 {self.n(e.ref)} 파괴!"
        if ev == L.RELOAD:
            kind = "기억하던 상대" if e.info == "remembered" else "새 패턴"
            return f"  {who}: 재로드 {self.frames(e.value)}f ({kind})"
        if ev == L.FORGET:
            return f"  {who}: {e.ref} 기억을 잃음"
        if ev == L.MOVE_DONE:
            return f"  {who}: {e.ref}(으)로 이동{' 실패 (막힘)' if e.info == 'blocked' else ''}"
        if ev == L.DEATH:
            return f"  {who} 쓰러짐"
        if ev == L.END:
            return {"party": "전투 종료: 파티 승리", "enemies": "전투 종료: 파티 전멸", "timeout": "전투 종료: 시간 초과"}.get(e.ref, e.ref)
        return f"{who} {ev} {e.ref} {e.value} {e.info}"
