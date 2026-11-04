"""wiki.ask: 로컬 위키 Q&A. 사용자가 저장해 둔 노트(md/txt/html) 폴더만 검색한다. 웹 접속 없음.

payload: {"question": "보스 약점이 뭐야", "game": "elden"}   (game 생략 = 모든 폴더)
설정:   "wiki.ask": {"enabled": true, "sources": {"elden": "C:/notes/elden"}, "index_db": "C:/notes/.wiki.sqlite",
                    "llm": {"base_url": "http://127.0.0.1:8080/v1"}}

- 폴더는 설정의 키로만 고른다. payload 에 경로를 쓰면 거부.
- 답: 상위 문단(파일 이름 포함). 로컬 LLM(OpenAI 호환, 선택)이 있으면 문단만 근거로 요약하고 파일 이름을 인용하게 한다.
  LLM 이 없거나 실패하면 문단 그대로 돌려준다. 인용이 빠지면 출처 줄을 덧붙인다.
"""
import ipaddress
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from ..wikiindex import WikiIndex
from .base import Handler, PermanentError, clip

ALLOWED_FIELDS = {"question", "game"}
GAME_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
MAX_QUESTION = 500


def is_local_url(url):
    """LLM 주소는 이 PC/사설망/tailnet 만 허용 (웹으로 나가지 않는다)."""
    try:
        u = urllib.parse.urlparse(url)
    except ValueError:
        return False
    if u.scheme not in ("http", "https") or not u.hostname:
        return False
    host = u.hostname.lower()
    if host == "localhost" or "." not in host:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host.endswith((".local", ".lan", ".internal", ".ts.net"))
    return ip.is_loopback or ip.is_private or ip.is_link_local or ip in ipaddress.ip_network("100.64.0.0/10")


def validate_payload(payload, cfg):
    if not isinstance(payload, dict):
        raise PermanentError("payload 는 객체여야 합니다")
    extra = sorted(set(payload) - ALLOWED_FIELDS)
    if extra:
        raise PermanentError("허용되지 않은 필드: %s (경로 불가, 게임은 'game' 키로만)" % ", ".join(map(str, extra)))
    q = payload.get("question")
    if not isinstance(q, str) or not q.strip():
        raise PermanentError("question 이 비었습니다")
    if len(q) > MAX_QUESTION:
        raise PermanentError("question 이 너무 깁니다")
    sources = cfg.get("sources") or {}
    game = payload.get("game") or ""
    if game:
        if not isinstance(game, str) or not GAME_KEY.match(game) or game not in sources:
            raise PermanentError("설정에 없는 게임 키: %r (가능: %s)" % (game if isinstance(game, str) else None,
                                                                 ", ".join(sorted(sources)) or "없음"))
    return q.strip(), game


class WikiAskHandler(Handler):
    type = "wiki.ask"

    def ask_llm(self, question, passages):
        llm = self.cfg.get("llm") or {}
        base = (llm.get("base_url") or "").rstrip("/")
        if not base:
            return None, "LLM 미설정"
        if not is_local_url(base):
            return None, "LLM 주소가 로컬이 아님 (웹 접속 금지)"
        ctx = "\n\n".join("[%s] (%s)\n%s" % (p["file"], p["heading"] or "-", p["text"]) for p in passages)
        msgs = [{"role": "system", "content": "너는 사용자가 직접 저장한 게임 노트만 근거로 답하는 도우미다. 아래 문단에 없는 "
                 "내용은 지어내지 말고 '노트에 없음'이라고 말한다. 한국어로 짧게 답하고, 근거로 쓴 문단의 파일 이름을 "
                 "[파일이름] 형태로 반드시 인용한다."},
                {"role": "user", "content": "질문: %s\n\n--- 노트 문단 ---\n%s" % (question, ctx)}]
        body = {"model": llm.get("model", "local"), "messages": msgs, "temperature": 0.2,
                "max_tokens": int(min(llm.get("max_tokens", 700), 4096))}
        req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode("utf-8"), method="POST")
        req.add_header("Content-Type", "application/json")
        if llm.get("api_key"):
            req.add_header("Authorization", "Bearer " + llm["api_key"])
        opener = self.opener or urllib.request.urlopen
        try:
            with opener(req, timeout=llm.get("timeout", 120)) as r:
                data = json.loads(r.read().decode("utf-8"))
            text = data["choices"][0]["message"]["content"].strip()
        except urllib.error.HTTPError as e:
            return None, "LLM HTTP %s" % e.code
        except (urllib.error.URLError, OSError) as e:
            return None, "LLM 연결 실패: %s" % e
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            return None, "LLM 응답 형식 오류"
        return (text or None), ("" if text else "LLM 빈 응답")

    def run(self, job):
        if not self.cfg.get("enabled"):
            raise PermanentError("wiki.ask 가 이 워커에서 꺼져 있습니다 (handlers.wiki.ask.enabled)")
        question, game = validate_payload(job.get("payload"), self.cfg)
        sources = self.cfg.get("sources") or {}
        if not sources:
            raise PermanentError("설정 오류: handlers.wiki.ask.sources 가 비었습니다")
        idx = WikiIndex(self.cfg.get("index_db") or ":memory:",
                        use_fts=not self.cfg.get("force_fallback_index"),
                        max_passage_chars=self.cfg.get("max_passage_chars", 700))
        try:
            stats = {}
            for key, folder in sources.items():
                if game and key != game:
                    continue
                stats[key] = idx.scan_folder(key, folder)
            hits = idx.search(question, source=game or None, top_k=int(self.cfg.get("top_k", 5)))
            mode = "fts5" if idx.fts else "bigram"
        finally:
            idx.close()
        passages = [{"file": h["file"], "game": h["source"], "heading": h["heading"], "text": clip(h["text"], 700),
                     "score": h["score"]} for h in hits]
        citations = list(dict.fromkeys(p["file"] for p in passages))
        out = {"question": question, "game": game, "index": mode, "indexed": stats, "passages": passages,
               "citations": citations}
        if not passages:
            out.update(answer="위키에서 관련 내용을 찾지 못했어요.", mode="none", text="위키에서 관련 내용을 찾지 못했어요.")
            out["summary"] = out["answer"]
            return out
        answer, why = self.ask_llm(question, hits)
        if answer:
            names = [c for c in citations if c in answer or c.rsplit("/", 1)[-1] in answer]
            if not names:
                answer += "\n출처: " + ", ".join(citations)
            out.update(mode="llm", answer=clip(answer, 4000))
        else:
            body = "\n\n".join("[%s]%s %s" % (p["file"], (" " + p["heading"]) if p["heading"] else "", p["text"])
                               for p in passages[:3])
            out.update(mode="passages", llm_error=why, answer="관련 문단 (요약 없음: %s)\n%s\n출처: %s"
                       % (why, body, ", ".join(citations)))
        out["text"] = out["answer"]
        out["summary"] = clip(out["answer"], 300)
        return out
