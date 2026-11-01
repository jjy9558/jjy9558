# hpatch - 인디게임 AI 한글패치 도구 (개인용)

한국어를 지원하지 않는 인디게임의 텍스트를, **내 PC에서 돌아가는 로컬 LLM**(llama-server 등 OpenAI 호환 서버)으로
한국어로 번역해 **내가 설치한 게임 사본**에 적용하는 개인용 도구입니다. 표준 라이브러리만 사용하며(Python 3.9+),
외부 서버로는 아무것도 전송하지 않습니다(기본 엔드포인트는 `http://127.0.0.1:8080/v1`).

> v0.1 지원 엔진: **RPG Maker MV / MZ**, **Ren'Py** (Unity / Unreal / Godot 은 아직 미지원, 아래 로드맵 참고)

## 법적 고지 / 개인 사용 원칙 (꼭 읽어 주세요)

- 이 도구는 **본인이 정당하게 구입/설치한 게임 사본을 개인적으로 읽기 위한 용도**입니다.
- **게임 파일과 번역된 에셋(패치된 `data/*.json`, `tl/korean/*.rpy`, 폰트 포함 폴더, `.hpatch_backup/` 등)을 재배포하지 마세요.**
  업로드, 공유 폴더, 패치 배포, 번역본이 들어간 게임 통째 공유 모두 포함입니다.
- 각 게임의 **EULA/이용약관**과 개발자의 번역·수정 관련 방침을 확인하고 지키세요. 번역/수정을 금지하는 게임에는 쓰지 마세요.
- 공유가 꼭 필요하다면 **본인이 만든 번역 메모리(`tm.sqlite`) / 용어집 / 검수 CSV 정도로 한정**하는 것을 권합니다.
  그마저도 원문 대사가 그대로 들어 있으므로 저작권자가 허용하는 범위인지 먼저 확인하세요. 개발자가 공식 번역 기여를 받는다면 그쪽으로 보내는 편이 가장 좋습니다.
- 사용 폰트의 라이선스(재배포 가능 여부)도 각자 확인하세요. 이 도구는 폰트를 포함하지 않습니다.
- 무보증입니다. 적용 전에 항상 `.hpatch_backup/` 에 원본을 백업하지만, 세이브/플러그인 호환성은 보장하지 않습니다.

## 빠른 시작

```
cd hangul-patcher
python -m hpatch scan      "D:\Games\MyGame"            # 엔진 감지 + 문자열 개수 (아무것도 쓰지 않음)
python -m hpatch translate "D:\Games\MyGame" --dry-run  # LLM 호출 없이 번역 대상/배치 수 확인
python -m hpatch translate "D:\Games\MyGame" --limit 30 # 먼저 30개만 시험 번역
python -m hpatch export-csv "D:\Games\MyGame"           # 결과 검수 (.hpatch/review.csv)
python -m hpatch translate "D:\Games\MyGame"            # 나머지 전부 (중단해도 이어서 가능)
python -m hpatch apply     "D:\Games\MyGame" --font "C:\Fonts\NanumGothic.ttf"
python -m hpatch restore   "D:\Games\MyGame"            # 언제든 원본으로 되돌리기
```

사전 준비: llama-server 를 띄워 둡니다. 예) `llama-server -m your-model.gguf -c 8192 --port 8080`
(모델은 일본어/영어 -> 한국어 번역을 잘하는 것을 고르세요. 모델 이름은 `--model` 로 넘기지만 llama-server 는 보통 무시합니다.)

## 명령어

| 명령 | 설명 |
|---|---|
| `scan <game_dir>` | 엔진 감지, 파일/문자열 수, 번역 메모리 진행률. 파일을 쓰지 않습니다. |
| `extract <game_dir>` | 번역 대상을 `.hpatch/units.json` 에 저장(확인용). |
| `translate <game_dir> [--limit N] [--dry-run]` | 번역 메모리에 없는 문자열만 LLM 으로 번역. 실패/중단해도 진행 상황 유지. |
| `apply <game_dir> [--font F] [--locale-ko] ...` | 번역을 게임에 기록. 쓰는 파일마다 원본을 `.hpatch_backup/` 에 먼저 백업. |
| `restore <game_dir> [--force]` | 백업에서 원본 복원(새로 만든 파일은 삭제). 패치 후 바뀐 파일은 `--force` 없이는 건드리지 않음. |
| `export-csv` / `import-csv` | 엑셀(UTF-8 BOM)로 검수/수정. 가져온 번역은 `manual` 로 저장되어 LLM 이 덮어쓰지 않음. |

