"""말(텍스트 명령) -> 의도 -> 허브 상태로 대답하거나 작업을 만든다.

"자비스, 오늘 일정이랑 작업 상태 알려줘"
  -> "오늘 일정 1개: 14:00 치과. 작업 3개 진행 중, 1개 승인 대기, 5080 꺼져 있음."
"""
import datetime as _dt

from . import schedule
from .intents import parse

UNKNOWN = "잘 못 알아들었어요. 예: '작업 상태 알려줘', '오늘 일정', '컴퓨터 켜', '이 PDF 요약해줘'."
HELP = ("할 수 있는 것: 작업 상태, 기기 상태, 오늘 일정, 컴퓨터 켜기, 문서 요약, "
        "한글패치 이어서 돌리기(승인 필요), 위키에서 찾기, 로컬 모델에게 질문, 클로드에게 작업 시키기(승인 필요), 'N번 승인/취소'.")


class Router:
    def __init__(self, hub):
        self.hub = hub

    @property
    def rc(self):
        return self.hub.cfg["router"]

    # ---- 진입점 ----
    def handle(self, device_id, text, direct=False, file=None):
        text = str(text)[:500]
        self.hub.seen(device_id)  # 말한 기기는 살아 있다
        p = parse(text, self.rc["wake_words"], self.rc["shell_aliases"],
                  self.rc.get("games"))
        if self.rc["require_wake_word"] and not p.wake and not direct:
            return {"ok": True, "ignored": True, "reason": "호출어(자비스) 없음", "answer": "", "intents": [],
                    "jobs": []}
        intents = list(p.intents)
        if not intents and self.rc["llm_fallback"] and p.text:
            from .intents import Intent
            intents = [Intent("ask", {"question": p.text})]
        parts, jobs = [], []
        if not intents:
            parts.append(UNKNOWN)
        for it in intents:
            args = dict(it.args)
            if it.name == "summarize" and isinstance(file, str) and file:
                args["file"] = file
            try:
                getattr(self, "i_" + it.name)(device_id, args, parts, jobs)
            except Exception as e:  # ApiError 등: 말로 돌려준다
                parts.append("%s 처리 실패: %s" % (it.name, getattr(e, "message", None) or e))
        answer = " ".join(s for s in parts if s)
        if self.hub.cfg["notify"]["say_events"] and answer:
            self.hub.store.add_event("say", answer, to=device_id, source="router")
        return {"ok": True, "ignored": False, "heard": p.text, "intents": [i.name for i in intents],
                "answer": answer, "jobs": jobs}

    # ---- 상태 문장 ----
    def status_line(self):
        c = self.hub.store.counts(exclude_types=("notify.say",))  # 내부 알림 작업은 세지 않는다
        items = []
        for n, word in ((c["leased"], "진행 중"), (c["queued"], "대기"), (c["waiting_approval"], "승인 대기")):
            if n:
                items.append(("작업 " if not items else "") + "%d개 %s" % (n, word))
        if not items:
            items.append("진행 중인 작업 없음")
        failed = self.hub.store.recent_failures(24 * 3600)
        if failed:
            items.append("최근 하루 실패 %d개" % failed)
        for d in self.hub.wake_devices():
            items.append("%s %s" % (d["label"], "켜져 있음" if d["online"] else "꺼져 있음"))
        return ", ".join(items) + "."

    def devices_line(self):
        devs = self.hub.device_list()
        if not devs:
            return "등록된 기기가 없어요."
        return ", ".join("%s %s" % (d["label"], "켜져 있음" if d["online"] else "꺼져 있음") for d in devs) + "."

    def _offline_note(self, caps, jtype):
        if self.hub.can_run_online(caps, jtype):
            return ""
        if self.hub.cfg["wake"]["enabled"] and set(caps) & set(self.hub.cfg["wake"]["for_caps"]):
            return " %s이(가) 꺼져 있어서 켜는 중이에요. 켜지면 시작해요." % self.hub.wake_label()
        return " 지금 처리할 컴퓨터가 없어서 대기해요."

    # ---- 의도별 ----
    def i_status(self, did, a, parts, jobs):
        parts.append(self.status_line())

    def i_devices(self, did, a, parts, jobs):
        parts.append(self.devices_line())

    def i_schedule(self, did, a, parts, jobs):
        today = self.hub.today() if self.hub.today else None
        parts.append(schedule.describe(schedule.today_events(self.hub.cfg["schedule"]["path"], today)))

    def i_help(self, did, a, parts, jobs):
        parts.append(HELP)

    def i_time(self, did, a, parts, jobs):
        now = _dt.datetime.fromtimestamp(self.hub.clock())
        h = now.hour
        parts.append("지금 %s %d시 %d분이에요." % ("오전" if h < 12 else "오후", (h % 12) or 12, now.minute))

    def i_wake_pc(self, did, a, parts, jobs):
        label = self.hub.wake_label()
        if not self.hub.cfg["wake"]["enabled"]:
            parts.append("Wake-on-LAN이 설정되지 않았어요 (hub.json의 wake).")
        elif self.hub.wake_target_online():
            parts.append("%s은(는) 이미 켜져 있어요." % label)
        else:
            job = self.hub.request_wake(force=True, created_by=did, notify=True)
            if job:
                jobs.append(job["id"])
                parts.append("%s 켜는 신호 보낼게요." % label)
            else:
                parts.append("%s 켜는 중이에요. 조금만 기다려 주세요." % label)

    def _submit(self, did, body, jobs):
        job = self.hub.submit(did, dict(body, notify=True))
        jobs.append(job["id"])
        return job

    def i_summarize(self, did, a, parts, jobs):
        caps = self.rc["llm_caps"]
        job = self._submit(did, {"type": "llm.chat", "caps": caps, "title": "문서 요약",
                                 "payload": {"task": "summarize", "file": a.get("file") or "@latest",
                                             "prompt": "이 문서를 한국어로 핵심만 3~5문장으로 요약해 줘."}}, jobs)
        what = "PDF" if a.get("kind") == "pdf" else "문서"
        parts.append("%s 요약 작업 %d번 넣었어요.%s" % (what, job["id"], self._offline_note(caps, "llm.chat")))

    def i_ask(self, did, a, parts, jobs):
        q = (a.get("question") or "").strip()
        if not q:
            parts.append("무엇을 물어볼까요?")
            return
        caps = self.rc["llm_caps"]
        job = self._submit(did, {"type": "llm.chat", "caps": caps, "title": "질문",
                                 "payload": {"prompt": q}}, jobs)
        parts.append("물어볼게요. 작업 %d번.%s" % (job["id"], self._offline_note(caps, "llm.chat")))

    def i_claude(self, did, a, parts, jobs):
        prompt = (a.get("prompt") or "").strip()
        if not prompt:
            parts.append("클로드에게 무엇을 시킬지 말해 주세요.")
            return
        job = self._submit(did, {"type": "claude.task", "caps": self.rc["claude_caps"], "title": "클로드 작업",
                                 "payload": {"prompt": prompt, "repo": self.rc["claude_repo"] or "default"}}, jobs)
        if job["status"] == "waiting_approval":
            parts.append("클로드 작업 %d번 만들었어요. '자비스, %d번 승인'이라고 하면 시작해요." % (job["id"], job["id"]))
        else:
            parts.append("클로드 작업 %d번 시작할게요.%s" % (job["id"], self._offline_note(self.rc["claude_caps"], "claude.task")))

    def i_hpatch(self, did, a, parts, jobs):
        key = a.get("game")
        if not key:
            known = ", ".join(sorted(self.rc.get("games") or {})) or "없음"
            parts.append("어느 게임인지 모르겠어요. (등록된 게임: %s)" % known)
            return
        caps = self.rc["hpatch_caps"]
        payload = {"game": key, "limit": int(self.rc.get("hpatch_limit") or 200)}
        if a.get("apply"):
            payload["apply"] = True
        job = self._submit(did, {"type": "hpatch.translate", "caps": caps, "title": "한글패치 %s" % key,
                                 "payload": payload, "max_attempts": 10}, jobs)
        if job["status"] == "waiting_approval":
            parts.append("%s 한글패치 작업 %d번 만들었어요. '자비스, %d번 승인'이라고 하면 시작해요."
                         % (key, job["id"], job["id"]))
        else:
            parts.append("%s 한글패치 작업 %d번 시작할게요.%s" % (key, job["id"], self._offline_note(caps, "hpatch.translate")))

    def i_wiki(self, did, a, parts, jobs):
        q = (a.get("question") or "").strip()
        if not q:
            parts.append("위키에서 무엇을 찾을까요?")
            return
        caps = self.rc["wiki_caps"]
        payload = {"question": q}
        if a.get("game"):
            payload["game"] = a["game"]
        job = self._submit(did, {"type": "wiki.ask", "caps": caps, "title": "위키 질문", "payload": payload}, jobs)
        parts.append("위키에서 찾아볼게요. 작업 %d번.%s" % (job["id"], self._offline_note(caps, "wiki.ask")))

    def i_run(self, did, a, parts, jobs):
        spec = a.get("spec") or {}
        if not isinstance(spec, dict) or not spec.get("name"):
            parts.append("'%s' 명령 설정이 잘못됐어요." % a.get("alias"))
            return
        job = self._submit(did, {"type": "shell.allowed", "caps": spec.get("caps", []), "title": a["alias"],
                                 "payload": {"name": spec["name"], "args": spec.get("args") or {}}}, jobs)
        parts.append("%s 작업 %d번 넣었어요.%s" % (a["alias"], job["id"], self._offline_note(spec.get("caps", []), "shell.allowed")))

    def _pick(self, a, statuses, verb):
        if a.get("id"):
            return a["id"], None
        waiting = self.hub.store.list(status=statuses, limit=20)
        if not waiting:
            return None, "%s할 작업이 없어요." % verb
        if len(waiting) > 1:
            return None, "대상이 %d개예요: %s. 번호로 말해 주세요." % (
                len(waiting), ", ".join("%d번" % j["id"] for j in reversed(waiting)))
        return waiting[0]["id"], None

    def i_approve(self, did, a, parts, jobs):
        if not self.hub.cfg["devices"][did].get("approve"):
            parts.append("이 기기는 승인 권한이 없어요.")
            return
        jid, msg = self._pick(a, ["waiting_approval"], "승인")
        if msg:
            parts.append(msg)
            return
        job = self.hub.approve(did, jid)
        jobs.append(job["id"])
        parts.append("%d번 승인했어요. 시작할게요." % job["id"])

    def i_reject(self, did, a, parts, jobs):
        jid, msg = self._pick(a, ["waiting_approval"], "취소")
        if msg:
            parts.append(msg)
            return
        job = self.hub.reject(did, jid)
        jobs.append(job["id"])
        parts.append("%d번 취소했어요." % job["id"])
