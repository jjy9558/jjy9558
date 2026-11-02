"""dev-guard: 개발자 안전장치 팩 (Python 3.9+, 표준 라이브러리만 사용).

구성 요소 (각각 단독으로 사용 가능):
  scanner / precommit  - .env 커밋 도둑 경보 (git pre-commit)
  friday               - 금요일 배포 차단기 (git pre-push)
  strike               - 에이전트 파업 (Claude Code hooks)
  wallet               - 지갑 고발장 (Claude Code 사용량/비용 감시)
  snippet              - .claude/settings.json hooks 블록 출력
"""

__version__ = "0.1.0"