공통 옵션: `--engine {auto,rpgmaker,renpy}`, `--work-dir`(기본 `<game_dir>/.hpatch`), `--tm`(번역 메모리 경로), `--glossary`(용어집 경로).
`translate` 옵션: `--endpoint`(또는 환경변수 `HPATCH_ENDPOINT`), `--model`, `--api-key`, `--temperature`, `--batch-size`(기본 8),
`--max-chars`, `--retries`, `--source-lang`, `--style`, `--show-prompt`(dry-run 시 첫 프롬프트 출력).
종료 코드: 0 정상 / 1 오류 / 3 LLM 서버 연결 불가(진행 상황은 저장됨, 서버를 다시 켜고 같은 명령 재실행).

## 번역 방식

1. **마스킹**: 제어코드/변수/태그를 `<0>`, `<1>` 플레이스홀더로 바꿔 LLM 에 보내고 번역 후 복원합니다.
   플레이스홀더가 하나라도 사라지거나 늘어나면(순서 변경은 허용) 해당 줄만 개별 재시도하고, 그래도 안 되면
   **번역 메모리에 저장하지 않아 원문이 그대로 남습니다**(다음 실행 때 다시 시도).
2. **배치**: 여러 줄을 JSON(`[{"id":1,"text":...}]`)으로 한 번에 보내고 응답을 관대하게 파싱합니다
   (코드펜스, `<think>`, 잘린 JSON, 번호 목록 등). 누락/불일치 줄은 개별 재요청합니다.
3. **문맥**: 대사에는 화자 이름(이미 번역된 이름이 있으면 그 번역)과 직전 대사를 함께 보냅니다.
4. **번역 메모리**(sqlite, 원문 해시 키): 같은 문자열은 한 번만 번역하고, 게임이 업데이트되어도 새 문자열만 번역합니다.
   게임이 이미 패치된 상태여도 추출은 항상 `.hpatch_backup/` 의 **원본**에서 읽습니다.
   게임 업데이트로 파일이 새 원본으로 덮어써지면 감지해서 다음 `apply` 때 백업도 새로 만듭니다.
5. 이미 한글이 대부분이거나 글자가 없는 문자열(숫자, 기호, 제어코드뿐)은 건너뜁니다.

## 용어집 (glossary) 팁

작업 폴더의 `glossary.json`(또는 `--glossary`)을 두면 프롬프트에 주입됩니다. 예: [`examples/glossary.example.json`](examples/glossary.example.json)

```json
{
  "style": "주인공은 반말, 상인은 존댓말",
  "terms": {"Alice": "앨리스", "Potion": "포션"},
  "keep": ["HP", "MP"],
  "mask_patterns": ["<[A-Za-z]+:[^>]*>"]
}
```

- `terms`: 강제 번역. 문자열 **전체**가 용어와 같으면 LLM 호출 없이 바로 확정하고, 문장 속에 들어 있으면 해당 배치 프롬프트에만 규칙으로 들어갑니다.
- `keep`: 번역하지 않고 그대로 둘 단어(HP, 고유 약어 등).
- `mask_patterns`: 플러그인 전용 태그 등 **추가로 보호할 정규식**.
- 텍스트 형식도 가능: 한 줄에 `원문<TAB>번역` 또는 `원문 = 번역`, 번역이 없으면 keep, `#` 은 주석.
- 처음에는 `--limit 30` 으로 시험 번역 -> `export-csv` 로 확인 -> 인물/아이템/스킬 이름을 용어집에 넣고 본 번역을 돌리는 순서가 좋습니다.
- 용어집을 나중에 바꿔도 이미 번역 메모리에 있는 문장은 다시 번역되지 않습니다. 바꾸고 싶으면 CSV 에서 고쳐 `import-csv` 하세요.
- 번역 메모리를 전부 지우고 새로 하려면 `.hpatch/tm.sqlite` 를 삭제합니다.

## RPG Maker MV / MZ

- 감지: `www/data/System.json`(MV 배포본) 또는 `data/System.json`(MZ, 프로젝트 폴더). `js/rmmz_core.js` 유무로 MV/MZ 구분.
- 대상: `Map*.json`(표시 이름, 이벤트 명령), `CommonEvents.json`, `Troops.json`(전투 이벤트),
  `Actors/Classes/Items/Weapons/Armors/Skills/States/Enemies.json`(name, description, message1~4, nickname, profile),
  `System.json`(gameTitle, 화폐 단위, 속성/스킬/무기/방어구/장비 타입, terms 전체).
