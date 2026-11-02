"""BGM/이미지 출처 장부(csv): 이름, 라이선스, 출처 URL, AI 생성 여부."""
import csv
import os

FIELDS = ["name", "license", "source_url", "ai_generated"]
YES = {"yes", "y", "true", "1", "예", "네", "o"}
NO = {"no", "n", "false", "0", "아니오", "아니요", "x"}


def norm_ai(v):
    s = (v or "").strip().lower()
    if s in YES:
        return "yes"
    if s in NO:
        return "no"
    return ""


def load(path):
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [{k: (row.get(k) or "").strip() for k in FIELDS} for row in csv.DictReader(f)]


def save(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def add(path, name, license_, url, ai):
    name = (name or "").strip()
    if not name:
        raise ValueError("이름이 비어 있습니다")
    rows = load(path)
    row = {"name": name, "license": (license_ or "").strip(), "source_url": (url or "").strip(),
           "ai_generated": norm_ai(ai)}
    for i, r in enumerate(rows):
        if r["name"] == name:
            rows[i] = row
            save(path, rows)
            return "updated"
    rows.append(row)
    save(path, rows)
    return "added"


def check(rows):
    """-> [(level, message)]; 미기재 항목마다 경고. 이름순 정렬로 결정적."""
    out = []
    for i, r in enumerate(rows, 1):
        label = r["name"] or "(이름 없음 #%d)" % i
        miss = []
        if not r["name"]:
            miss.append("이름")
        if not r["license"]:
            miss.append("라이선스")
        if not r["source_url"]:
            miss.append("출처 URL")
        if r["ai_generated"] not in ("yes", "no"):
            miss.append("AI 생성 여부")
        if miss:
            out.append(("warn", "%s: %s 미기재" % (label, ", ".join(miss))))
    return out


def format_list(rows):
    if not rows:
        return "(장부가 비어 있습니다)"
    lines = []
    for r in rows:
        lines.append("%s | %s | %s | AI:%s" % (r["name"], r["license"] or "-", r["source_url"] or "-",
                                               r["ai_generated"] or "?"))
    return "\n".join(lines)
