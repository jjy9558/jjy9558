"""llm.chat: OpenAI 호환 로컬 엔드포인트(llama-server, vLLM, Ollama의 /v1, LM Studio 등)에 묻는다.

payload: {"prompt": "...", "system": "...", "max_tokens": 800}
     또는 {"messages": [{"role": "user", "content": "..."}]}
     문서 요약: {"prompt": "요약해 줘", "file": "report.pdf" | "@latest"}
       -> files_dir(설정) 안의 파일만. '@latest'는 가장 최근 pdf/txt/md. PDF는 pdftotext(있으면)로 텍스트 추출.
"""
import json
import os
import urllib.error
import urllib.request

from .base import Handler, HandlerError, PermanentError, clip

DOC_EXT = (".pdf", ".txt", ".md")


def resolve_file(files_dir, name):
    if not files_dir or not os.path.isdir(files_dir):
        raise PermanentError("files_dir 이 설정되지 않았거나 없습니다 (handlers.llm.chat.files_dir)")
    if name == "@latest":
        cands = [os.path.join(files_dir, f) for f in os.listdir(files_dir) if f.lower().endswith(DOC_EXT)]
        cands = [c for c in cands if os.path.isfile(c)]
        if not cands:
            raise PermanentError("%s 에 요약할 문서(pdf/txt/md)가 없습니다" % files_dir)
        return max(cands, key=os.path.getmtime)
    if not isinstance(name, str) or not name or os.path.basename(name) != name or name in (".", "..") \
            or "/" in name or "\\" in name:
        raise PermanentError("파일 이름만 쓸 수 있습니다 (경로 불가): %r" % name)
    if not name.lower().endswith(DOC_EXT):
        raise PermanentError("지원하지 않는 파일 형식: %s" % name)
    path = os.path.join(files_dir, name)
    if not os.path.isfile(path):
        raise PermanentError("파일이 없습니다: %s" % name)
    return path


class LlmChatHandler(Handler):
    type = "llm.chat"

    def read_document(self, path):
        if path.lower().endswith(".pdf"):
            code, out, err = self.runner([self.cfg.get("pdftotext") or "pdftotext", "-layout", "-enc", "UTF-8",
                                          path, "-"], timeout=120)
            if code != 0:
                raise PermanentError("PDF 텍스트 추출 실패 (pdftotext 설치 필요): %s" % clip(err, 200))
            text = out
        else:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read(self.cfg.get("max_file_chars", 40000) + 1)
        text = text.strip()
        if not text:
            raise PermanentError("문서에서 텍스트를 찾지 못했습니다 (스캔 PDF는 OCR 필요)")
        return clip(text, self.cfg.get("max_file_chars", 40000))

    def build_messages(self, p):
        if p.get("messages"):
            msgs = p["messages"]
            if not isinstance(msgs, list) or not all(isinstance(m, dict) and isinstance(m.get("content"), str)
                                                     and m.get("role") in ("system", "user", "assistant")
                                                     for m in msgs):
                raise PermanentError("messages 형식 오류")
            return msgs, None
        prompt = p.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise PermanentError("prompt 또는 messages가 필요합니다")
        used = None
        if p.get("file"):
            used = resolve_file(self.cfg.get("files_dir"), p["file"])
            prompt = "%s\n\n--- 문서: %s ---\n%s" % (prompt, os.path.basename(used), self.read_document(used))
        msgs = []
        msgs.append({"role": "system", "content": p.get("system") or "너는 '자비스'라는 개인 비서다. 한국어로 짧고 정확하게 답한다."})
        msgs.append({"role": "user", "content": prompt})
        return msgs, used

    def run(self, job):
        base = (self.cfg.get("base_url") or "").rstrip("/")
        if not base:
            raise PermanentError("llm.chat base_url 이 설정되지 않았습니다")
        p = job.get("payload") or {}
        msgs, used = self.build_messages(p)
        body = {"model": p.get("model") or self.cfg.get("model", "local"), "messages": msgs,
                "max_tokens": int(min(p.get("max_tokens") or self.cfg.get("max_tokens", 800), 4096)),
                "temperature": 0.3}
        req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode("utf-8"),
                                     method="POST")
        req.add_header("Content-Type", "application/json")
        if self.cfg.get("api_key"):
            req.add_header("Authorization", "Bearer " + self.cfg["api_key"])
        opener = self.opener or urllib.request.urlopen
        try:
            with opener(req, timeout=self.cfg.get("timeout", 300)) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise (PermanentError if 400 <= e.code < 500 else HandlerError)("LLM HTTP %s" % e.code)
        except (urllib.error.URLError, OSError) as e:
            raise HandlerError("LLM 서버 연결 실패: %s" % e)
        except ValueError:
            raise HandlerError("LLM 응답이 JSON이 아닙니다")
        try:
            text = data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError, AttributeError):
            raise HandlerError("LLM 응답 형식이 예상과 다릅니다")
        out = {"text": clip(text, 20000), "model": data.get("model") or body["model"], "summary": clip(text, 300)}
        if used:
            out["file"] = os.path.basename(used)
        return out
