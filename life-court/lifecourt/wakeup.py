"""누가 날 깨웠나 재판: sound-watch 기록(읽기 전용)에서 밤 시간대의 주범 소리를 골라 판결문으로 만든다.

피고는 항상 '소리'다. 사람·이웃·호수는 피고가 될 수 없고, 정체를 알 수 없으면 '정체불명의 소음'이다.
"""
from __future__ import annotations

import math
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime, timedelta
from pathlib import Path

from .courts import CourtError, _case_no, _seed, parse_time
from .engine import Judgment, Picker

UNKNOWN = "정체불명의 소음"
MATCH_SLACK = 5.0  # 인식 기록이 소음 이벤트 앞뒤 몇 초 안이면 같은 소리로 본다

# 사람·장소를 가리키는 이름이 소리 이름에 섞여 있으면 피고로 쓰지 않는다.
PEOPLE_WORDS = ("옆집", "윗집", "아랫집", "앞집", "뒷집", "이웃", "주민", "아저씨", "아줌마", "아주머니", "할머니",
                "할아버지", "아이", "애기", "아기", "사람", "누구", "님", "씨", "층", "호실", "동생", "형", "누나",
                "오빠", "언니", "엄마", "아빠", "남편", "아내", "친구", "룸메", "집주인", "세입자", "neighbor")

KIND_LABEL = {"knock": "노크 소리", "beep": "삐 소리", "melody": "멜로디"}

PRESCRIPTIONS = [
    (("세탁", "건조", "빨래"), "세탁·건조 예약을 23시 전에 끝나도록 당기세요(종료음 끄기도 가능)."),
    (("초인종", "벨", "차임"), "밤에는 초인종 알림을 방해 금지로 돌리고, 택배는 '문 앞 비대면'으로 요청하세요."),
    (("노크", "knock"), "택배·배달 요청사항에 '노크 금지, 문 앞에 두기'를 적어 두세요."),
    (("전자레인지", "밥솥", "정수기", "냉장고", "에어컨", "보일러"), "가전 알림음을 줄이거나 야간 예약을 끄세요."),
    (("삐", "알람", "경보", "멜로디"), "기기 알림음 볼륨과 야간 모드를 확인하세요."),
]
GENERIC_RX = "귀마개와 백색소음을 처방합니다. 같은 시각에 또 울리면 기록과 클립만 모아 두세요."


@dataclass
class Candidate:
    label: str            # 피고 이름(소리)
    start: float
    end: float
    peak: float | None    # 상대 dBFS (없으면 인식 기록만)
    leq: float | None
    recognized: bool
    generic: bool         # 정체불명 여부
    note: str = ""        # 이름을 바꿔치기한 사유

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def rank(self) -> float:
        base = self.peak if self.peak is not None else -90.0
        return base + 6.0 * math.log10(1.0 + self.duration) + (1.5 if self.recognized else 0.0)


def safe_name(name: str) -> tuple[str, bool]:
    """소리 이름에서 사람을 가리키는 말이 나오면 정체불명으로 바꾼다. (이름, 바뀌었는지)"""
    n = (name or "").strip()
    if not n or any(w in n for w in PEOPLE_WORDS):
        return UNKNOWN, bool(n)
    return n, False


# -- 시간 창 ------------------------------------------------------------------
def night_window(d: Date, t_from: str, t_to: str) -> tuple[datetime, datetime]:
    fh, fm = parse_time(t_from)
    th, tm = parse_time(t_to)
    end = datetime(d.year, d.month, d.day, th, tm)
    start = datetime(d.year, d.month, d.day, fh, fm)
    if end <= start:  # 자정을 넘는 창: 전날 from ~ 당일 to
        start -= timedelta(days=1)
    return start, end


def resolve_woke(woke: str, start: datetime, end: datetime) -> datetime:
    h, m = parse_time(woke)
    day = start.date()
    while day <= end.date():
        t = datetime(day.year, day.month, day.day, h, m)
        if start <= t <= end:
            return t
        day += timedelta(days=1)
    raise CourtError("--woke %s 가 재판 시간대(%s ~ %s) 밖입니다." % (woke, start.strftime("%H:%M"),
                                                                  end.strftime("%H:%M")))


