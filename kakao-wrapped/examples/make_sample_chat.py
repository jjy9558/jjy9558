#!/usr/bin/env python3
"""Generate a FAKE KakaoTalk (Android style) group chat for demos and tests.

Everything here is invented: names, sentences, links, phone number.
Usage:  python3 examples/make_sample_chat.py [out.txt] [--seed 7]
"""

import random
import sys
from datetime import datetime, timedelta

MEMBERS = {
    # name: (weight, ㅋ rate, ㅠ rate, photo, emoticon, link, night-owl, reply seconds, long-text)
    "철수": dict(w=1.2, kk=0.6, cry=0.05, photo=0.06, emo=0.04, link=0.03, night=0.05, lat=45, long=0.05),
    "영희": dict(w=1.5, kk=0.3, cry=0.20, photo=0.10, emo=0.12, link=0.04, night=0.05, lat=20, long=0.10),
    "민수": dict(w=0.8, kk=0.8, cry=0.02, photo=0.03, emo=0.03, link=0.12, night=0.10, lat=420, long=0.02),
    "지은": dict(w=1.0, kk=0.4, cry=0.10, photo=0.15, emo=0.05, link=0.05, night=0.04, lat=75, long=0.20),
    "현우": dict(w=1.1, kk=0.9, cry=0.08, photo=0.05, emo=0.08, link=0.06, night=0.55, lat=130, long=0.04),
}

OPENERS = [
    "오늘 점심 뭐 먹어?", "다들 뭐해?", "주말에 시간 되는 사람?", "야 이거 봤어?", "퇴근하고 한잔 콜?",
    "아 월요일 너무 싫다", "오늘 날씨 미쳤다", "새로 생긴 카페 가봤어?", "저녁 치킨 먹을까", "생일 선물 뭐 사지",
]
LINES = [
    "나는 마라탕 먹고 싶다", "치킨 좋지 맥주도 같이", "이번 주말에 영화 보러 가자", "그 영화 후기 완전 별로래",
    "카페에서 공부하는 중인데 집중 안돼", "여행 계획 다시 짜야겠다", "제주도 가고 싶다 진짜", "회사 회식 또 있대",
    "다이어트는 내일부터 시작할게", "넷플릭스 새 드라마 추천해줘", "커피 마시고 싶다", "오늘 야근 확정이다",
    "택배 왔는데 열어보기 무섭다", "이번 달 월급 어디갔지", "운동 갈 사람 있어?", "맛집 리스트 공유할게",
    "고양이 사진 보고 힐링하는 중", "비 오는 날엔 파전에 막걸리지", "노래 추천 받습니다", "새해 계획 세웠어?",
    "벌써 연말이라니 믿기지가 않아", "송년회 날짜 정하자", "크리스마스에 뭐해", "스키장 갈 사람 모집합니다",
    "여름휴가 어디로 갈까", "캠핑 장비 새로 샀어", "배고파 죽겠다", "퇴근 시간이 왜 이렇게 안 오지",
]
REACT = ["ㅋㅋㅋㅋ", "ㅋㅋ", "ㅎㅎ", "오 좋다", "인정", "맞아 맞아", "ㅠㅠ", "헐", "대박", "진짜?", "ㅇㅇ", "좋아 콜",
         "나도 나도", "그건 아니지 ㅋㅋㅋ", "오케이", "ㅋㅋㅋㅋㅋㅋㅋㅋㅋ"]
LINKS = ["https://www.youtube.com/watch?v=abc123", "https://youtu.be/xyz789", "https://map.naver.com/p/entry/place/1234",
         "https://www.instagram.com/p/AbCdEf/", "https://namu.wiki/w/%EC%98%88%EC%8B%9C", "https://blog.naver.com/someone/123"]
LONG = ["아니 근데 어제 있었던 일 말해줄까 진짜 웃긴데 퇴근길에 지하철 타고 가는데 옆자리 아저씨가 갑자기 노래를 부르기 시작하는 거야 그래서 다들 쳐다봤는데 끝까지 부르더라",
        "이번 주말 계획 정리해볼게 토요일 점심에 만나서 영화 보고 저녁은 고기 먹고 일요일은 각자 쉬는 걸로 하자 다른 의견 있으면 말해줘"]
MULTILINE = ["장보기 목록\n우유\n계란\n두부", "공지\n이번 모임 장소는 강남역\n시간은 7시", "가사 한 줄\n두 줄\n세 줄"]


def pick_speaker(rng, prev):
    names = list(MEMBERS)
    weights = [MEMBERS[n]["w"] * (0.35 if n == prev else 1.0) for n in names]
    return rng.choices(names, weights)[0]


