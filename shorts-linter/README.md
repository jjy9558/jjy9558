# shortslint - 쇼츠 업로드 전 린터

사연/스토리 쇼츠(AI 이미지 + 나레이션)를 배치로 만들 때, **업로드 직전에** 영상·자막·메타데이터를 자동 점검하고
**제목/썸네일이 약속한 내용이 실제 영상에 있는지**(B9)까지 확인하는 CLI입니다.

- Python 3.9+ 표준 라이브러리만 사용 (설치 불필요)
- `ffmpeg`/`ffprobe`가 있으면 영상 분석, 없으면 해당 검사만 `⏭️ 건너뜀`으로 표시 (자막/메타/약속 검사는 계속 동작)
- 모든 외부 호출은 인자 리스트(`shell=False`), 네트워크는 기본적으로 사용하지 않음 (LLM 판정은 옵션, 로컬 서버)

## 사용법

```bash
python -m shortslint check video.mp4 --meta meta.json --profile shorts
python -m shortslint check-dir ./out --md report.md --json report.json
```

옵션: `--config cfg.json`(임계값/단어 덮어쓰기, 현재 폴더의 `shortslint.json`은 자동 로드), `--strict`(⚠️도 실패 처리),
`--llm`(로컬 LLM 판정 켜기), `--quiet`(콘솔 출력 생략).
종료 코드: `0` 통과 / `1` ❌ 있음 / `2` 사용 오류.

### 영상 옆에 두는 파일 (자동 탐색)

| 파일 | 용도 |
|---|---|
| `clip.srt` 또는 `clip.ass` | 자막 검사 + 대본(약속 일치 비교) |
| `clip.txt` | 나레이션 원고 (자막과 합쳐 대본으로 사용) |
| `clip.meta.json` 또는 `clip.json` | 메타데이터 (`check`에서는 `--meta`로 직접 지정 가능) |

```json
{
  "title": "시어머니가 숨긴 3가지 비밀 #shorts",
  "description": "...",
  "tags": ["사연", "썸"],
  "hashtags": ["#shorts", "#사연"],
  "thumbnail": "clip_thumb.jpg",
  "thumbnail_text": "결말 반전",
  "claims": ["실화", "충격"],
  "ai_disclosure": true,
  "transcript": "clip.txt"
}
```

`thumbnail_text`는 썸네일에 넣은 문구입니다 (OCR은 하지 않으므로 직접 적어 주세요).

## 검사 항목과 이유

### 영상 (ffprobe/ffmpeg)
| 검사 | 기준 | 왜 중요한가 |
|---|---|---|
| 길이 | 3분 초과 ❌, 60초 초과 ⚠️(프로파일 `shorts`), 3초 미만 ⚠️ | 쇼츠 규격 초과 시 일반 영상으로 분류됨. 60초 넘으면 끝까지 시청 비율이 떨어지기 쉬움 |
| 화면비/해상도 | 9:16, 1080x1920 이상 (720x1280 미만 ❌) | 가로/저해상도는 레터박스·화질 저하. 회전 메타데이터(rotate)도 반영 |
| 프레임레이트 | 24~60fps (15 미만 ❌) | 끊김, 가변 프레임 이슈 방지 |
| 코덱/컨테이너 | mp4/mov + H.264/HEVC/VP9/AV1, AAC/Opus/MP3, yuv420p | 업로드 후 재인코딩 지연/실패 및 색 문제 방지 |
| 오디오 | 오디오 스트림 필수 | TTS/나레이션 합성 누락 방지 |
| 라우드니스 | `loudnorm` 분석, -14 LUFS 목표 (±2 LU ⚠️, ±6 LU ❌) | 너무 작은 나레이션은 시청 이탈. 유튜브는 큰 소리를 낮출 뿐 키우지 않음 |
| 트루피크 | -1 dBTP 초과 ⚠️, 0 초과 ❌ | 클리핑/왜곡 방지 |
| 무음 | `silencedetect`, 2초 이상 무음 / 시작 무음 1초 이상 / 무음 비율 30% 초과 ⚠️ | 나레이션 사이 끊김, TTS 오류 발견 |
| 첫 1초 훅 | 시작 블랙 ⚠️, 첫 1초 전체 블랙 ❌ (`blackdetect`) | 쇼츠는 첫 프레임에서 스와이프 여부가 결정됨 |
| 블랙 프레임 | 중간/끝 0.5초 이상 ⚠️ | 이미지 렌더 실패, 컷 누락 |
| 정지 화면 | `freezedetect`, 처음 1초 정지 또는 전체 50% 초과 ⚠️ | 줌/팬 효과 없는 정지 이미지는 지루해 보임 |
| 파일 크기 | 200MB ⚠️, 1000MB ❌ | 업로드 시간/실패 방지 |

### 자막 (`.srt`/`.ass`)
| 검사 | 기준 | 왜 |
|---|---|---|
| 줄 길이 | 한 줄 16자 초과 ⚠️ (50% 이상이면 ❌), 큐당 2줄 초과 ⚠️ | 세로 영상 폭에서 줄바꿈/잘림 방지 |
| CPS | 공백 제외 초당 14자 초과 ⚠️ | 읽을 시간 부족, 0.5초 미만 큐 |
| 하단 UI 영역 | ASS는 `\pos`/`\an`/`MarginV`로 위치 계산, 하단 20% 안이면 ⚠️/❌. SRT는 위치 정보가 없어 일반 경고 | 쇼츠 제목·채널명·버튼이 자막을 가림 |

