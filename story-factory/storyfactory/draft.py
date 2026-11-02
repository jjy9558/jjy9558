"""대본 초안. LLM(로컬, 선택)이 있으면 사연체로, 없거나 실패하면 템플릿으로 만든다.

LLM 에는 가명화가 끝난 카드 내용만 보낸다. LLM 출력도 다시 민감 주제/실명 잔존/약속 일치를 확인하고,
통과하지 못하면 템플릿으로 되돌린다. 사용한 AI 요소는 meta 의 ``ai_used`` 에 기록한다.
"""
from __future__ import annotations

import json
import random
import re

from ._jjkit import llm as jllm
from .model import Card, Draft
from .seeds import won_ko

MAX_TITLE = 40


def _f(card: Card, extra: dict | None = None) -> dict:
    d = dict(card.facts)
    d["subject"] = card.subject
    if "price" in d:
        d["price_ko"] = won_ko(d["price"])
    if "total" in d:
        d["total_ko"] = won_ko(d["total"])
    if extra:
        d.update(extra)
    return d


# kind -> dict(titles, thumbs, claims, hooks[], body[], endings[])  (str.format 필드는 facts 키)
TEMPLATES: dict[str, dict] = {
    "dish_streak": {
        "titles": ["설거지 {days}일 방치한 사람의 결말", "싱크대에 그릇 {days}일째 쌓아 둔 사람", "설거지 {days}일 미룬 기록을 공개합니다"],
        "thumbs": ["설거지 {days}일째", "{days}일 방치"],
        "claims": ["설거지 {days}일 방치"],
        "hooks": ["설거지를 {days}일째 안 한 사람이 있습니다. 제 이야기입니다.",
                  "싱크대에 그릇을 {days}일 동안 쌓아 둔 사람의 기록입니다."],
        "body": ["마지막으로 설거지를 한 날로부터 정확히 {days}일이 지났습니다.",
                 "생활 기록 앱이 매일 저를 불렀지만 저는 매번 내일 하겠다고 답했습니다.",
                 "그릇은 하루에 한 개씩 늘어난 게 아니라 마음의 짐처럼 한꺼번에 늘어났습니다.",
                 "미룰수록 설거지는 설거지가 아니라 발굴 작업이 됩니다."],
        "endings": ["결말부터 말하면, 오늘 밤 설거지를 합니다. 이 영상을 올릴 수 있다는 건 그 뒤라는 뜻이겠죠.",
                    "결말은 평범합니다. 결국 제가 합니다. 그러니 여러분은 {days}일까지 가지 마세요."],
    },
    "sigh_week": {
        "titles": ["일주일에 한숨 {count}번 쉰 사람의 기록", "한숨 {count}번, 제일 많이 쉰 시간은 {hour}", "{weekday} {hour}에 한숨이 몰렸다"],
        "thumbs": ["한숨 {count}번", "{weekday} {hour}"],
        "claims": ["한숨 {count}번"],
        "hooks": ["이번 주에 한숨을 {count}번 쉬었습니다. 세어 보니까요.",
                  "한숨 횟수를 일주일 동안 직접 기록해 봤습니다."],
        "body": ["총 {count}번, 그중 가장 몰린 때는 {weekday} {hour}였습니다.",
                 "기록 앱은 한숨을 쉬면 버튼을 누르게 합니다. 처음엔 웃겼는데 숫자가 쌓이니 웃음이 줄었습니다.",
                 "한숨은 의외로 정직합니다. 하루 중 언제 제가 지치는지 숫자가 먼저 알려 줬습니다."],
        "endings": ["결말은 간단합니다. {weekday} {hour}에는 일정을 비우기로 했습니다.",
                    "그래서 다음 주에는 {hour} 전에 쉬는 시간을 먼저 넣어 두려고 합니다."],
    },
    "pushup_month": {
        "titles": ["한 달 동안 푸시업 {count}개 한 기록", "푸시업 {count}개를 센 사람의 한 달"],
        "thumbs": ["푸시업 {count}개", "한 달 {count}개"],
        "claims": ["푸시업 {count}개"],
        "hooks": ["한 달 동안 푸시업을 {count}개 했습니다. 전부 기록으로 남아 있습니다."],
        "body": ["하루에 몰아서 한 날도, 아예 건너뛴 날도 있었습니다.",
                 "그런데 숫자를 합쳐 보니 {count}개였습니다. 기억 속의 저보다 기록 속의 제가 더 성실했습니다."],
        "endings": ["결말은 소박합니다. 다음 달 목표는 지금보다 조금만 더입니다."],
    },
    "graveyard_count": {
        "titles": ["못 지킨 할 일 {count}개를 묻어 준 사람", "할 일 묘지에 {count}개가 잠들었습니다"],
        "thumbs": ["묘지 {count}기", "할 일 {count}개 사망"],
        "claims": ["할 일 {count}개 묘지"],
        "hooks": ["제 할 일 앱에는 묘지가 있습니다. 지금 {count}개가 잠들어 있습니다."],
        "body": ["이 할 일들은 총 {skipped_total}번 미뤄진 끝에 묻혔습니다.",
                 "하나하나가 한때는 꼭 하겠다고 적어 둔 일이었습니다.",
                 "묘지를 열어 보니 공통점이 있었습니다. 시작이 너무 거창했습니다."],
        "endings": ["결말은 이렇습니다. 오늘 하나만 꺼내서 5분만 해 보기로 했습니다."],
    },
    "graveyard_tomb": {
        "titles": ["{skipped}번 미루다 묻힌 할 일의 묘비명", "미루기 {skipped}번, 묘지에 간 할 일 이야기"],
        "thumbs": ["{skipped}번 미룸", "묘비명 공개"],
        "claims": ["{skipped}번 미룸"],
        "hooks": ["{skipped}번 미루다 결국 묘지로 간 할 일이 있습니다."],
        "body": ["그 할 일의 이름은 \"{subject}\"였습니다.",
                 "만들어진 지 {age_days}일, 그동안 {skipped}번 눈앞에 나타났고 {skipped}번 외면당했습니다.",
                 "할 일 앱은 조용히 묘비에 이렇게 적었습니다. 여기 잠들다."],
        "endings": ["결말은 슬픕니다. 그래도 묘지에서 다시 꺼낼 수 있다는 걸 오늘 알았습니다."],
    },
    "declared_quit": {
        "titles": ["선언했다가 포기한 지 {days}일째", "큰소리쳤던 일을 접은 지 {days}일"],
        "thumbs": ["선언 {days}일 전", "포기 선언"],
        "claims": ["{days}일 전 선언"],
        "hooks": ["{days}일 전에 저는 큰소리쳤습니다. 그 일은 지금 묘지에 있습니다."],
        "body": ["일기에 이렇게 적어 뒀습니다. \"{subject}\"",
                 "녹음까지 남겼으니 부인할 수도 없습니다.",
                 "음성 일기는 기억보다 훨씬 정직합니다. {days}일 뒤의 저를 가장 잘 아는 건 그날의 저였습니다."],
        "endings": ["결말은 아직 없습니다. 다만 이번엔 선언 말고 5분짜리 시작부터 하려고 합니다."],
    },
    "regret_item": {
        "titles": ["{price_ko} 주고 산 걸 후회한 이야기", "{price_ko}짜리 후회, 만족도 {satisfaction}점"],
        "thumbs": ["{price_ko} 후회", "만족도 {satisfaction}점"],
        "claims": ["{price_ko} 후회"],
        "hooks": ["{price_ko}을 주고 산 물건이 있습니다. 결과부터 말하면 후회합니다."],
        "body": ["이 물건은 \"{subject}\"입니다. 산 지 {days_ago}일이 지났습니다.",
                 "구매 뒤 만족도를 물었더니 저는 5점 만점에 {satisfaction}점이라고 답했습니다.",
                 "냉각 기간에 한 번 더 고민했다면 달랐을까요. 기록은 말없이 가격만 보여 줍니다."],
        "endings": ["결말은 교훈입니다. 다음엔 사기 전에 기록부터 합니다."],
    },
    "regret_total": {
        "titles": ["후회한 소비 {count}건, 합치면 {total_ko}", "{total_ko}어치 후회를 모아 봤습니다"],
        "thumbs": ["후회 {total_ko}", "후회 {count}건"],
        "claims": ["후회 {count}건"],
        "hooks": ["후회한 소비를 모아 봤습니다. 합계는 {total_ko}입니다."],
        "body": ["사고 나서 만족도가 낮았거나 거의 안 쓴 물건이 {count}건이었습니다.",
                 "하나씩 보면 작은데 합치니 {total_ko}이 됩니다.",
                 "무서운 건 금액이 아니라, 살 때는 전부 필요하다고 믿었다는 점입니다."],
        "endings": ["결말은 간단합니다. 이제 사고 싶은 건 일단 담아 두고 기다립니다."],
    },
    "saved_total": {
        "titles": ["참은 소비 {count}건, 아낀 돈 {total_ko}", "사고 싶던 {count}개를 참았더니 {total_ko}"],
        "thumbs": ["{total_ko} 아낌", "{count}번 참음"],
        "claims": ["{total_ko} 아낌"],
        "hooks": ["사고 싶은 걸 {count}번 참았습니다. 아낀 돈은 {total_ko}입니다."],
        "body": ["담아 두고 며칠 기다렸더니 마음이 바뀐 물건들입니다.",
                 "그 {count}건을 다 샀다면 {total_ko}이 나갔을 겁니다.",
                 "참는다고 늘 행복한 건 아니었습니다. 그래도 후회는 없었습니다."],
        "endings": ["결말은 통장에 있습니다. 숫자가 조금 덜 줄어 있습니다."],
    },
    "streak": {
        "titles": ["단톡방에서 {days}일 연속 말한 기록", "{days}일 연속, 단톡방이 멈추지 않았다"],
        "thumbs": ["{days}일 연속", "단톡 {days}일"],
        "claims": ["{days}일 연속 대화"],
        "hooks": ["우리 단톡방은 {days}일 연속으로 대화가 이어졌습니다. 익명 통계로 확인했습니다."],
        "body": ["누가 말했는지는 공개하지 않습니다. 숫자만 봅니다.",
                 "하루도 빠지지 않고 {days}일, 할 말이 그렇게 많았을까요.",
                 "아무 일 없는 날에도 누군가 한 줄은 남겼다는 뜻입니다."],
        "endings": ["결말은 따뜻합니다. 이런 방이 있다는 게 통계로도 보입니다."],
    },
    "peak_hour": {
        "titles": ["단톡방이 제일 시끄러운 시간은 {hour}", "{hour}에 메시지 {count}개가 몰린 방"],
        "thumbs": ["{hour}의 단톡", "메시지 {count}개"],
        "claims": ["{hour} 최다"],
        "hooks": ["단톡방에서 가장 말이 많은 시간은 {hour}였습니다."],
        "body": ["그 시간대에만 메시지가 {count}개 쌓였습니다.",
                 "내용은 공개하지 않습니다. 시간대 숫자만 봤습니다.",
                 "다들 그 시간에는 무슨 얘기를 그렇게 했을까요."],
        "endings": ["결말은 모릅니다. 다만 그 시간만 되면 알림이 울린다는 건 확실합니다."],
    },
    "busiest_day": {
        "titles": ["{month}월 {day}일, 단톡이 터진 날", "하루에 메시지 {count}개, 그날 무슨 일이"],
        "thumbs": ["{month}월 {day}일", "메시지 {count}개"],
        "claims": ["하루 메시지 {count}개"],
        "hooks": ["{month}월 {day}일, 단톡방에 하루 동안 메시지가 {count}개 올라왔습니다."],
        "body": ["평소의 몇 배인지는 따지지 않겠습니다. 그날은 그냥 터진 날이었습니다.",
                 "누가 무슨 말을 했는지는 공개하지 않고 숫자만 남깁니다."],
        "endings": ["결말은 모두의 기억에만 있습니다. 통계는 입이 무겁습니다."],
    },
    "volume": {
        "titles": ["올해 단톡방 메시지 {messages}개, 몇 명이 썼을까", "{messages}개의 메시지가 남긴 한 해"],
        "thumbs": ["메시지 {messages}개", "한 해 결산"],
        "claims": ["메시지 {messages}개"],
        "hooks": ["올해 우리 단톡방에는 메시지가 {messages}개 쌓였습니다."],
        "body": ["참여한 사람은 {members}명, 대화가 있었던 날은 {active_days}일입니다.",
                 "익명 통계라서 누가 얼마나 말했는지는 비밀입니다.",
                 "숫자만 봐도 올해가 어땠는지 조금은 보입니다."],
        "endings": ["결말은 내년에 다시 세어 보는 겁니다."],
    },
    "journal_entry": {
        "titles": ["{day} 일기장에 남아 있던 한 줄", "일기장을 열었더니 이런 말이 있었다"],
        "thumbs": ["{day}의 일기", "일기장 공개"],
        "claims": ["{day} 일기"],
        "hooks": ["{day}의 일기를 다시 읽어 봤습니다."],
        "body": ["그날의 저는 이렇게 말했습니다. \"{subject}\"",
                 "시간이 지나서 읽으니 같은 문장이 다르게 들렸습니다."],
        "endings": ["결말은 없습니다. 다만 오늘의 저는 이 문장에 답장을 쓰기로 했습니다."],
    },
}