def make_text(rng, name, opener=False):
    p = MEMBERS[name]
    r = rng.random()
    if opener:
        t = rng.choice(OPENERS)
    elif r < p["long"]:
        t = rng.choice(LONG)
    elif r < p["long"] + 0.01:
        t = rng.choice(MULTILINE)
    elif r < p["long"] + 0.01 + 0.45:
        t = rng.choice(LINES)
    else:
        t = rng.choice(REACT)
    if "\n" not in t and rng.random() < p["kk"]:
        t += "ㅋ" * rng.choice([2, 3, 3, 4, 5, 6, 9] if name != "현우" else [3, 5, 8, 12, 20, 25])
    if "\n" not in t and rng.random() < p["cry"]:
        t += "ㅠ" * rng.choice([2, 3])
    return t


def render(rng, start, end):
    """Return Android-style export text."""
    out = ["공유방 카카오톡 대화", "저장한 날짜 : %d년 %d월 %d일 오전 9:00" % (end.year, end.month, end.day), ""]
    ampm = lambda dt: "%s %d:%02d" % ("오전" if dt.hour < 12 else "오후", (dt.hour % 12) or 12, dt.minute)
    wk = "월화수목금토일"
    day = start
    lurk_until = datetime(2026, 5, 20)
    while day <= end:
        events = []  # (ts, name, text)
        episodes = rng.choice([0, 1, 1, 2, 2, 3, 4]) if day.weekday() < 5 else rng.choice([1, 2, 3, 4, 5])
        if (day.month, day.day) == (3, 15):
            episodes = 6  # 불꽃 대화의 날
        for _ in range(episodes):
            hr = rng.choices(
                range(24), [1, 3, 3, 2, 1, 0, 0, 1, 2, 3, 3, 4, 6, 5, 3, 3, 3, 3, 5, 5, 6, 8, 8, 5]
            )[0]
            t = datetime(day.year, day.month, day.day, hr, rng.randrange(60), rng.randrange(60))
            n = rng.choice([4, 6, 9, 14, 22, 35]) * (4 if (day.month, day.day) == (3, 15) else 1)
            prev = None
            for i in range(n):
                nm = pick_speaker(rng, prev)
                if i:  # reply latency depends on who answers
                    gap = rng.expovariate(1.0 / MEMBERS[nm]["lat"])
                    t = t + timedelta(seconds=int(min(gap, 1500)))
                if nm == "민수" and datetime(2026, 4, 25) <= t <= lurk_until:
                    nm = pick_speaker(rng, "민수")
                    if nm == "민수":
                        nm = "영희"
                if i == 0 and 1 <= hr <= 3:
                    nm = rng.choices(list(MEMBERS), [1, 1, 1, 1, 6])[0]
                p = MEMBERS[nm]
                r = rng.random()
                if r < p["photo"]:
                    k = rng.choice([1, 1, 1, 2, 3, 5])
                    text = "사진" if k == 1 else "사진 %d장" % k
                elif r < p["photo"] + p["emo"]:
                    text = "이모티콘"
                elif r < p["photo"] + p["emo"] + p["link"]:
                    text = rng.choice(LINKS)
                elif r < p["photo"] + p["emo"] + p["link"] + 0.004:
                    text = "삭제된 메시지입니다."
                elif r < p["photo"] + p["emo"] + p["link"] + 0.006:
                    text = "연락처는 010-1234-5678 이고 메일은 sample@example.com 이야"
                else:
                    text = make_text(rng, nm, opener=(i == 0))
                events.append((t, nm, text))
                prev = nm
        if day.day in (3, 11, 19) and day.month == 2:  # 가끔 입퇴장
            events.append((datetime(day.year, day.month, day.day, 9, 0, 1), None, "지은님이 들어왔습니다."))
        events.sort(key=lambda e: e[0])
        if events:
            out.append("")
            out.append("%d년 %d월 %d일 %s요일" % (day.year, day.month, day.day, wk[day.weekday()]))
            for ts, nm, text in events:
                head = "%d년 %d월 %d일 %s" % (ts.year, ts.month, ts.day, ampm(ts))
                if nm is None:
                    out.append("%s: %s" % (head, text))
                else:
                    out.append("%s, %s : %s" % (head, nm, text))
        day += timedelta(days=1)
    return "\n".join(out) + "\n"


def main(argv):
    path = "sample_chat.txt"
    seed = 7
    args = [a for a in argv[1:]]
    if "--seed" in args:
        i = args.index("--seed")
        seed = int(args[i + 1])
        del args[i:i + 2]
    if args:
        path = args[0]
    rng = random.Random(seed)
    text = render(rng, datetime(2025, 12, 20), datetime(2026, 12, 20))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("wrote", path)


if __name__ == "__main__":
    main(sys.argv)
