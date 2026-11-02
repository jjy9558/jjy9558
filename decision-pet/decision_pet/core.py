"""핵심 로직: 할 일 풀, pick(), 묘지, 마크다운 가져오기, Pet 서비스. GUI 의존성 없음."""
import random
import re
from datetime import datetime

from . import decide as decide_mod
from .experiments import experiment_for, load_experiments
from .storage import Store

EFFORTS = (5, 15, 60)
ISO = "%Y-%m-%dT%H:%M:%S"
DATE_FMT = "%Y.%m.%d"


def fmt(dt):
    return dt.strftime(ISO)


def parse_dt(s):
    return datetime.strptime(s, ISO) if s else None


# ---------------------------------------------------------------- tasks

def new_task(task_id, title, effort=15, tags=None, now=None):
    title = (title or "").strip()
    if not title:
        raise ValueError("제목이 비어 있어요.")
    if effort not in EFFORTS:
        raise ValueError("effort는 5/15/60 중 하나여야 해요.")
    now = now or datetime.now()
    return {
        "id": task_id,
        "title": title,
        "created": fmt(now),
        "effort": effort,
        "tags": list(tags or []),
        "times_skipped": 0,
        "last_suggested": None,
        "status": "todo",
        "done_at": None,
        "buried_at": None,
        "bury_reason": None,
    }


# ----------------------------------------------------------------- pick

def score_task(task, now):
    """투명한 가점표. 반환: [(이유, 점수), ...] (무작위 항목 제외)"""
    parts = []
    if task["effort"] == 15:
        parts.append(("15분짜리라 부담이 적어서", 3.0))
    elif task["effort"] == 5:
        parts.append(("5분이면 끝나서", 1.5))
    days = (now - parse_dt(task["created"])).days
    if days >= 1:
        parts.append(("%d일째 미뤄서" % days, round(min(days, 12) * 2 / 3, 1)))
    if task["times_skipped"] > 0:
        parts.append(("%d번 미룬 이력이 있어서" % task["times_skipped"],
                      min(task["times_skipped"] * 0.5, 2.0)))
    last = parse_dt(task.get("last_suggested"))
    if last is not None:
        hours = (now - last).total_seconds() / 3600.0
        if hours < 1:
            parts.append(("방금 추천했던 거라서", -4.0))
        elif hours < 6:
            parts.append(("몇 시간 전에 추천했던 거라서", -2.5))
        elif hours < 24:
            parts.append(("오늘 이미 추천했던 거라서", -1.0))
    return parts


def _fmt_pts(p):
    return ("%+g" % p) + "점"


def pick(tasks, now=None, rng=None):
    """todo 중 하나를 고른다. 반환: dict(task, score, reasons, explanation) 또는 None."""
    now = now or datetime.now()
    rng = rng or random
    best = None
    for t in tasks:
        if t["status"] != "todo":
            continue
        parts = score_task(t, now)
        noise = round(rng.uniform(0, 0.5), 2)
        total = round(sum(p for _, p in parts) + noise, 2)
        if best is None or total > best["score"]:
            best = {"task": t, "score": total, "reasons": parts}
    if best is None:
        return None
    if best["reasons"]:
        why = ", ".join("%s %s" % (r, _fmt_pts(p)) for r, p in best["reasons"])
    else:
        why = "딱히 감점도 가점도 없어서 운에 맡겼어요"
    best["explanation"] = "%s (합계 %.1f점)" % (why, best["score"])
    return best


# ------------------------------------------------------------- graveyard

def bury_reason(task, now, max_skips=5, max_age_days=30):
    if task["status"] != "todo":
        return None
    if task["times_skipped"] >= max_skips:
        return "skips"
    if (now - parse_dt(task["created"])).days > max_age_days:
        return "age"
    return None


def bury(task, now, reason):
    task["status"] = "buried"
    task["buried_at"] = fmt(now)
    task["bury_reason"] = reason
    return task


def epitaph(task):
    if task.get("bury_reason") == "vjournal":
        return "여기 잠들다: 선언했다가 포기 (%s, %s)" % (task.get("declared_day") or "?", task.get("raw") or task["title"])
    created = parse_dt(task["created"]).strftime(DATE_FMT)
    buried = parse_dt(task["buried_at"]).strftime(DATE_FMT) if task.get("buried_at") else "?"
    return "여기 잠들다: %s (%s~%s, 미룬 횟수 %d)" % (
        task["title"], created, buried, task["times_skipped"])


