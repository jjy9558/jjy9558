"""재활용 분리 심판: 사용자 규칙 > 내장 규칙, 없으면 '모름'."""
from . import match

DISCLAIMER_RECYCLE = ("※ 분리배출 규칙은 지역(지자체)마다 다릅니다. 이 결과는 일반적인 안내일 뿐 공식 기준이 아니니 "
                      "거주지 지자체 안내를 꼭 확인하세요.")


def merged(user_rules: list[dict], builtin: list[dict]) -> list[dict]:
    return list(user_rules) + list(builtin)  # 앞쪽이 우선


def judge(rules: list[dict], text: str, user_names: set[str] | None = None) -> dict:
    r = match.find(rules, text)
    if r is None:
        return {"known": False, "item": text}
    mine = bool(user_names and match.norm(r["name"]) in user_names)
    return {"known": True, "item": text, "rule": r, "user": mine}


def render(res: dict) -> list[str]:
    if not res["known"]:
        return [f"품목: {res['item']}", "판정: 모름. 지자체 확인이 필요합니다.",
                "대사: 증거 불충분. 이 사건은 구청 환경과에 이첩합니다.",
                "(규칙을 알게 되면 `rules add`로 직접 추가할 수 있습니다.)", "", DISCLAIMER_RECYCLE]
    r = res["rule"]
    src = "내 규칙" if res["user"] else "내장 일반 안내"
    lines = [f"품목: {res['item']}  (매칭: {r['name']}, {src})", f"판정: {r['bin']}"]
    if r.get("why"):
        lines.append(f"이유: {r['why']}")
    if r.get("line"):
        lines.append(f"대사: {r['line']}")
    return lines + ["", DISCLAIMER_RECYCLE]
