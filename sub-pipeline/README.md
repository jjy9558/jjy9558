# sub-pipeline: 자막/번역 외주용 반자동 파이프라인

```
미디어 --transcribe--> 원문 SRT --translate--> 번역 초안 + 검수 HTML
      (faster-whisper)          (llama-server)         |
                                                   사람 검수 (핵심 경쟁력)
                                                        |
                         최종 SRT <--apply-review-- 편집본 JSON <-- HTML 다운로드
                              |
                             qa (납품 전 점검)
```

핵심은 **사람의 검수**입니다. 이 도구는 검수를 생략하는 용도가 아니라, 검수가 빨라지도록
문제 줄에 플래그를 달아주는 용도입니다. Python 3.9+, 핵심 코드는 표준 라이브러리만 사용합니다
(faster-whisper는 `transcribe` 단계에서만 import).

## 설치

```bash
cd sub-pipeline
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install faster-whisper                           # transcribe 단계에만 필요
```

### CUDA (RTX 5080)
- NVIDIA 최신 드라이버 + CUDA 12 런타임(cuBLAS, cuDNN 9)이 필요합니다. RTX 50 시리즈(Blackwell)는
  구버전 CUDA/ctranslate2 조합에서 동작하지 않을 수 있으니, faster-whisper/ctranslate2를 최신으로 올리세요.
- `--device cuda` 기본값, 메모리가 부족하면 `--compute-type int8_float16`.
- GPU 없이 시험할 때: `--device cpu --model small`.
- `ffmpeg`/`ffprobe`가 있으면 `price`에서 영상 길이를 자동으로 읽습니다.

### llama-server (번역 LLM)
```bash
llama-server -m your-model.gguf --port 8080 -c 8192 -ngl 99
```
기본 주소는 `http://127.0.0.1:8080/v1` (OpenAI 호환). 다른 주소/모델은 `--base-url`, `--llm-model`.
한국어 번역 품질이 좋은 모델(Qwen, Gemma 계열 등)을 직접 골라 테스트하세요.
Whisper와 LLM을 한 GPU에서 동시에 쓰면 VRAM이 부족할 수 있으니 순차 실행을 권장합니다.

## 명령어

모든 명령은 `python3 -m subpipe <명령>` 으로 실행합니다.

### 1) 전사
```bash
python3 -m subpipe transcribe talk.mp4 --model large-v3 --lang auto --device cuda
# -> talk.en.srt  (단어 타임스탬프 기반으로 줄/길이 재분할)
```
분할 옵션: `--max-chars 42 --max-lines 2 --min-dur 1.0 --max-dur 7.0 --min-gap 0.08`.
문장 끝, 0.8초 이상의 침묵, 길이 한도에서 끊고, 너무 짧은 자막은 병합합니다.
**원문 SRT도 직접 훑어보며** 고유명사/오인식을 먼저 고치면 번역 품질이 크게 올라갑니다.

### 2) 번역 초안
```bash
python3 -m subpipe translate talk.en.srt --to ko --from en \
    --tone 존댓말 --domain 예능 --glossary glossary.txt --context 2 --batch-size 15 --cps 14
```
- `--tone formal|casual` (존댓말|반말), `--domain general|variety|drama|it|medical` (예능, 드라마, IT, 의학도 가능)
- 용어집(`glossary.example.txt` 참고): 원문에 등장하는 용어만 프롬프트에 넣어 강제 번역하게 합니다.
- 앞뒤 `--context` 줄은 참고용으로만 전달되고 번역 대상이 아닙니다.
- 번호가 붙은 JSON 배치로 요청하고, 깨진 출력(코드펜스, 잘린 JSON, 번호 목록 등)도 최대한 복구합니다.
  누락된 줄은 한 줄씩 `--retries` 만큼 재시도합니다. 끝내 실패한 줄은 빈 칸으로 남고 `미번역`으로 표시됩니다.
- 줄 수와 타임코드는 원문과 항상 1:1입니다. 읽기 속도(CPS) 초과는 **자르지 않고 플래그**만 표시합니다.
  `--condense`를 주면 초과 줄을 LLM으로 한 번 더 줄여 봅니다(그래도 넘으면 플래그 유지).
- 번역 메모리(`translation_memory.json`, `--tm`/`--no-tm`)가 같은 문장을 캐시하고, 서버가 죽어도
  배치마다 저장됩니다. LLM 서버가 꺼져 있으면 오류 메시지와 함께 종료(코드 2)하며,
  **같은 명령을 다시 실행하면 이어서 진행**됩니다.

