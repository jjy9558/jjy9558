# -*- coding: utf-8 -*-
"""최소한의 RFC 5545 파서/검증기 (테스트용)."""
import re


def unescape(v):
    out, i = [], 0
    while i < len(v):
        c = v[i]
        if c == "\\" and i + 1 < len(v):
            n = v[i + 1]
            out.append("\n" if n in "nN" else n)
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def parse_ics(data):
    """bytes/str -> 컴포넌트 트리. 형식 위반이면 AssertionError."""
    if isinstance(data, bytes):
        text = data.decode("utf-8")
    else:
        text = data
    assert text.endswith("\r\n"), "CRLF 로 끝나야 함"
    assert "\n" not in text.replace("\r\n", ""), "맨 LF 금지"
    physical = text.split("\r\n")[:-1]
    for ln in physical:
        assert len(ln.encode("utf-8")) <= 75, "75옥텟 초과: %r" % ln
    # unfold
    lines = []
    for ln in physical:
        if ln.startswith(" ") or ln.startswith("\t"):
            assert lines, "첫 줄에 접힘 표시"
            lines[-1] += ln[1:]
        else:
            lines.append(ln)
    root = {"name": "ROOT", "props": [], "children": []}
    stack = [root]
    for ln in lines:
        m = re.match(r"^([A-Za-z0-9-]+)((?:;[^:]*)?):(.*)$", ln)
        assert m, "속성 형식 오류: %r" % ln
        name, params, value = m.group(1).upper(), m.group(2), m.group(3)
        if name == "BEGIN":
            comp = {"name": value, "props": [], "children": []}
            stack[-1]["children"].append(comp)
            stack.append(comp)
        elif name == "END":
            assert stack[-1]["name"] == value, "BEGIN/END 불일치"
            stack.pop()
        else:
            stack[-1]["props"].append((name, params, value))
    assert len(stack) == 1, "닫히지 않은 컴포넌트"
    assert len(root["children"]) == 1 and root["children"][0]["name"] == "VCALENDAR"
    cal = root["children"][0]
    validate(cal)
    return cal


def prop(comp, name):
    for n, p, v in comp["props"]:
        if n == name:
            return p, v
    return None


def props(comp, name):
    return [(p, v) for n, p, v in comp["props"] if n == name]


def validate(cal):
    assert prop(cal, "VERSION")[1] == "2.0"
    assert prop(cal, "PRODID") is not None
    for ev in cal["children"]:
        if ev["name"] != "VEVENT":
            continue
        for req in ("UID", "DTSTAMP", "DTSTART", "SUMMARY"):
            assert prop(ev, req) is not None, "VEVENT 에 %s 없음" % req
        assert re.fullmatch(r"\d{8}T\d{6}Z", prop(ev, "DTSTAMP")[1]), "DTSTAMP 는 UTC"
        p, v = prop(ev, "DTSTART")
        if "VALUE=DATE" in p:
            assert re.fullmatch(r"\d{8}", v)
        else:
            assert re.fullmatch(r"\d{8}T\d{6}Z", v), "시각 DTSTART 는 UTC(Z)"
        for al in ev["children"]:
            assert al["name"] == "VALARM"
            for req in ("ACTION", "TRIGGER", "DESCRIPTION"):
                assert prop(al, req) is not None, "VALARM 에 %s 없음" % req