def resurrect(task, now):
    if task["status"] != "buried":
        raise ValueError("묘지에 있는 할 일이 아니에요.")
    task["status"] = "todo"
    task["times_skipped"] = 0
    task["created"] = fmt(now)  # 나이 초기화: 바로 다시 묻히지 않게
    task["last_suggested"] = None
    task["buried_at"] = None
    task["bury_reason"] = None
    task["resurrections"] = task.get("resurrections", 0) + 1
    return task


# -------------------------------------------------------------- markdown

_TODO_RE = re.compile(r"^\s*[-*+]\s+\[ \]\s+(.+?)\s*$")
_EFFORT_RE = re.compile(r"[\[(]\s*(5|15|60)\s*(?:m|min|분)\s*[\])]", re.I)
_TAG_RE = re.compile(r"(?:(?<=\s)|^)#([^\s#]+)")


def parse_markdown_todos(text):
    """미완료 '- [ ]' 항목만. 반환: [{title, effort, tags}]"""
    items = []
    for line in text.splitlines():
        m = _TODO_RE.match(line)
        if not m:
            continue
        body = m.group(1)
        effort = 15
        em = _EFFORT_RE.search(body)
        if em:
            effort = int(em.group(1))
            body = (body[:em.start()] + body[em.end():])
        tags = _TAG_RE.findall(body)
        body = _TAG_RE.sub("", body)
        title = re.sub(r"\s+", " ", body).strip()
        if title:
            items.append({"title": title, "effort": effort, "tags": tags})
    return items


# ------------------------------------------------------------------- Pet

