#!/usr/bin/env python3
"""Claude Code PreToolUse 훅 진입점 (설치 없이 경로로 실행).

settings.json:  "command": "python C:/path/to/s21-sidekick/hooks/claude_pretooluse.py"
설정 파일은 --config, $SIDEKICK_CONFIG, 저장소 루트의 config.json 순서로 찾는다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sidekick.hook import main  # noqa: E402

if __name__ == "__main__":
    cfg = None
    if len(sys.argv) >= 3 and sys.argv[1] == "--config":
        cfg = sys.argv[2]
    raise SystemExit(main(cfg))