DEFAULT_AI_USED = {"voice": True, "image": True}


def _fmt(s: str, f: dict) -> str:
    try:
        return s.format(**f)
    except (KeyError, IndexError, ValueError):
        return s


def template_draft(card: Card, seed: int = 0) -> Draft:
    """LLM 없이 완성되는 템플릿 대본. (seed, card.id) 가 같으면 항상 같다."""
    t = TEMPLATES.get(card.kind)
    if t is None:
        raise ValueError("템플릿이 없는 소재 종류: %s" % card.kind)
    rng = random.Random("%s:%s" % (seed, card.id))
    f = _f(card)
    titles = [_fmt(x, f) for x in t["titles"]]
    titles = [x for x in titles if len(x) <= MAX_TITLE] or [titles[0][:MAX_TITLE]]
    head = rng.randrange(len(titles))
    titles = titles[head:] + titles[:head]
    lines = [_fmt(rng.choice(t["hooks"]), f)] + [_fmt(x, f) for x in t["body"]] + [_fmt(rng.choice(t["endings"]), f)]
    thumb = _fmt(rng.choice(t["thumbs"]), f)
    claims = [_fmt(x, f) for x in t["claims"]]
    return Draft(card.id, titles[0], titles, lines, thumb, claims, ai_used={"voice": True, "image": True, "script": False},
                 kind=card.kind, source=card.source)