### 메타데이터 (`meta.json`)
| 검사 | 기준 | 왜 |
|---|---|---|
| 제목 길이 | 40자 초과 ⚠️(피드에서 잘림), 100자 초과 ❌ | 핵심 문구가 잘리지 않게 |
| 해시태그 | `#shorts` 없음 ⚠️, 5개 초과 ⚠️, 15개 초과 ❌ | 15개 초과 시 유튜브가 해시태그를 모두 무시 |
| 금지 단어 | `banned_words` 포함 시 ❌ | 광고 제한/정책 위반 위험 (사용자 편집) |
| 낚시성 단어 | `clickbait_words` 포함 시 ⚠️ ("실화", "충격" 등) | 영상이 뒷받침하지 않으면 신뢰/노출 하락 |
| AI 고지 | `ai_disclosure: true`가 아니면 ⚠️ 리마인더 | 현실적인 합성/변형 콘텐츠는 업로드 시 고지 필요. 링크 문구만 표시하며, 정확한 정책은 직접 확인하세요 |
| 썸네일 파일 | 존재, 2MB 이하 | 업로드 실패 방지 |
| 배치 중복 | 폴더 내 제목 동일 ❌, 85% 이상 유사 ⚠️ | 같은 제목 반복 업로드 방지 |

### 약속 일치 (B9)
제목 + `thumbnail_text` + `claims`를 대본(자막/원고)과 비교합니다.
- **키워드 일치도**: 조사 제거 후 핵심어가 대본에 있는 비율. 50% 미만 ⚠️, 20% 미만 ❌
- **약속 항목**: 제목의 숫자("3가지" -> 대본에 3/세 가지), 이름·인용부호 문구, "결말"/"반전" 약속이 대본에 있는지 (동의어는 `promise.word_synonyms`)
- **LLM 판정(선택)**: `--llm` 또는 설정 `llm.enabled: true`. OpenAI 호환 `/chat/completions` 엔드포인트(Ollama, llama.cpp 서버 등)를 호출하며, 실패하면 규칙 기반 결과만 사용합니다. 대본은 앞 6000자만 전송됩니다.

## 임계값 설정

`shortslint.json`(현재 폴더) 또는 `--config`로 필요한 값만 적으면 기본값에 병합됩니다.

```json
{
  "profiles": {
    "shorts": {"warn_over": 45, "target_lufs": -14, "min_height": 1920},
    "my-long": {"max_duration": 600, "warn_over": null}
  },
  "subtitle": {"max_chars_per_line": 14, "max_cps": 12},
  "metadata": {
    "banned_words": ["토토", "카지노"],
    "clickbait_words": ["충격", "실화", "소름"],
    "hashtags_recommended_max": 3,
    "ai_content": true
  },
  "promise": {"warn_score": 0.6},
  "llm": {"enabled": true, "base_url": "http://localhost:11434/v1", "model": "qwen2.5:7b", "api_key_env": ""}
}
```

프로파일: `shorts`(기본, 180초 한도 + 60초 초과 경고), `shorts-long`(60초 경고 없음). `--profile 이름`으로 선택하며,
설정 파일에 새 프로파일을 추가하면 `shorts` 값을 기본으로 덮어씁니다. 전체 키는 `shortslint/config.py`의 `DEFAULTS`를 참고하세요.

## 제작 파이프라인에 넣기

```bash
# 렌더 끝난 폴더를 한 번에 검사; ❌가 있으면 업로드 단계로 넘기지 않음
python -m shortslint check-dir ./render --json lint.json --md lint.md --quiet || { echo "린트 실패, lint.md 확인"; exit 1; }
python upload.py ./render
```

- 영상마다 `<이름>.meta.json`, `<이름>.srt|ass`, `<이름>.txt`를 같이 생성해 두면 배치 중복/약속 일치까지 한 번에 검사됩니다.
- 엄격 모드(`--strict`)는 ⚠️도 실패로 처리합니다. CI에서는 평소 ❌만, 릴리스 전에는 `--strict`를 권장합니다.
- `lint.json`의 `reports[].results[]`(`name`, `status`: ok/warn/fail/skip, `message`)를 후처리하면 실패한 영상만 재렌더 큐에 넣을 수 있습니다.
- 대용량 배치는 영상당 ffmpeg 2회(오디오/비디오 분석) 디코딩이 필요합니다. 필요하면 `ffmpeg_timeout`을 조정하세요.

## 테스트

```bash
python3 -m unittest discover -s tests -v
```
ffmpeg가 설치돼 있으면 lavfi로 합성 영상을 만들어 end-to-end 검사도 실행합니다.

## 한계

- 첫 프레임의 "공백(흰색 등 단색)"은 별도 감지하지 않고 블랙/정지 감지로 근사합니다.
- 썸네일 이미지 속 글자는 OCR하지 않습니다 (`thumbnail_text` 직접 입력).
- 한국어 키워드 추출은 간단한 조사 제거 규칙 기반이라 활용형 일부는 놓칠 수 있습니다.