# -- sound-watch 읽기 전용 -------------------------------------------------------
def default_db(env) -> Path:
    """SOUNDWATCH_HOME > (옛 ./data/soundwatch.db 가 있으면 그것) > ~/.soundwatch (sound-watch 기본과 동일)"""
    home = env.get("SOUNDWATCH_HOME")
    if home:
        return Path(home).expanduser() / "soundwatch.db"
    legacy = Path("data") / "soundwatch.db"
    if legacy.is_file():
        print("안내: 현재 폴더의 옛 data/soundwatch.db 를 사용합니다. sound-watch 기본 위치는 ~/.soundwatch 입니다.",
              file=sys.stderr)
        return legacy
    return Path.home() / ".soundwatch" / "soundwatch.db"


def read_night(db: Path, a: float, b: float) -> tuple[list[dict], list[dict]]:
    """sound-watch sqlite를 읽기 전용(mode=ro)으로 열어 events/detections만 읽는다."""
    p = Path(db).expanduser()
    if not p.is_file():
        raise CourtError("sound-watch 기록(DB)을 찾을 수 없습니다: %s (--db 로 지정하세요)" % p)
    conn = None
    try:
        conn = sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        ev = [dict(r) for r in conn.execute("SELECT * FROM events WHERE start>=? AND start<? ORDER BY start", (a, b))]
        de = [dict(r) for r in conn.execute("SELECT * FROM detections WHERE ts>=? AND ts<? ORDER BY ts", (a, b))]
    except sqlite3.Error as e:
        raise CourtError("sound-watch DB를 읽을 수 없습니다(%s): %s" % (e, p)) from e
    finally:
        if conn is not None:
            conn.close()
    return ev, de


# -- 후보 만들기 / 주범 고르기 -------------------------------------------------------
def build_candidates(events: list[dict], dets: list[dict]) -> list[Candidate]:
    used: set[int] = set()
    out: list[Candidate] = []
    for e in events:
        s, en = float(e["start"]), float(e["end"])
        hit = None
        for i, d in enumerate(dets):
            if s - MATCH_SLACK <= float(d["ts"]) <= en + MATCH_SLACK:
                hit = hit if hit is not None else i
                used.add(i)
        if hit is not None:
            label, changed = _det_label(dets[hit])
            out.append(Candidate(label, s, en, float(e["peak_dbfs"]), float(e["leq_dbfs"]), True,
                                 label == UNKNOWN, "이름에 사람을 가리키는 말이 있어 소리 이름을 지웠습니다." if changed else ""))
        else:
            out.append(Candidate(UNKNOWN, s, en, float(e["peak_dbfs"]), float(e["leq_dbfs"]), False, True))
    for i, d in enumerate(dets):
        if i in used:
            continue
        label, changed = _det_label(d)
        t = float(d["ts"])
        out.append(Candidate(label, t, t, None, None, True, label == UNKNOWN,
                             "이름에 사람을 가리키는 말이 있어 소리 이름을 지웠습니다." if changed else ""))
    out.sort(key=lambda c: c.start)
    return out


def _det_label(d: dict) -> tuple[str, bool]:
    kind = str(d.get("kind") or "")
    name = str(d.get("name") or "")
    if kind == "knock":
        return KIND_LABEL["knock"], False
    if kind in ("beep", "melody") and not name.strip():
        return KIND_LABEL[kind], False
    return safe_name(name or KIND_LABEL.get(kind, ""))


def pick_culprit(cands: list[Candidate], woke_ts: float | None) -> Candidate:
    if woke_ts is None:
        return max(cands, key=lambda c: (c.rank, -c.start))
    # 깬 시각 이전에 시작한 소리를 우선, 거리가 가장 가까운 것. 같으면 더 센 것.
    return min(cands, key=lambda c: (abs(c.start - woke_ts), -c.rank, c.start))


def prescription(c: Candidate) -> str:
    if not c.generic:
        for keys, text in PRESCRIPTIONS:
            if any(k in c.label for k in keys):
                return text
    return GENERIC_RX