# ------------------------------------------------------------------ LLM
SYSTEM = ("너는 한국어 유튜브 쇼츠 사연 대본 작가다. 주어진 '사실'만으로 40~55초 분량(공백 제외 약 250자) 1인칭 사연체 대본을 쓴다.\n"
          "규칙: 사실에 없는 이름·장소·나이·병명·사건을 지어내지 말 것. 특정 인물을 비방하지 말 것. "
          "숫자는 사실에 있는 그대로만 쓰고, 제목에 쓴 숫자와 약속 표현은 대본에 반드시 나오게 할 것. "
          "'실화', '충격' 같은 과장 표현 금지. JSON 하나만 출력: "
          '{"title":"40자 이하","title_candidates":["제목 3개"],"thumbnail_text":"12자 이하","claims":["제목이 약속한 핵심 1~2개"],'
          '"script_lines":["한 줄 한 문장, 6~12줄"]}')


def _numbers_ok(title: str, lines: list[str]) -> bool:
    body = " ".join(lines)
    return all(n in body for n in re.findall(r"\d+", title))


def valid_llm_json(j) -> dict | None:
    if not isinstance(j, dict):
        return None
    title = j.get("title")
    lines = j.get("script_lines")
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > MAX_TITLE:
        return None
    if not isinstance(lines, list) or not 4 <= len(lines) <= 20 or not all(isinstance(x, str) and x.strip() for x in lines):
        return None
    cands = [c.strip() for c in (j.get("title_candidates") or []) if isinstance(c, str) and c.strip() and len(c.strip()) <= MAX_TITLE]
    thumb = j.get("thumbnail_text") if isinstance(j.get("thumbnail_text"), str) else ""
    claims = [c.strip() for c in (j.get("claims") or []) if isinstance(c, str) and c.strip()][:3]
    lines = [x.strip() for x in lines]
    title = title.strip()
    if title not in cands:
        cands.insert(0, title)
    if not all(_numbers_ok(c, lines) for c in cands):
        cands = [c for c in cands if _numbers_ok(c, lines)]
        if title not in cands:
            return None
    return {"title": title, "title_candidates": cands[:5], "thumbnail_text": thumb.strip()[:20],
            "claims": claims, "lines": lines}


