"""설치 없이 실행하기 위한 런처 (작업 스케줄러/cron 에서 절대경로로 호출).

    python /path/to/homelab/homelab_run.py --dir ~/.homelab run morning-brief

``python -m homelab`` 과 같지만 현재 디렉터리에 의존하지 않는다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from homelab.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
