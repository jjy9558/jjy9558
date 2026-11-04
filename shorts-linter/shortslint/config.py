"""기본 임계값과 설정 로딩. JSON 설정 파일로 어떤 값이든 덮어쓸 수 있다."""
import copy
import json
import os

DEFAULTS = {
    "profiles": {
        "shorts": {
            "max_duration": 180,        # 초. 이 값 초과 -> ❌
            "warn_over": 60,            # 초. 이 값 초과 -> ⚠️ (None이면 끔)
            "min_duration": 3,
            "aspect": [9, 16],
            "aspect_tolerance": 0.02,
            "min_width": 1080,
            "min_height": 1920,
            "hard_min_width": 720,      # 이 미만이면 ❌
            "hard_min_height": 1280,
            "min_fps": 24,
            "max_fps": 60,
            "hard_min_fps": 15,
            "containers": ["mp4", "mov"],
            "video_codecs": ["h264", "hevc", "vp9", "av1"],
            "audio_codecs": ["aac", "opus", "mp3"],
            "warn_size_mb": 200,
            "max_size_mb": 1000,
            "target_lufs": -14.0,
            "lufs_warn": 2.0,           # 목표와 차이(LU)
            "lufs_fail": 6.0,
            "max_true_peak": -1.0,      # dBTP, 초과 -> ⚠️ / 0 초과 -> ❌
            "silence_noise_db": -35,
            "silence_min": 2.0,         # 초
            "silence_start_warn": 1.0,  # 시작 무음 길이
            "silence_total_ratio": 0.3,
            "black_min": 0.5,
            "black_pix_th": 0.10,
            "hook_seconds": 1.0,
            "freeze_noise_db": -60,
            "freeze_min": 3.0,
            "freeze_total_ratio": 0.5,
            "ffmpeg_timeout": 600,
        },
        "shorts-long": {"warn_over": None},
    },
    "subtitle": {
        "max_chars_per_line": 16,
        "max_lines": 2,
        "max_cps": 14.0,
        "min_cue_seconds": 0.5,
        "fail_ratio": 0.5,              # 문제 큐 비율이 이 이상이면 ❌
        "ui_bottom_ratio": 0.20,        # 하단 UI 영역 비율
    },
    "metadata": {
        "title_visible_chars": 40,
        "title_max_chars": 100,
        "hashtags_recommended_max": 5,
        "hashtags_ignored_over": 15,
        "description_max": 5000,
        "tags_max_chars": 500,
        "thumbnail_max_mb": 2,
        "require_shorts_tag": True,
        "ai_content": True,             # AI 이미지/음성 사용 채널이면 공개 고지 리마인더
        "ai_disclosure_link": "https://support.google.com/youtube/answer/14328491",
        "duplicate_similarity": 0.85,
        # 사용자 편집 대상 단어 목록
        "banned_words": ["야동", "불법 도박", "토토", "카지노", "먹튀"],
        "clickbait_words": ["충격", "실화", "경악", "소름", "역대급", "레전드",
                            "미쳤", "난리", "무조건", "100%", "절대", "ㄷㄷ", "헐"],
    },
    "promise": {
        "min_keywords": 2,
        "warn_score": 0.5,
        "fail_score": 0.2,
        "word_synonyms": {
            "결말": ["결말", "마지막", "결국", "끝내", "끝이", "엔딩"],
            "반전": ["반전", "알고 보니", "알고보니", "사실은", "뜻밖", "그런데"],
        },
    },
    "similar": {
        "char_n": 3,                    # 글자 n-gram (한국어는 띄어쓰기 변형에 강함)
        "word_n": 2,                    # --words 사용 시 어절 n-gram
        "threshold": 0.5,               # 자카드 유사도가 이 이상이면 재활용 콘텐츠 위험 경고
        "top": 3,
    },
    "titles": {
        "ideal_min_chars": 12,
        "hard_min_chars": 8,
        "exaggeration_words": ["세상에", "믿을 수 없", "난리났", "대박", "미쳤다", "역대급",
                               "전설", "ㅋㅋㅋ", "!!", "???", "속보", "긴급", "필독", "꼭 보세요"],
    },
    "hook": {
        "seconds": 3.0,                 # 훅으로 보는 앞부분 길이
        "fallback_chars": 20,           # 시간 정보가 없을 때 첫 문장 기준 글자 수(공백 제외)
        "ok_score": 70,
        "min_chars": 6,
        "ideal_max_chars": 40,
        "long_chars": 60,
        "audio_start_max": 0.5,         # 오디오가 이 시간(초) 안에 시작해야 함
        "audio_noise_db": -35,
        "first_cue_late": 1.0,          # 첫 대사가 이보다 늦으면 경고
        "filler_starts": ["안녕하세요", "안녕", "여러분", "오늘은", "오늘 ", "자 ", "자,", "음 ", "음,",
                          "어 ", "저는", "이번 영상", "이번에는", "먼저", "구독", "좋아요", "반갑습니다"],
        "conflict_words": ["배신", "거짓말", "싸움", "싸웠", "이혼", "폭로", "무시", "쫓겨", "해고", "의심",
                           "몰래", "숨겼", "비밀", "화가", "분노", "울었", "끊었", "버렸", "속였", "고백"],
        "question_words": ["왜", "어떻게", "무슨", "뭐", "누가", "어디", "언제"],
    },
    "hooks": {
        "overuse_window": 5,            # 최근 N편 안에서
        "overuse_max": 1,               # 같은 템플릿을 이 횟수 초과로 쓰면 경고
        "suggest_count": 3,
    },
    "llm": {
        "enabled": False,
        "base_url": "http://localhost:11434/v1",   # OpenAI 호환 (Ollama, llama.cpp 등)
        "model": "qwen2.5:7b",
        "api_key_env": "",
        "timeout": 60,
        "warn_below": 70,
        "fail_below": 40,
    },
}


def deep_merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def load_config(path=None, profile="shorts"):
    cfg = copy.deepcopy(DEFAULTS)
    if path is None and os.path.isfile("shortslint.json"):
        path = "shortslint.json"
    if path:
        with open(path, encoding="utf-8") as f:
            deep_merge(cfg, json.load(f))
    profiles = cfg["profiles"]
    if profile not in profiles:
        raise ValueError("알 수 없는 프로파일: %s (사용 가능: %s)" % (profile, ", ".join(profiles)))
    base = copy.deepcopy(profiles["shorts"])
    deep_merge(base, profiles[profile])
    cfg["video"] = base
    cfg["profile_name"] = profile
    return cfg