- 이벤트 명령: 대사 `401`(연속된 줄은 한 덩어리로 번역하고 줄 수가 달라지면 명령을 늘이거나 줄여 맞춤),
  `102` 선택지, `402` 선택지 분기, `405` 스크롤 텍스트, MZ 의 `101` 화자 이름(`parameters[4]`).
  스크립트(355/655), 주석(108/408), 변수/스위치 이름, 맵/이벤트 에디터용 이름은 건드리지 않습니다.
- 제어코드 `\V[n] \N[n] \C[n] \I[n] \{ \} \. \| \! \> \< \^ \\ \G \$ \FS[n]`, `%1` 등은 모두 보호됩니다.
- 출력 JSON 은 값만 바뀌고(구조/숫자/기타 필드 그대로) 한 줄 압축 형식으로 저장됩니다. UTF-8 BOM 은 유지합니다.

### 폰트 (한글이 네모로 보일 때)

RPG Maker 기본 폰트(mplus 등)에는 한글이 없어 한글이 비거나 네모로 보일 수 있습니다. 방법은 두 가지입니다.

1. **폰트 주입**: 한글을 포함한 `.ttf/.otf/.woff` 를 직접 준비해(라이선스 확인) 지정합니다.
   ```
   python -m hpatch apply "D:\Games\MyGame" --font "C:\Fonts\NanumGothic.ttf"
   ```
   - **MV**: 폰트를 `www/fonts/` 에 복사하고 `www/fonts/gamefont.css` 의 `GameFont` 정의가 그 파일을 가리키도록 고칩니다.
   - **MZ**: 폰트를 `fonts/` 에 복사하고 `data/System.json` 의 `advanced.mainFontFilename` 을 교체합니다(`numberFontFilename` 은 그대로).
   - 모든 변경은 백업/복원 대상이며 `restore` 하면 복사한 폰트 파일도 삭제됩니다.
   - 영문/숫자 글리프도 있는 폰트를 쓰세요(한글 전용 폰트면 영문이 깨집니다).
2. **`--locale-ko`**: `System.json` 의 `locale` 을 `ko_KR` 로 바꿉니다. 기본 스크립트가 시스템 폰트(Dotum 등)를 쓰게 되지만
   PC 에 해당 폰트가 있어야 하고 일부 플러그인은 이를 무시합니다. (폰트 주입과 함께 써도 됩니다.)

한글은 일본어보다 글자 폭이 달라 창 밖으로 넘칠 수 있습니다. 줄바꿈 자동 처리 플러그인(WordWrap 계열)을 직접 설치하는 것을 권장합니다(이 도구는 자동 줄바꿈을 하지 않음).

## Ren'Py

- `game/*.rpy` 소스를 파싱(논리 줄 토크나이저: 주석, 문자열, 괄호 이어짐 처리)해서 `game/tl/korean/` 번역 세트를 **새로 만들고**
  원본 `.rpy` 는 수정하지 않습니다. 같은 이름의 파일이 이미 있으면 백업 후 교체합니다.
- 대상: `say` 대사(화자/속성/`with`/`nointeract` 포함), `menu` 선택지, `define x = Character("이름")` 의 이름.
  `python`, `init`, `screen`, `transform` 블록 안의 문자열은 건드리지 않습니다.
