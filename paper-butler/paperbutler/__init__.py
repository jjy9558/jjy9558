"""개인 서류 비서 (paper-butler).

고지서·영수증·결제 문자·안내문을 사진/PDF/텍스트로 받아 핵심만 뽑아 추적한다.
모든 처리는 로컬에서 이루어지며 표준 라이브러리만 사용한다(핵심 로직).
"""

__version__ = "0.1.0"

import logging as _logging

_logging.getLogger("paperbutler").addHandler(_logging.NullHandler())
