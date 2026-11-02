"""meta.json의 ai_used로 AI 공개 문구 초안 생성."""
KEYS = (("voice", "AI 합성 음성(TTS)"), ("image", "AI로 생성한 이미지"), ("script", "AI의 도움을 받아 작성한 대본"))
OFFICIAL = "https://support.google.com/youtube/answer/14328491"


def used_flags(meta):
    ai = meta.get("ai_used")
    if not isinstance(ai, dict):
        raise ValueError("meta.json에 ai_used 객체가 없습니다 (예: {\"voice\": true, \"image\": true, \"script\": false})")
    return {k: bool(ai.get(k)) for k, _ in KEYS}


def _join(items):
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " 및 " + items[-1]


def draft(meta):
    f = used_flags(meta)
    labels = [lab for k, lab in KEYS if f[k]]
    lines = []
    if not labels:
        lines.append("이 영상에는 AI 합성 요소가 없다고 기록되어 있습니다 (ai_used 모두 false).")
        lines.append("합성 콘텐츠 표시는 필요 없을 수 있으나, 실제 제작 방식과 일치하는지 공식 문서에서 확인하세요.")
    else:
        lines.append("이 영상은 %s을(를) 사용해 제작되었습니다." % _join(labels))
        realistic = [lab for k, lab in KEYS if f[k] and k in ("voice", "image")]
        if realistic:
            lines.append("영상 속 %s은(는) 실제 사람·장소·사건이 아닌 합성 결과물일 수 있습니다." % _join(realistic))
        else:
            lines.append("대본 작성에만 AI를 보조로 활용했으며, 영상과 음성은 합성이 아닙니다.")
    guide = []
    if f["voice"] or f["image"]:
        guide.append("YouTube 스튜디오 업로드 화면의 '변경되거나 합성된 콘텐츠' 항목에 해당하는지 확인하고, "
                     "해당하면 '예'를 선택하세요.")
    else:
        guide.append("YouTube 스튜디오 업로드 화면의 '변경되거나 합성된 콘텐츠' 질문에 현재 구성이 해당하는지 확인하세요.")
    guide.append("정확한 기준은 정책이 바뀔 수 있으니 공식 문서 확인: %s" % OFFICIAL)
    if any(f.values()) and meta.get("ai_disclosure") is not True:
        guide.append("참고: meta.json의 ai_disclosure가 true가 아닙니다 (check에서 경고됨).")
    return {"flags": f, "text": "\n".join(lines), "guide": guide}


def format_draft(d):
    out = ["[설명란 공개 문구 초안]", d["text"], "", "[업로드 시 안내]"]
    out += ["- " + g for g in d["guide"]]
    return "\n".join(out)
