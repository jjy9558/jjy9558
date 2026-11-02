"""GM 엔진: 한 턴 처리(2단계 판정), 상태 적용, 요약, 명령어."""
import logging

from . import context as ctx
from .dice import Dice, format_check
from .llm import LLMError
from .parser import parse_gm_output

log = logging.getLogger("gmtrpg")

SYSTEM_PROMPT = """당신은 한국어 텍스트 TRPG의 게임 마스터(GM)입니다. 플레이어의 행동에 생생하지만 간결하게(3~8문장) 서술하세요.
규칙:
- 아래 [컨텍스트]의 세계 설정, NPC, 퀘스트, 기록과 모순되지 않게 진행하고, 세계관의 분위기(전연령 기본)를 지키세요.
- 플레이어의 행동을 대신 결정하지 마세요. 서술 끝에서 플레이어가 고를 수 있는 상황을 열어 두세요.
- 주사위는 직접 굴리지 마세요. 결과가 불확실한 행동이면 서술을 짧게 끝내고 마지막에 JSON으로 판정을 요청하세요:
```json
{"roll": {"skill": "설득", "dc": 12}}
```
  (선택: "mode": "advantage"|"disadvantage"). 판정 결과를 받으면 그에 맞춰 이어서 서술하세요.
- 서술 뒤에 상태 변화가 있으면 아래 형식의 펜스 JSON 블록 하나를 붙이세요. 변화가 없으면 생략해도 됩니다.
```json
{"state_updates": {"move_to": "장소명", "add_location": [{"name": "", "description": "", "links": []}],
 "add_npc": [{"name": "", "traits": "", "relation": "", "location": ""}],
 "update_npc": [{"name": "", "relation": ""}], "add_item": [{"name": "", "qty": 1}],
 "remove_item": [{"name": "", "qty": 1}], "quest_add": [{"title": "", "detail": ""}],
 "quest_done": ["퀘스트 제목"], "add_fact": ["오래 기억할 사실"], "hp_change": -3}}
```
- 필요한 키만 쓰세요. 중요한 약속, 발견, 인물 관계 변화는 add_fact로 남기세요."""

SUMMARY_PROMPT = "다음은 TRPG 진행 기록입니다. 기존 요약과 새 기록을 합쳐 이후 진행에 필요한 핵심(사건, 인물 관계, 미해결 사항)만 담아 한국어 10줄 이내로 요약하세요. 요약문만 출력하세요."

HELP = "명령어: /상태 /인벤 /퀘스트 /지도 /요약 /저장 /되감기 /세계관 /도움말 /종료"


class TurnResult:
    def __init__(self, narration, rolls=None, applied=None, errors=None):
        self.narration = narration
        self.rolls = rolls or []
        self.applied = applied or []
        self.errors = errors or []