def _hm(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def _lvl(c: Candidate) -> str:
    if c.peak is None:
        return "소리 크기는 기록되지 않음(인식 기록만 있음)"
    return "최대 %.1f dBFS, 평균 %.1f dBFS (상대값)" % (c.peak, c.leq if c.leq is not None else c.peak)


# -- 판결 ---------------------------------------------------------------------
def judge_wakeup(events: list[dict], dets: list[dict], d: Date, start: datetime, end: datetime, *,
                 woke: datetime | None = None, seed: str | None = None) -> Judgment:
    sd = _seed(seed, "wakeup", start.isoformat(), end.isoformat(), woke.isoformat() if woke else "")
    pk = Picker(sd)
    win = "%s ~ %s" % (start.strftime("%m-%d %H:%M"), end.strftime("%m-%d %H:%M"))
    cands = build_candidates(events, dets)
    case = _case_no(sd, "소", d)
    if not cands:
        ruling = pk.pick("empty", [
            "이 밤의 소리 기록은 없습니다. 피고 불출석으로 공소 기각, 잠 못 잔 이유는 소리 밖에서 찾으세요.",
            "증거 불충분으로 무혐의. 이 밤 sound-watch는 조용했고, 깬 이유는 법정 밖의 문제입니다.",
        ])
        return Judgment(
            "wakeup", "누가 날 깨웠나 재판", case, d.isoformat(),
            parties=[("재판 시간대", win), ("피고", "없음 (기록된 소리 없음)")],
            sections=[("증거", ["- 해당 시간대 소음 이벤트와 인식 기록이 0건입니다."]),
                      ("주문", [ruling]),
                      ("처방", ["기록이 없다고 안 깬 건 아닙니다. 마이크가 켜져 있었는지(sound-watch 실행 여부)부터 확인하세요."])],
            ruling=ruling)
    c = pick_culprit(cands, woke.timestamp() if woke else None)
    same = [x for x in cands if x.label == c.label and not x.generic] if not c.generic else \
        [x for x in cands if x.generic]
    defendant = "피고 " + c.label if not c.generic else "피고 " + UNKNOWN
    basis = "깬 시각 %s에 가장 가까운 소리" % woke.strftime("%H:%M") if woke else "가장 크고 길었던 소리"
    ev = ["- 주범 후보 선정 기준: %s" % basis,
          "- 발생 시각: %s%s" % (_hm(c.start), "" if c.end <= c.start else " ~ " + _hm(c.end)) +
          (" (%.0f초)" % c.duration if c.duration >= 1 else ""),
          "- 크기: " + _lvl(c),
          "- 같은 밤 같은 종류의 기록: 총 %d건 (이 밤 전체 소리 %d건)" % (len(same), len(cands)),
          "- dBFS는 보정하지 않은 상대값입니다. 실제 소음계 dB과 같지 않고, 소리끼리 비교용으로만 보세요."]
    if woke:
        ev.append("- 깬 시각 신고: %s (본인 진술)" % woke.strftime("%H:%M"))
    others = [x for x in cands if x is not c][:3]
    if others:
        ev.append("- 참고인 소리: " + ", ".join("%s %s" % (_hm(x.start), x.label) for x in others))
    if c.note:
        ev.append("- " + c.note)
    verdict_pool = [
        "%s에게 수면 방해 유죄를 선고한다." % defendant,
        "%s, 새벽의 평화를 깬 죄로 유죄." % defendant,
        "본 재판부는 %s의 소행임이 상당히 의심된다고 본다(상대값 기준)." % defendant,
    ]
    verdict = pk.pick("verdict", verdict_pool)
    sentence = pk.pick("sentence", [
        "형량: 다음 주 같은 시간대 재출석 명령.", "형량: 귀마개 3일 집행유예.",
        "형량: 베개 위 분노 0회, 숙면 8시간 권고.", "형량: 이불 속 한숨 1회."])
    sections = [("증거", ev), ("판결", [verdict, sentence]), ("처방", [prescription(c)])]
    if c.generic:
        sections.append(("유의", ["정체불명의 소음은 누구의 소행인지 알 수 없다는 뜻입니다. "
                                  "이 판결문만 근거로 누구에게도 따지거나 찾아가지 마세요."]))
    ruling = verdict + " " + prescription(c)
    return Judgment("wakeup", "누가 날 깨웠나 재판", case, d.isoformat(),
                    parties=[("재판 시간대", win), ("피고", c.label if not c.generic else UNKNOWN),
                             ("사건", "수면 방해 (원고: 본인의 잠)")],
                    sections=sections, ruling=ruling)
