"""jjkit: 이 저장소 프로젝트들이 같이 쓰는 표준 라이브러리 전용 기반 모듈.

원본은 여기 하나만 고친다. 각 프로젝트는 ``tools/jjkit_sync.py``로 ``<pkg>/_jjkit/``에 복사해서 쓴다.
모듈끼리는 상대 import(``from .netutil import ...``)만 쓰므로 복사본도 그대로 동작한다.
"""
__version__ = "0.1.0"

MODULES = ("netutil", "llm", "notify", "textwidth", "redact", "httpkit", "ics", "claude_logs")