class GameMaster:
    def __init__(self, world, llm, dice=None, recent_turns=6, summary_batch=4):
        self.world = world
        self.llm = llm
        self.dice = dice or Dice()
        self.recent_turns = recent_turns
        self.summary_batch = summary_batch

    # ---------- 턴 ----------
    def _messages(self, user_text):
        w = self.world
        system = SYSTEM_PROMPT + "\n\n[컨텍스트]\n" + ctx.build_context(w, user_text)
        msgs = [{"role": "system", "content": system}]
        for h in w.history(after_turn=max(0, w.turn - self.recent_turns)):
            msgs.append({"role": h["role"], "content": h["content"]})
        msgs.append({"role": "user", "content": user_text})
        return msgs

    def turn(self, user_text):
        w = self.world
        w.snapshot()  # 되감기용: 이 턴 시작 직전 상태
        msgs = self._messages(user_text)
        raw1 = self.llm.chat(msgs)
        p1 = parse_gm_output(raw1)
        errors = list(p1.errors)
        narration = p1.narration
        rolls, updates_list = [], [p1.updates]
        if p1.roll:
            r = p1.roll
            mod = r["modifier"] if r["modifier"] is not None else int(w.stats.get(r["skill"], 0))
            res = self.dice.check(r["skill"], r["dc"], mod, r["mode"])
            rolls.append(res)
            line = format_check(res)
            msgs.append({"role": "assistant", "content": raw1})
            msgs.append({"role": "user", "content":
                         "%s\n판정 결과(%s)에 맞춰 장면을 이어서 서술하세요. 다시 판정을 요청하지 마세요." % (line, res["grade"])})
            raw2 = self.llm.chat(msgs)
            p2 = parse_gm_output(raw2)
            errors += p2.errors
            narration = "\n\n".join(x for x in (p1.narration, line, p2.narration) if x)
            updates_list.append(p2.updates)
        applied = []
        for u in updates_list:
            applied += self.apply_updates(u)
        t = w.turn + 1
        w.set("turn", t)
        w.add_history(t, "user", user_text)
        w.add_history(t, "assistant", narration)
        w.commit()
        self.maybe_summarize()
        return TurnResult(narration, rolls, applied, errors)

    # ---------- 상태 적용 ----------
    def apply_updates(self, u):
        w, log_ = self.world, []
        for e in u.get("add_location", []):
            w.add_location(e["name"], e.get("description", ""), e.get("links", []))
            log_.append("장소 추가: " + e["name"])
        if "move_to" in u:
            dest, old = u["move_to"], w.location
            if not w.has_location(dest):
                w.add_location(dest)
            if dest != old:
                w.link(old, dest)
            w.set("location", dest)
            log_.append("이동: %s -> %s" % (old, dest))
        for e in u.get("add_npc", []):
            w.upsert_npc(e["name"], e.get("traits"), e.get("relation"), e.get("location") or w.location)
            log_.append("NPC 등록: " + e["name"])
        for e in u.get("update_npc", []):
            if w.get_npc(e["name"]) is None:
                log.warning("update_npc: 알 수 없는 NPC %s", e["name"])
                continue
            w.upsert_npc(e["name"], e.get("traits"), e.get("relation"), e.get("location"))
            log_.append("NPC 갱신: " + e["name"])
        for e in u.get("add_item", []):
            w.add_item(e["name"], e.get("qty", 1), e.get("description", ""))
            log_.append("획득: %s x%d" % (e["name"], e.get("qty", 1)))
        for e in u.get("remove_item", []):
            if w.remove_item(e["name"], e.get("qty", 1)):
                log_.append("소모: " + e["name"])
            else:
                log.warning("remove_item: 없는 아이템 %s", e["name"])
        for e in u.get("quest_add", []):
            if w.add_quest(e["title"], e.get("detail", "")):
                log_.append("퀘스트 추가: " + e["title"])
        for t in u.get("quest_done", []):
            done = w.finish_quest(t)
            if done:
                log_.append("퀘스트 완료: " + done)
            else:
                log.warning("quest_done: 없는 퀘스트 %s", t)
        for f in u.get("add_fact", []):
            if w.add_fact(f):
                log_.append("기록: " + f)
        if "hp_change" in u:
            hp = max(0, min(w.max_hp, w.hp + u["hp_change"]))
            w.set("hp", hp)
            log_.append("HP %+d -> %d/%d" % (u["hp_change"], hp, w.max_hp))
        return log_

    # ---------- 요약 ----------
    def _summarize(self, upto, kind="rolling"):
        w = self.world
        done = int(w.get("summarized_upto", 0))
        rows = w.history(done, upto)
        if not rows:
            return False
        prior = w.latest_summary()
        text = "기존 요약:\n%s\n\n새 기록:\n%s" % (
            prior["text"] if prior else "(없음)",
            "\n".join("%s: %s" % ("플레이어" if r["role"] == "user" else "GM", r["content"]) for r in rows))
        try:
            out = self.llm.chat([{"role": "system", "content": SUMMARY_PROMPT},
                                 {"role": "user", "content": text}], max_tokens=600, temperature=0.3).strip()
        except LLMError as e:
            log.warning("요약 실패: %s", e)
            return False
        if not out:
            return False
        w.add_summary(upto, out, kind)
        w.set("summarized_upto", upto)
        w.commit()
        return True

    def maybe_summarize(self):
        w = self.world
        if w.turn - int(w.get("summarized_upto", 0)) >= self.recent_turns + self.summary_batch:
            return self._summarize(w.turn - self.recent_turns)
        return False

    # ---------- 명령어 ----------
    def state_view(self):
        w = self.world
        s = w.latest_summary()
        return {"name": w.get("name"), "player": w.get("player_name"), "turn": w.turn,
                "hp": w.hp, "max_hp": w.max_hp, "location": w.location,
                "npcs": [n["name"] + (" (%s)" % n["relation"] if n["relation"] else "") for n in w.npcs(w.location)],
                "inventory": ["%s x%d" % (i["name"], i["qty"]) for i in w.items()],
                "quests": [q["title"] for q in w.quests("open")],
                "done_quests": [q["title"] for q in w.quests("done")],
                "map": self.cmd_map(), "summary": s["text"] if s else "", "can_undo": w.can_undo()}

    def cmd_map(self):
        w = self.world
        rows = []
        for l in w.locations():
            mark = "[*]" if l["name"] == w.location else "[ ]"
            rows.append("%s %s" % (mark, l["name"]))
            if l["links"]:
                rows.append("     +-- " + ", ".join(l["links"]))
        return "\n".join(rows) or "(알려진 장소 없음)"

    def command(self, text):
        """'/'로 시작하면 처리 결과 문자열, 아니면 None."""
        text = text.strip()
        if not text.startswith("/"):
            return None
        cmd = text.split()[0]
        w = self.world
        if cmd == "/상태":
            v = self.state_view()
            return "%s | 턴 %d | HP %d/%d | 위치: %s\n동행/주변 NPC: %s" % (
                v["player"], v["turn"], v["hp"], v["max_hp"], v["location"], ", ".join(v["npcs"]) or "없음")
        if cmd == "/인벤":
            inv = self.state_view()["inventory"]
            return "소지품:\n" + "\n".join("- " + i for i in inv) if inv else "소지품이 없습니다."
        if cmd == "/퀘스트":
            o, d = w.quests("open"), w.quests("done")
            out = ["진행 중:"] + ["- %s%s" % (q["title"], " (%s)" % q["detail"] if q["detail"] else "") for q in o] if o else ["진행 중인 퀘스트 없음"]
            if d:
                out += ["완료:"] + ["- " + q["title"] for q in d]
            return "\n".join(out)
        if cmd == "/지도":
            return self.cmd_map()
        if cmd == "/요약":
            self._summarize(w.turn, "session")
            s = w.latest_summary()
            return s["text"] if s else "아직 요약할 내용이 없습니다."
        if cmd == "/저장":
            self._summarize(w.turn, "session")
            w.commit()
            return "저장했습니다. (턴 %d, 파일: %s)" % (w.turn, w.path)
        if cmd == "/되감기":
            n = w.undo()
            return "턴 %d을(를) 되감았습니다." % n if n else "되감을 수 있는 턴이 없습니다."
        if cmd == "/세계관":
            return "\n".join("%s: %s" % (l, w.get(k) or "-") for l, k in
                             (("이름", "name"), ("배경", "setting"), ("분위기", "tone"), ("규칙", "rules")))
        if cmd in ("/도움말", "/help"):
            return HELP
        return "알 수 없는 명령어입니다. " + HELP

    def handle(self, text):
        """명령이면 문자열, 아니면 턴 처리 후 TurnResult를 문자열 서술로. (reply, TurnResult|None)"""
        out = self.command(text)
        if out is not None:
            return out, None
        res = self.turn(text)
        return res.narration, res

    def start(self):
        if self.world.turn == 0:
            return self.turn("(모험을 시작한다. 플레이어가 처음 마주하는 장면을 묘사하라.)")
        return None