- 대사는 `translate korean <id>:` 블록, 선택지/이름은 `translate korean strings:` (`old`/`new`, 전체 파일에서 중복 제거) 로 생성합니다.
- `[변수]`, `{태그}`, `%(name)s`, `%%`, `[[`, `{{`, 이스케이프(`\"`, `\n` 등)는 마스킹되고, 번역문에 맨몸의 `[`/`{`/`\` 가 있거나
  따옴표가 깨지면 거부/정리합니다.
- **언어 전환**: `tl/korean/_lang.rpy` 가 함께 생성됩니다. 기본은 `config.default_language = "korean"` 으로 처음부터 한국어로 시작합니다
  (`--no-default-lang` 으로 끔). 설정 화면에서 바꾸려면 `Language("korean")` 액션을 쓰거나 개발자 콘솔에서 `renpy.change_language("korean")`.
- **폰트**: Ren'Py 기본 폰트도 한글이 없습니다. `apply --font C:\...\NanumGothic.ttf` 로 `.ttf/.otf` 를 `game/tl/korean/` 에 복사하고
  `_lang.rpy` 에 `translate korean python:` 로 `gui.text_font` 등을 바꾸는 코드가 들어갑니다.
- **`.rpyc` / `.rpa` 만 있는 게임**: 소스(`.rpy`)가 없으면 이 도구는 아무것도 하지 않고 안내만 출력합니다(`scan` 이 종료 코드 1).
  언패커는 포함하지 않습니다. 직접 구한 외부 도구(예: `unrpa` 로 `.rpa` 풀기, `unrpyc` 로 `.rpyc` -> `.rpy` 복원)로 소스를 얻은 뒤 다시 실행하세요.
  이 과정이 해당 게임의 약관에 어긋나지 않는지는 사용자가 확인해야 합니다.
- **번역이 적용되지 않을 때**: 대사 블록 id(`label이름_해시8자리`)는 Ren'Py 의 생성 규칙을 재현한 것이라 버전/게임에 따라 어긋날 수 있습니다
  (id 가 안 맞으면 해당 블록은 단순히 무시됩니다). 선택지/이름(`strings`)은 id 와 무관하게 동작합니다.
  Ren'Py 런처의 "Generate Translations" 로 만든 공식 뼈대와 id 를 비교해 보고 이슈로 알려 주세요. 기존 `tl/korean` 에 공식 생성 파일이 있다면
  id 중복 오류가 날 수 있으니 한쪽을 지우세요.
- 화면(screen)의 `text`/`textbutton`, `_("...")` 등 UI 문자열은 v0.1 범위 밖입니다.

## 검수 / 수정

`export-csv` -> 엑셀에서 `translation` 열 수정 -> `import-csv`. 제어코드/변수가 원문과 다르게 바뀐 행은 경고 후 건너뜁니다.
수정한 항목은 `manual` 로 기록되어 이후 `translate` 가 덮어쓰지 않습니다.

## 한계

- **미지원 엔진**: Unity, Unreal, Godot 등은 아직 지원하지 않습니다. 암호화/패킹된 게임(Enigma Virtual Box 등), 데이터를 `.rpgmvp` 등으로
  암호화한 RPG Maker 자산, 플러그인에 하드코딩된 텍스트(`js/plugins/*.js`, `plugins.js` 파라미터), 이미지 속 글자는 번역하지 못합니다.
- RPG Maker: 액터 이름 변경(320)/닉네임(324)/프로필(325) 이벤트 명령, 플러그인 명령 인자, 맵 노트태그의 텍스트는 추출하지 않습니다.
  아이템/스킬 이름을 노트태그나 스크립트에서 이름으로 비교하는 게임은 번역 후 오동작할 수 있으니 백업 후 확인하세요.
- 번역 품질은 사용 모델에 따라 다릅니다. 조사(은/는, 이/가)가 `\N[1]`, `[name]` 같은 변수 뒤에서 어색할 수 있습니다.
- 한글 줄 길이 자동 조정 없음. 세이브 데이터에 이미 저장된 문자열(예: 액터 이름)은 바뀌지 않을 수 있습니다.
- 대사 글자 수/창 크기 제한은 검사하지 않습니다.

## 로드맵

- Unity: [XUnity.AutoTranslator](https://github.com/bbepis/XUnity.AutoTranslator) 연동 - 이 도구의 로컬 LLM 엔드포인트(OpenAI 호환)를
  가리키는 설정/글로서리 내보내기, 번역 캐시 가져오기
- Ren'Py: 공식 생성 뼈대(`translate korean <id>:` 빈 블록)를 읽어 채우는 모드, screen UI 문자열
- RPG Maker: 플러그인 파라미터, 320/324/325 명령, 자동 줄바꿈 옵션

## 개발 / 테스트

실제 게임 콘텐츠 없이 합성 픽스처만 사용합니다. 네트워크가 필요 없습니다(로컬 루프백 HTTP 서버만 사용).

```
cd hangul-patcher
python3 -m unittest discover -s tests -v
```

구성: `hpatch/masking.py`(제어코드 마스킹) · `translate.py`(프롬프트/파싱/배치) · `tm.py`(번역 메모리) · `glossary.py` ·
`backup.py`(백업/복원) · `engines/rpgmaker.py` · `engines/renpy.py` · `llm.py`(주입 가능한 클라이언트: `complete(messages) -> str`) · `cli.py`.
