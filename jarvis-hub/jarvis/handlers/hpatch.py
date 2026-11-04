"""hpatch.translate: 한글패치(hangul-patcher) 대기열. 5080 워커에서 설정에 등록한 게임 폴더만 처리한다.

payload: {"game": "elden", "limit": 200, "apply": false}
설정:   "hpatch.translate": {"enabled": true, "command": ["python", "-m", "hpatch"], "cwd": "C:/work/hangul-patcher",
          "games": {"elden": {"path": "D:/Games/Elden", "allow_apply": false, "font": "C:/Fonts/Nanum.ttf"}}}

- 게임은 '키'로만 고른다. 경로/폴더 이름은 payload 로 받지 않는다 (알 수 없는 필드는 거부).
- 단계: scan -> extract -> translate --limit N -> (apply: payload.apply 가 true 이고 게임에 allow_apply: true 일 때만).
- translate 는 이어서 하기가 된다. 종료 코드 3(LLM 서버 연결 불가)이면 재시도 가능 오류 -> 허브가 다시 큐에 넣는다.
- 모든 실행은 argv 목록 + 셸 없음. 진행 줄은 결과(progress)에 담는다.
"""
import os
import re

from .base import Handler, HandlerError, PermanentError, clip

ALLOWED_FIELDS = {"game", "limit", "apply"}
GAME_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
EXIT_LLM_DOWN = 3


def validate_payload(payload, cfg):
    """(game_key, limit, apply). 잘못되면 PermanentError."""
    if not isinstance(payload, dict):
        raise PermanentError("payload 는 객체여야 합니다")
    extra = sorted(set(payload) - ALLOWED_FIELDS)
    if extra:
        raise PermanentError("허용되지 않은 필드: %s (게임은 'game' 키로만 지정, 경로 불가)" % ", ".join(map(str, extra)))
    games = cfg.get("games") or {}
    key = payload.get("game")
    if not isinstance(key, str) or not GAME_KEY.match(key) or key not in games:
        raise PermanentError("설정에 없는 게임 키: %r (가능: %s)" % (key if isinstance(key, str) else None,
                                                             ", ".join(sorted(games)) or "없음"))
    limit = payload.get("limit")
    if limit is None:
        limit = cfg.get("default_limit", 200)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise PermanentError("limit 는 1 이상의 정수여야 합니다")
    limit = min(limit, int(cfg.get("max_limit", 5000)))
    apply = payload.get("apply", False)
    if not isinstance(apply, bool):
        raise PermanentError("apply 는 true/false 여야 합니다")
    if apply and games[key].get("allow_apply") is not True:
        raise PermanentError("이 게임(%s)은 apply 가 허용되지 않았습니다 (games.%s.allow_apply)" % (key, key))
    return key, limit, apply


class HpatchTranslateHandler(Handler):
    type = "hpatch.translate"

    def _env(self):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        if self.cfg.get("endpoint"):
            env["HPATCH_ENDPOINT"] = self.cfg["endpoint"]
        if self.cfg.get("model"):
            env["HPATCH_MODEL"] = self.cfg["model"]
        return env

    def run(self, job):
        if not self.cfg.get("enabled"):
            raise PermanentError("hpatch.translate 가 이 워커에서 꺼져 있습니다 (handlers.hpatch.translate.enabled)")
        key, limit, apply = validate_payload(job.get("payload"), self.cfg)
        game = self.cfg["games"][key]
        game_dir = game.get("path")
        if not isinstance(game_dir, str) or not os.path.isdir(game_dir):
            raise PermanentError("게임 폴더가 없습니다: %s" % key)
        base = list(self.cfg.get("command") or ["python", "-m", "hpatch"])
        engine = game.get("engine") or "auto"
        common = ["--engine", engine] if engine != "auto" else []
        cwd = self.cfg.get("cwd") or None
        steps = [("scan", base + ["scan", game_dir] + common, self.cfg.get("scan_timeout", 300)),
                 ("extract", base + ["extract", game_dir] + common, self.cfg.get("scan_timeout", 300)),
                 ("translate", base + ["translate", game_dir, "--limit", str(limit)] + common,
                  self.cfg.get("translate_timeout", 7200))]
        if apply:
            argv = base + ["apply", game_dir] + common
            if game.get("font"):
                argv += ["--font", game["font"]]
            steps.append(("apply", argv, self.cfg.get("apply_timeout", 600)))
        keep = int(self.cfg.get("max_progress_lines", 40))
        done, progress = [], []

        def tail():
            return "\n".join(progress[-10:])

        for name, argv, timeout in steps:
            try:
                code, out, err = self.runner(argv, cwd=cwd, timeout=timeout, env=self._env())
            except HandlerError as e:  # 시간 초과 등: 진행은 저장돼 있으니 이어서 하면 된다
                raise HandlerError("%s 단계 %s\n%s" % (name, e, tail()))
            lines = [clip(ln.rstrip(), 300) for ln in (out or "").splitlines() if ln.strip()][-keep:]
            done.append({"step": name, "returncode": code, "lines": lines})
            progress.extend("[%s] %s" % (name, ln) for ln in lines)
            if code == EXIT_LLM_DOWN and name == "translate":
                raise HandlerError("LLM 서버에 연결할 수 없습니다 (진행은 저장됨, 서버를 켜면 이어서 번역): %s"
                                   % clip(tail(), 600))
            if code != 0:
                raise PermanentError("%s 단계 종료 코드 %s: %s" % (name, code, clip(err or out, 400)))
        result = {"game": key, "limit": limit, "applied": apply, "steps": done,
                  "progress": progress[-keep:]}
        result["summary"] = "%s 한글패치 %s 완료 (translate --limit %d)" % (key, "번역+적용" if apply else "번역", limit)
        return result
