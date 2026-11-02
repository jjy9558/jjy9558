"""오늘의 15분 실험: 기본 목록과 날짜별 선택."""
import random

DEFAULT_EXPERIMENTS = [
    "Claude한테 처음 써보는 스킬 하나 시켜보기",
    "안 써본 단축키 1개 익히기",
    "Claude Code에게 내 프로젝트 README 개선안 물어보기",
    "자주 하는 반복 작업 하나를 슬래시 커맨드로 만들어 보기",
    "오늘 쓴 코드 중 가장 지저분한 함수 하나 Claude와 같이 정리하기",
    "CLAUDE.md에 내 작업 규칙 3줄 추가하기",
    "안 써본 터미널 명령어 하나 man/--help로 읽어 보기",
    "테스트가 없는 함수 하나에 테스트 1개 붙이기",
    "Claude에게 '내가 모르는 줄 모르는 것'을 질문해 보기",
    "책상 위 물건 3개만 제자리에 두기",
    "10분 산책하며 오늘 할 일 한 가지만 정하기",
    "미뤄둔 메일/메시지 하나에 답장 쓰기",
    "프로젝트 폴더에서 안 쓰는 파일 5개 정리하기",
    "다른 사람의 코드 50줄 읽고 배운 점 한 줄 메모하기",
    "내가 쓰는 에디터 설정 하나 바꿔 보기",
    "Claude에게 오늘 할 일을 3개로 줄여달라고 부탁하기",
    "깃 커밋 메시지를 평소보다 정성껏 써 보기",
    "새로운 정규식 하나를 직접 만들어 보기",
    "일기 대신 오늘 배운 것 한 줄 적기",
    "타이머 15분 맞추고 가장 하기 싫은 일의 첫 5분만 해 보기",
]


def load_experiments(path=None):
    """사용자 파일(한 줄에 하나)이 있으면 그것을, 없으면 기본 목록을 쓴다."""
    if path is not None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]
            if lines:
                return lines
        except (OSError, UnicodeDecodeError):
            pass
    return list(DEFAULT_EXPERIMENTS)


def write_default_experiments(path):
    """편집용 기본 파일을 만든다 (이미 있으면 건드리지 않음)."""
    import os
    if os.path.exists(path):
        return False
    os.makedirs(os.path.dirname(str(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("# 한 줄에 실험 하나. '#'로 시작하면 무시됩니다.\n")
        f.write("\n".join(DEFAULT_EXPERIMENTS) + "\n")
    return True


def experiment_for(date, experiments):
    """같은 날에는 항상 같은 실험을 돌려준다."""
    if not experiments:
        return None
    rng = random.Random(date.toordinal())
    return rng.choice(experiments)