def llm_draft(card: Card, client, seed: int = 0) -> Draft | None:
    """실패/무효면 None. 카드는 이미 가명화된 상태여야 한다."""
    f = _f(card)
    user = "소재 종류: %s\n사실(JSON): %s\n" % (card.kind, json.dumps(f, ensure_ascii=False, sort_keys=True))
    try:
        j = client.chat_json([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                             temperature=0.0, max_tokens=900, extra={"seed": int(seed)})
    except (jllm.LLMError, ValueError, TypeError):
        return None
    v = valid_llm_json(j)
    if v is None:
        return None
    base = template_draft(card, seed)
    return Draft(card.id, v["title"], v["title_candidates"], v["lines"], v["thumbnail_text"] or base.thumbnail_text,
                 v["claims"] or base.claims, ai_used={"voice": True, "image": True, "script": True},
                 used_llm=True, model=getattr(client, "model", ""), kind=card.kind, source=card.source)


def make_draft(card: Card, client=None, seed: int = 0, ai_voice: bool = True, ai_image: bool = True) -> Draft:
    d = llm_draft(card, client, seed) if client is not None else None
    if d is None:
        d = template_draft(card, seed)
    d.ai_used["voice"] = bool(ai_voice)
    d.ai_used["image"] = bool(ai_image)
    d.ai_used.setdefault("script", d.used_llm)
    return d
