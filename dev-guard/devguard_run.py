"""설치 없이 실행하기 위한 런처 (git hook / Claude Code hook 에서 절대경로로 호출).

    python /path/to/dev-guard/devguard_run.py <subcommand> ...

`python -m devguard` 와 동일하지만 현재 디렉터리/PYTHONPATH 에 의존하지 않는다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from devguard.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
