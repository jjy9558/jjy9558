"""어댑터 모음. 각 모듈은 NAME, SECTION, DEFAULTS, collect(cfg, ctx) -> list[Item] 을 가진다.

collect 가 SourceSkip 을 던지면 '건너뜀', SourceMissing 이면 '없음'(아직 연결 안 됨), 그 밖의 예외는 '오류'.
"""
from . import (claude_night, community_briefing, cool_off, game_calendar, jarvis, paper_butler, sound_watch,
               voice_journal)

# 표시와 동점 처리 순서
REGISTRY = [paper_butler, game_calendar, cool_off, voice_journal, claude_night, jarvis, sound_watch,
            community_briefing]
BY_NAME = {m.NAME: m for m in REGISTRY}