class Pet:
    """저장소 + 규칙을 묶은 서비스. now/rng/opener를 주입할 수 있어 테스트가 쉽다."""

    def __init__(self, store=None, rng=None, clock=None, opener=None):
        self.store = store or Store()
        self.rng = rng or random.Random()
        self.clock = clock or datetime.now
        self.opener = opener
        self.state = self.store.load()

    # -- helpers
    def now(self):
        return self.clock()

    def save(self):
        self.store.save(self.state)

    @property
    def config(self):
        return self.state["config"]

    def get(self, task_id):
        for t in self.state["tasks"]:
            if t["id"] == task_id:
                return t
        raise KeyError("할 일 #%s 를 찾을 수 없어요." % task_id)

    def tasks(self, status=None):
        return [t for t in self.state["tasks"] if status is None or t["status"] == status]

    # -- add / import
    def add(self, title, effort=15, tags=None):
        t = new_task(self.state["next_id"], title, effort, tags, self.now())
        self.state["next_id"] += 1
        self.state["tasks"].append(t)
        self.save()
        return t

    def import_markdown(self, text):
        existing = {t["title"] for t in self.tasks("todo")}
        added = []
        for it in parse_markdown_todos(text):
            if it["title"] in existing:
                continue
            existing.add(it["title"])
            added.append(self.add(it["title"], it["effort"], it["tags"]))
        return added

    # -- now / done / skip
    def sweep(self):
        """묻힐 때가 된 할 일을 묘지로. 반환: 새로 묻힌 목록."""
        now = self.now()
        buried = []
        for t in self.state["tasks"]:
            r = bury_reason(t, now, self.config["max_skips"], self.config["max_age_days"])
            if r:
                bury(t, now, r)
                if self.state["current"] == t["id"]:
                    self.state["current"] = None
                buried.append(t)
        if buried:
            self.save()
        return buried

    def suggest(self):
        self.sweep()
        res = pick(self.state["tasks"], self.now(), self.rng)
        if res:
            res["task"]["last_suggested"] = fmt(self.now())
            self.state["current"] = res["task"]["id"]
        self.save()
        return res

    def current_task(self):
        cid = self.state.get("current")
        if cid is None:
            return None
        try:
            t = self.get(cid)
        except KeyError:
            return None
        return t if t["status"] == "todo" else None

    def _target(self, task_id):
        if task_id is None:
            t = self.current_task()
            if t is None:
                raise LookupError("지금 진행 중인 할 일이 없어요. 먼저 'now'를 해보세요.")
            return t
        return self.get(task_id)

    def done(self, task_id=None):
        t = self._target(task_id)
        t["status"] = "done"
        t["done_at"] = fmt(self.now())
        if self.state["current"] == t["id"]:
            self.state["current"] = None
        self.save()
        return t

    def skip(self, task_id=None):
        """미루기. 반환: (task, 묻혔는지)"""
        t = self._target(task_id)
        t["times_skipped"] += 1
        if self.state["current"] == t["id"]:
            self.state["current"] = None
        r = bury_reason(t, self.now(), self.config["max_skips"], self.config["max_age_days"])
        if r:
            bury(t, self.now(), r)
        self.save()
        return t, bool(r)

    # -- voice-journal 묘지 가져오기
    def import_vjournal(self, db_path):
        from .vjournal_import import read_abandoned
        found = read_abandoned(db_path, self.now().date())
        existing = {t.get("source_key") for t in self.state["tasks"]}
        added = []
        for g in found:
            key = "vjournal:%d" % g["id"]
            if key in existing:
                continue
            t = new_task(self.state["next_id"], g["title"], 15, ["vjournal"], self.now())
            self.state["next_id"] += 1
            t.update(status="buried", buried_at=fmt(self.now()), bury_reason="vjournal",
                     source_key=key, declared_day=g["declared_day"], raw=g["raw"],
                     created=g["declared_day"] + "T00:00:00")
            self.state["tasks"].append(t)
            existing.add(key)
            added.append(t)
        if added:
            self.save()
        return added

    # -- graveyard
    def graveyard(self):
        return sorted(self.tasks("buried"), key=lambda t: t["buried_at"] or "")

    def resurrect(self, task_id):
        t = resurrect(self.get(task_id), self.now())
        self.save()
        return t

    # -- experiment
    def experiments(self):
        return load_experiments(self.store.experiments_path)

    def experiment_today(self):
        today = self.now().date()
        text = experiment_for(today, self.experiments())
        log = self.state["experiment_log"].get(today.isoformat(), {})
        return text, bool(log.get("done"))

    def complete_experiment(self):
        today = self.now().date().isoformat()
        text, _ = self.experiment_today()
        self.state["experiment_log"][today] = {"text": text, "done": True}
        self.save()
        return text

    # -- 의회 / 점심
    def record_council(self, result):
        rec = {
            "id": self.state["next_decision_id"], "at": fmt(self.now()), "mode": "council",
            "options": ["하자", "말자"], "choice": "하자" if result["verdict"] == "가결" else "말자",
            "reason": "%s: %s (찬성 %d : 반대 %d)" % (result["question"], result["verdict"],
                                                  result["yes"], result["no"]),
            "satisfaction": None, "dismissed": False,
        }
        self.state["next_decision_id"] += 1
        self.state["decisions"].append(rec)
        self.save()
        return rec

    def lunch(self, menu=None):
        from .lunch import pick_lunch
        lu = self.state["lunch"]
        if menu:
            lu["menu"] = list(menu)
        choice, excluded = pick_lunch(lu["menu"], lu["history"], self.rng)
        lu["history"] = (lu["history"] + [choice])[-20:]
        self.save()
        return choice, excluded

    # -- decisions
    def decide(self, options, mode="coin", criteria=None, scores=None):
        if mode == "coin":
            res = decide_mod.decide_coin(options, self.rng)
        elif mode == "score":
            res = decide_mod.decide_scores(options, criteria, scores, self.rng)
        elif mode == "llm":
            res = decide_mod.decide_llm(options, self.config["llm"], self.rng, self.opener)
        else:
            raise ValueError("알 수 없는 모드: %s" % mode)
        rec = {
            "id": self.state["next_decision_id"],
            "at": fmt(self.now()),
            "mode": res["mode"],
            "options": res["options"],
            "choice": res["choice"],
            "reason": res["reason"],
            "satisfaction": None,
            "dismissed": False,
        }
        self.state["next_decision_id"] += 1
        self.state["decisions"].append(rec)
        self.save()
        res["id"] = rec["id"]
        return res

    def pending_followups(self):
        """어제 이전에 한 결정 중 아직 만족도를 안 물어본 것."""
        today = self.now().date()
        return [d for d in self.state["decisions"]
                if d["satisfaction"] is None and not d["dismissed"]
                and parse_dt(d["at"]).date() < today]

    def rate_decision(self, decision_id, satisfaction):
        if satisfaction is None:
            raise ValueError("만족도를 입력하세요.")
        if not (1 <= satisfaction <= 5):
            raise ValueError("만족도는 1~5예요.")
        for d in self.state["decisions"]:
            if d["id"] == decision_id:
                d["satisfaction"] = satisfaction
                self.save()
                return d
        raise KeyError("결정 #%s 없음" % decision_id)

    def dismiss_followup(self, decision_id):
        for d in self.state["decisions"]:
            if d["id"] == decision_id:
                d["dismissed"] = True
                self.save()
                return
        raise KeyError("결정 #%s 없음" % decision_id)

    def satisfaction_history(self):
        """모드별 평균 만족도. 반환: {mode: (평균, 개수)}, 전체는 'all'"""
        buckets = {}
        for d in self.state["decisions"]:
            if d["satisfaction"] is None:
                continue
            for key in (d["mode"], "all"):
                buckets.setdefault(key, []).append(d["satisfaction"])
        return {k: (round(sum(v) / len(v), 2), len(v)) for k, v in buckets.items()}
