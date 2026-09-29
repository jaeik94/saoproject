"""배치 로그 요약. 코어를 import하지 않고 CSV만 읽는다.

    python -m analysis.summarize logs/<run>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

FPS = 60


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="배치 로그 요약")
    ap.add_argument("run", type=Path)
    args = ap.parse_args()

    battles = pd.read_csv(args.run / "battles.csv")
    ev = pd.read_csv(args.run / "events.csv", keep_default_na=False)
    n = len(battles)
    pd.set_option("display.width", 160)
    ally = ev["actor"].str.startswith("ally")
    enemy = ev["actor"].str.startswith("enemy")

    print(f"■ {args.run.name} — {n}판")
    wins = battles["winner"].value_counts()
    print("  결과: " + ", ".join(f"{k} {v} ({v / n:.1%})" for k, v in wins.items()))
    sec = battles["end_frame"] / FPS
    print(f"  전투 시간(초): 평균 {sec.mean():.1f}, 중앙 {sec.median():.1f}, 최소 {sec.min():.1f}, 최대 {sec.max():.1f}")
    print(f"  남은 아군 체력 비율: 평균 {(battles['ally_hp'] / battles['ally_max_hp']).mean():.1%}, "
          f"생존 인원 평균 {battles['allies_alive'].mean():.2f}/{battles['allies'].iloc[0]}")

    def per_battle(mask: pd.Series) -> float:
        return mask.sum() / n

    print("\n■ 행동 사용 (판당)")
    starts = ev[ev["event"] == "start"]
    for label, mask in (("아군", ally), ("적", enemy)):
        s = starts[mask.loc[starts.index]]
        counts = (s["info"] + ":" + s["ref"]).value_counts() / n
        print(f"  {label}: " + ", ".join(f"{k} {v:.1f}" for k, v in counts.head(14).items()))

    print("\n■ 피해 (판당 합계, 명중 수)")
    hits = ev[ev["event"] == "hit"]
    side = hits["actor"].str.replace(r"\d+$", "", regex=True)
    dmg = hits.groupby([side, hits["ref"]])["value"].agg(["sum", "count"])
    dmg["sum"] = dmg["sum"] / n
    dmg["count"] = dmg["count"] / n
    print(dmg.round(1).to_string())

    print("\n■ 태세")
    st = ev[ev["event"].isin(["stance_ok", "stance_fail"])]
    if len(st):
        st_side = st["actor"].str.replace(r"\d+$", "", regex=True)
        tab = st.groupby([st_side, st["ref"], st["event"]]).size().unstack(fill_value=0)
        for col in ("stance_ok", "stance_fail"):
            if col not in tab:
                tab[col] = 0
        tab["성공률"] = (tab["stance_ok"] / (tab["stance_ok"] + tab["stance_fail"])).map("{:.1%}".format)
        tab["판당 시도"] = ((tab["stance_ok"] + tab["stance_fail"]) / n).round(2)
        print(tab.to_string())

    print("\n■ 공방·헤이트 사건 (판당)")
    rows = {
        "패리 (막은 타)": per_battle(ev["event"] == "parry"),
        "패리 위력 비교": per_battle(ev["event"] == "clash"),
        "넘어짐": per_battle(ev["event"] == "knockdown"),
        "밀려남": per_battle(ev["event"] == "knockback"),
        "아군이 프리모션 끊음": per_battle((ev["event"] == "interrupt") & ally),
        "적이 프리모션 끊음": per_battle((ev["event"] == "interrupt") & enemy),
        "적의 즉시 반응(태세)": per_battle(ev["event"] == "react"),
        "자동 대응 성공(아군)": per_battle((ev["event"] == "auto_ok") & ally),
        "자동 대응 성공(적)": per_battle((ev["event"] == "auto_ok") & enemy),
        "딜레이 캐치 발생": per_battle(ev["event"] == "catch"),
        "적 대상 전환": per_battle(ev["event"] == "retarget"),
        "적 방향 전환": per_battle(ev["event"] == "turn"),
        "재로드(무기 교체 등)": per_battle(ev["event"] == "reload"),
        "프렌들리 파이어": per_battle(ev["event"] == "friendly_fire"),
        "하울": per_battle(ev["event"] == "howl"),
        "무력화": per_battle(ev["event"] == "break"),
        "장소 공격 빗나감": per_battle(ev["event"] == "miss_zone"),
        "휴식 회복 횟수": per_battle(ev["event"] == "heal"),
        "포션 사용": per_battle(ev["event"] == "item_used"),
        "장비 파괴": per_battle(ev["event"] == "equip_broken"),
    }
    for k, v in rows.items():
        print(f"  {k:<16} {v:.2f}")


if __name__ == "__main__":
    main()
