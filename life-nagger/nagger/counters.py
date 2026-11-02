"""한숨/푸시업/설거지 카운터. 추가 전용 JSONL 로그라서 데몬과 CLI가 동시에 써도 안전하다."""
import json
from datetime import datetime, timedelta

from .timeutil import WEEKDAYS_KO, ko_hour

DISH_LINES = {
    0: "오늘 설거지 완료. 싱크대가 당신을 존경합니다.",
    1: "설거지 방치 1일째. 어제 그릇이 아직 싱크대에서 당신을 기다려요.",
    2: "설거지 방치 2일째. 컵에 곰팡이 꽃이 필 준비를 하고 있어요.",
    3: "설거지 방치 3일째. 싱크대가 이제 하나의 생태계입니다.",
    4: "설거지 방치 4일째. 접시들이 '여기가 내 집인가' 하고 있어요.",
    5: "설거지 방치 5일째. 이쯤 되면 설거지가 아니라 발굴 작업이에요.",
    6: "설거지 방치 6일째. 엄마가 보면 등짝입니다.",
}


def dish_line(days):
    if days is None:
        return "설거지 기록이 아직 없어요. 일단 한 번 하고 `count dishes`로 도장 찍어요."
    if days in DISH_LINES:
        return DISH_LINES[days]
    return "설거지 방치 %d일째. 이건 이제 설거지가 아니라 문화재 보존입니다. 제발." % days


def dish_level(days):
    from .model import INFO, WARN, URGENT
    if days is None or days < 3:
        return INFO
    return WARN if days < 5 else URGENT


class Counters:
    def __init__(self, path, kinds):
        self.path = path
        self.kinds = kinds      # {"sigh": {"label","unit"}, ...}

    def add(self, kind, when, n=1):
        if kind not in self.kinds:
            raise ValueError("알 수 없는 카운터: %r (가능: %s)" % (kind, ", ".join(self.kinds)))
        if not isinstance(n, int) or isinstance(n, bool) or n < 1 or n > 10000:
            raise ValueError("n은 1~10000 정수여야 합니다")
        line = json.dumps({"t": when.isoformat(timespec="seconds"), "k": kind, "n": n}, ensure_ascii=False)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _read(self):
        rows = []
        try:
            f = open(self.path, encoding="utf-8")
        except FileNotFoundError:
            return rows
        with f:
            for line in f:
                try:
                    o = json.loads(line)
                    rows.append((datetime.fromisoformat(o["t"]), o["k"], int(o.get("n", 1))))
                except (ValueError, KeyError, TypeError):
                    continue       # 깨진 줄은 무시
        return rows

    def total(self, kind, start, end):
        return sum(n for t, k, n in self._read() if k == kind and start <= t < end)

    def peak(self, kind, start, end):
        """(요일 index, 시, 횟수) 또는 None. 동률이면 주중 앞선 시간."""
        buckets = {}
        for t, k, n in self._read():
            if k == kind and start <= t < end:
                key = (t.weekday(), t.hour)
                buckets[key] = buckets.get(key, 0) + n
        if not buckets:
            return None
        (wd, hr), cnt = max(sorted(buckets.items()), key=lambda kv: kv[1])
        return wd, hr, cnt

    def today_total(self, kind, now):
        s = datetime(now.year, now.month, now.day)
        return self.total(kind, s, s + timedelta(days=1))

    def last_time(self, kind):
        ts = [t for t, k, _ in self._read() if k == kind]
        return max(ts) if ts else None

    def dish_days(self, now):
        last = self.last_time("dishes")
        if last is None:
            return None
        return max(0, (now.date() - last.date()).days)

    @staticmethod
    def week_range(now, offset=0):
        monday = datetime(now.year, now.month, now.day) - timedelta(days=now.weekday())
        start = monday + timedelta(weeks=offset)
        return start, start + timedelta(weeks=1)

    def weekly_report(self, now, offset=0):
        start, end = self.week_range(now, offset)
        word = "이번 주" if offset == 0 else ("지난 주" if offset == -1 else "%d주 전" % -offset)
        lines = []
        for kind, meta in self.kinds.items():
            if kind == "dishes":
                continue
            total = self.total(kind, start, end)
            if total == 0:
                lines.append("%s %s 기록 없음. 평화롭네요." % (word, meta["label"]))
                continue
            wd, hr, _ = self.peak(kind, start, end)
            lines.append("%s %s %d%s, %s %s 최고점" % (word, meta["label"], total, meta["unit"],
                                                       WEEKDAYS_KO[wd], ko_hour(hr)))
        if "dishes" in self.kinds:
            done = self.total("dishes", start, end)
            lines.append("%s 설거지 %d%s. %s" % (word, done, self.kinds["dishes"]["unit"],
                                                dish_line(self.dish_days(now)) if offset == 0 else ""))
        return "\n".join(l.rstrip() for l in lines)
