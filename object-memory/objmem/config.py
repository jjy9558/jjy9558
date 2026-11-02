import json
import os
import re
import secrets

DEFAULTS = {
    "base_url": "http://127.0.0.1:8080/v1",
    "model": "local",
    "api_key": "none",
    "vlm_timeout": 180,
    "data_dir": "~/.objmem/data",
    "token": "",                # 또는 환경변수 OBJMEM_TOKEN
    "host": "127.0.0.1",
    "port": 8788,
    "max_frame_bytes": 6 * 1024 * 1024,
    "hash_threshold": 4,        # 8x8 평균 해시 해밍 거리 <= 이면 거의 같은 프레임으로 보고 건너뜀
    "photo_days": 7,
    "desc_days": 90,
    "privacy_hours": [],        # 예: ["23:00-07:00"] 이 시간대에는 아무것도 저장하지 않음
    "stale_hours": 24,          # 이보다 오래된 목격은 "마지막으로 본 게 N일 전" 로 안내
    "llm_phrase": False,        # ask 답변을 VLM 서버(텍스트)로 다듬기
    "use_fts": True,            # False 면 FTS5 가 있어도 자체 bigram 색인 사용
}


def default_path():
    return os.environ.get("OBJMEM_CONFIG") or os.path.expanduser("~/.objmem/config.json")


def load(path=None):
    path = path or default_path()
    cfg = dict(DEFAULTS)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            user = json.load(f)
        if not isinstance(user, dict):
            raise ValueError("설정 파일은 JSON 객체여야 합니다")
        cfg.update(user)
    if os.environ.get("OBJMEM_TOKEN"):
        cfg["token"] = os.environ["OBJMEM_TOKEN"]
    cfg["data_dir"] = os.path.expanduser(cfg["data_dir"])
    return cfg


def init(path=None):
    path = path or default_path()
    if os.path.exists(path):
        raise FileExistsError(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    cfg = dict(DEFAULTS)
    cfg["token"] = secrets.token_urlsafe(24)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


_RANGE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$")


def parse_ranges(ranges):
    out = []
    for r in ranges or []:
        m = _RANGE.match(str(r))
        if not m:
            raise ValueError("privacy_hours 형식 오류: %r (예: '23:00-07:00')" % (r,))
        a, b, c, d = (int(x) for x in m.groups())
        if not (a < 24 and c <= 24 and b < 60 and d < 60):
            raise ValueError("privacy_hours 범위 오류: %r" % (r,))
        out.append((a * 60 + b, (c * 60 + d) % 1440))
    return out


def in_privacy(ranges, minute_of_day):
    """자정을 넘는 범위(23:00-07:00) 지원. 시작==끝 이면 하루 종일."""
    for s, e in parse_ranges(ranges):
        if s == e:
            return True
        if s < e and s <= minute_of_day < e:
            return True
        if s > e and (minute_of_day >= s or minute_of_day < e):
            return True
    return False
