"""점심 운명 로딩: 가짜 로딩바 뒤에 메뉴 공개. 최근 3번 고른 메뉴는 피한다."""
import time

AVOID_LAST = 3
STAGES = [
    "운명의 서버에 접속하는 중",
    "배고픔 수치 측정 중",
    "냉장고 속 사정 무시하는 중",
    "위장과 협상 중",
    "점심 신께 기도하는 중",
    "마지막 한 입 상상하는 중",
]


def parse_menu(text):
    parts = [p.strip() for p in text.replace("\n", ",").split(",")]
    out = []
    for p in parts:
        if p and p not in out:
            out.append(p)
    return out


def pick_lunch(menu, history, rng):
    """반환: (선택, 제외된 메뉴 목록). 후보가 남도록 제외 개수는 len(menu)-1 이하로 제한."""
    if not menu:
        raise ValueError("점심 후보가 없어요. --menu \"김치찌개,라면,돈까스\" 로 알려 주세요.")
    n = min(AVOID_LAST, len(menu) - 1)
    recent = []
    for h in reversed(history):
        if h in menu and h not in recent:
            recent.append(h)
        if len(recent) >= n:
            break
    cands = [m for m in menu if m not in recent]
    return rng.choice(cands), recent


def progress_steps(rng, width_steps=8):
    """가짜 진행률: 불규칙하게 오르다 99%에서 한 번 멈칫."""
    pct, out = 0, []
    while pct < 99:
        pct = min(99, pct + rng.randint(3, 22))
        out.append(pct)
    out.append(100)
    return out


def bar(pct, width=20):
    filled = pct * width // 100
    return "█" * filled + "░" * (width - filled)


def animate(choice, rng, write, sleep=time.sleep, delay=0.15, flush=None):
    steps = progress_steps(rng)
    for i, pct in enumerate(steps):
        stage = STAGES[min(len(STAGES) - 1, i * len(STAGES) // len(steps))]
        write("\r%s %3d%%... %s" % (bar(pct), pct, stage) + " " * 6)
        if flush:
            flush()
        sleep(delay * (4 if pct == 99 else 1))
    write("\r" + " " * 70 + "\r")
    write("🍚 오늘의 점심 운명: %s\n" % choice)