출력: `talk.en.ko.draft.srt`, `talk.en.ko.review.json`, `talk.en.ko.review.html`.
(출력 위치는 `-o 폴더`)

### 3) 검수 (HTML)
`*.review.html`을 브라우저로 엽니다 (인터넷 불필요, 단일 파일).
- 타임코드 | 원문 | 초안(바로 수정 가능한 textarea) | 검수 체크
- 플래그: `읽기속도`(CPS 초과), `줄길이`, `미번역`, `숫자`(원문 숫자가 초안에 없음),
  `고유명사?`(원문 고유명사가 초안에 그대로 없음 - 음역이면 무시해도 됨), `용어집` 위반.
  수정하면 플래그가 실시간으로 갱신됩니다.
- `플래그 있는 줄만` 필터, Alt+↓/↑ 로 줄 이동, 편집 내용은 브라우저에 자동 저장됩니다.
- 끝나면 **편집본 JSON 내려받기** (또는 SRT 내려받기).

플래그 기준을 바꾸어 다시 만들기: `python3 -m subpipe review x.review.json --cps 13 --max-chars 20`
또는 이미 가진 SRT 두 개로: `review --src a.en.srt --draft a.ko.srt`.

### 4) 최종 SRT
```bash
python3 -m subpipe apply-review talk.en.ko.edited.json --src talk.en.srt -o talk.ko.srt
```
`--src`를 주면 줄 수/타임코드 1:1을 검증합니다. 빈 번역이 있으면 거부합니다(`--allow-empty`로 해제). `--vtt`로 VTT 출력.

### 5) QA
```bash
python3 -m subpipe qa talk.ko.srt --cps 14 --max-chars 22
```
겹침, 번호, 빈 자막, 너무 짧은/긴 표시 시간, CPS, 줄 길이, 줄 수를 점검합니다. 오류가 있으면 종료 코드 1
(`--strict`는 경고도 실패). 영어 SRT 점검은 `--max-chars 42`.

### 6) 견적
```bash
python3 -m subpipe price talk.mp4                 # 3개 요금제 표
python3 -m subpipe price talk.srt --rate 5000     # 단가 직접 지정
python3 -m subpipe price talk.mp4 --tier specialist --round half
python3 -m subpipe price x --minutes 12.5 --tiers basic=4500,entertainment=7000,specialist=11000
```
기본 단가: AI번역+감수 5,000원/분, 예능·드라마 7,000원/분, 의학·IT 10,000원/분. 과금 단위는 기본 올림(분), `--round half|none`.
SRT를 넣으면 마지막 자막 종료 시각을 길이로 봅니다(영상 전체 길이보다 짧을 수 있음).

## 검수 팁
1. 먼저 `플래그 있는 줄만` 으로 위험한 줄부터 처리하고, 그 다음 전체를 한 번 읽으며 어색한 번역을 고칩니다.
2. `숫자`/`용어집` 플래그는 오번역일 확률이 높으니 반드시 확인합니다.
3. CPS 초과 줄은 의역해서 줄이거나, 앞뒤 자막과의 타이밍은 별도 편집기로 조정하세요(이 도구는 타임코드를 바꾸지 않습니다).
4. 반복되는 오번역은 용어집에 추가하고 다시 `translate` 하면 TM 캐시 키가 바뀌어 새로 번역됩니다.
5. 납품 전 `qa`를 반드시 돌리고, 영상과 함께 실제로 한 번 재생해 보세요.

## 테스트
```bash
python3 -m unittest discover -s tests -v
```
가짜 LLM/가짜 Whisper를 쓰므로 네트워크, GPU 없이 실행됩니다.

## 라이선스 / 윤리 주의
- 의뢰인이 **권리를 가진 콘텐츠**만 작업하세요. 저작권이 있는 영상의 자막을 무단으로 배포하지 않습니다.
- 포트폴리오 샘플은 CC 라이선스(예: CC BY) 영상으로 만들고, 출처와 라이선스 조건(저작자 표시 등)을 지키세요.
- AI 번역 초안을 사용한다는 점과 사람이 감수한다는 점을 의뢰인에게 투명하게 안내하세요.
- 다른 사람의 목소리를 복제(보이스 클로닝)하는 용도로 쓰지 않습니다.
- 고객 파일은 로컬(내 PC)에서만 처리되며 외부 서버로 전송되지 않습니다(LLM 주소를 원격으로 바꾸지 않는 한).
