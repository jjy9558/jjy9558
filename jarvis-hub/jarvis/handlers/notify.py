"""notify.say: 폰(S21)이 소리 내어 읽게 s21-sidekick으로 전달한다.

s21-sidekick 현황 (2026-11 기준 코드 확인): 폰 페이지는 GET /api/events 를 롱폴링해서
kind == "say" 인 이벤트를 speechSynthesis로 읽는다. 하지만 '외부에서 say 이벤트를 넣는'
범용 엔드포인트는 아직 없다 (/api/utterance 는 트리거 판정용, /api/arrival 은 귀가 집사용).

그래서 이 핸들러는 설정된 경로(기본 POST /api/say, 헤더 X-Sidekick-Token, 본문 {"text","module"})로
보내 보고, 설정이 없거나 엔드포인트가 없으면(404) 로그만 남기고 delivered=false 로 끝낸다.
사이드킥 쪽에 필요한 코드는 README의 'S21 사이드킥 연동' 절 참고 (라우트 한 줄 + 핸들러 5줄).
"""
import json
import logging
import urllib.error
import urllib.request

from .base import Handler, PermanentError

log = logging.getLogger("jarvis.notify.say")


class NotifySayHandler(Handler):
    type = "notify.say"

    def run(self, job):
        p = job.get("payload") or {}
        text = p.get("text")
        if not isinstance(text, str) or not text.strip():
            raise PermanentError("text가 비었습니다")
        text = text.strip()[:500]
        url = (self.cfg.get("sidekick_url") or "").rstrip("/")
        if not url:
            log.info("[say] %s", text)
            return {"delivered": False, "logged": True, "reason": "sidekick_url 미설정", "text": text}
        body = json.dumps({"text": text, "module": p.get("module") or "jarvis"}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url + (self.cfg.get("path") or "/api/say"), data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        if self.cfg.get("token"):
            req.add_header("X-Sidekick-Token", self.cfg["token"])
        opener = self.opener or urllib.request.urlopen
        try:
            with opener(req, timeout=self.cfg.get("timeout", 5)) as r:
                r.read()
            return {"delivered": True, "text": text}
        except urllib.error.HTTPError as e:
            reason = "사이드킥에 say 엔드포인트가 없습니다 (README 참고)" if e.code == 404 else "HTTP %s" % e.code
        except (urllib.error.URLError, OSError) as e:
            reason = "사이드킥 연결 실패: %s" % e
        log.warning("[say 전달 실패: %s] %s", reason, text)
        return {"delivered": False, "logged": True, "reason": reason, "text": text}
