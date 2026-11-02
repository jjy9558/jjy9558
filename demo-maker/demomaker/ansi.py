"""ANSI SGR 해석, 동아시아 전각 문자 폭 계산, 줄바꿈을 하는 작은 터미널 화면 모델."""

import re
import unicodedata

# 기본 16색 (VS Code 다크 테마 계열). 인덱스 0-7 일반, 8-15 밝은색
PALETTE16 = [
    "#1e1e1e", "#f14c4c", "#23d18b", "#f5f543", "#3b8eea", "#d670d6", "#29b8db", "#cccccc",
    "#666666", "#ff6b6b", "#4ee6a6", "#ffff7f", "#6aa9ff", "#f08cf0", "#52d3ee", "#ffffff",
]


def char_width(ch):
    """터미널 칸 수: 결합/제어 문자 0, 전각(W/F) 2, 나머지 1."""
    if ch in "​‌‍︎️":
        return 0
    if unicodedata.combining(ch) or unicodedata.category(ch) in ("Mn", "Me", "Cf", "Cc"):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def text_width(s):
    return sum(char_width(c) for c in s)


def wrap_text(s, cols):
    """문자열을 cols 칸 기준으로 잘라 줄 목록으로 돌려준다 (전각 문자를 쪼개지 않음)."""
    lines, cur, w = [], "", 0
    for ch in unicodedata.normalize("NFC", s):
        cw = char_width(ch)
        if cw == 0:
            continue
        if w + cw > cols:
            lines.append(cur)
            cur, w = "", 0
        cur += ch
        w += cw
    lines.append(cur)
    return lines


class Style:
    """fg/bg: None | 0..15 | '#rrggbb'."""
    __slots__ = ("fg", "bg", "bold", "dim", "underline", "reverse")

    def __init__(self, fg=None, bg=None, bold=False, dim=False, underline=False, reverse=False):
        self.fg, self.bg, self.bold, self.dim, self.underline, self.reverse = fg, bg, bold, dim, underline, reverse

    def key(self):
        return (self.fg, self.bg, self.bold, self.dim, self.underline, self.reverse)

    def copy(self):
        return Style(*self.key())

    def __eq__(self, other):
        return isinstance(other, Style) and self.key() == other.key()

    def __hash__(self):
        return hash(self.key())

    def __repr__(self):
        return "Style%r" % (self.key(),)


def color256(n):
    if n < 16:
        return n
    if n >= 232:
        v = 8 + (n - 232) * 10
        return "#%02x%02x%02x" % (v, v, v)
    n -= 16
    steps = [0, 95, 135, 175, 215, 255]
    return "#%02x%02x%02x" % (steps[n // 36], steps[(n // 6) % 6], steps[n % 6])


def apply_sgr(style, params):
    """SGR 파라미터 목록(int)을 style(복사본)에 적용해 돌려준다."""
    st = style.copy()
    if not params:
        params = [0]
    i = 0
    while i < len(params):
        p = params[i]
        if p == 0:
            st = Style()
        elif p == 1:
            st.bold = True
        elif p == 2:
            st.dim = True
        elif p == 4:
            st.underline = True
        elif p == 7:
            st.reverse = True
        elif p == 22:
            st.bold = st.dim = False
        elif p == 24:
            st.underline = False
        elif p == 27:
            st.reverse = False
        elif 30 <= p <= 37:
            st.fg = p - 30
        elif p == 39:
            st.fg = None
        elif 40 <= p <= 47:
            st.bg = p - 40
        elif p == 49:
            st.bg = None
        elif 90 <= p <= 97:
            st.fg = p - 90 + 8
        elif 100 <= p <= 107:
            st.bg = p - 100 + 8
        elif p in (38, 48):
            target = "fg" if p == 38 else "bg"
            if i + 1 < len(params) and params[i + 1] == 5 and i + 2 < len(params):
                setattr(st, target, color256(max(0, min(255, params[i + 2]))))
                i += 2
            elif i + 4 < len(params) and params[i + 1] == 2:
                r, g, b = (max(0, min(255, x)) for x in params[i + 2:i + 5])
                setattr(st, target, "#%02x%02x%02x" % (r, g, b))
                i += 4
        i += 1
    return st


_TOKEN = re.compile(
    r"\x1b\[([0-9;:?<=>]*)([@-~])"      # CSI
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC
    r"|\x1b[@-Z\\-_]"                    # 기타 2바이트 이스케이프
    r"|\r\n|[\r\n\t\b]"
)


class Cell:
    __slots__ = ("ch", "w", "style")

    def __init__(self, ch, w, style):
        self.ch, self.w, self.style = ch, w, style


class Screen:
    """스크롤백이 무한한 간단한 화면. feed()로 텍스트를 넣으면 줄(셀 목록) 단위로 쌓인다.

    - SGR 색/굵기 지원, \\r(줄 처음으로) 덮어쓰기, \\t 탭(8칸), \\b 한 칸 되돌리기
    - cols 를 넘으면 자동 줄바꿈 (전각 문자는 쪼개지 않고 다음 줄로)
    """

    def __init__(self, cols=80):
        self.cols = cols
        self.lines = [[]]
        self.wrapped = [False]   # 자동 줄바꿈으로 이어진 줄인가
        self.col = 0
        self.style = Style()

    # 현재 줄 칸 수
    def _row(self):
        return self.lines[-1]

    def _newline(self, wrapped=False):
        self.lines.append([])
        self.wrapped.append(wrapped)
        self.col = 0

    def _put(self, ch):
        w = char_width(ch)
        if w == 0:
            return
        if self.col + w > self.cols:
            self._newline(wrapped=True)
        row = self._row()
        # 현재 col 위치까지 공백 채우기 (\r 이후 덮어쓰기 포함)
        pos = sum(c.w for c in row)
        while pos < self.col:
            row.append(Cell(" ", 1, Style()))
            pos += 1
        # col 에서부터 w 칸 덮어쓰기
        idx, acc = 0, 0
        while idx < len(row) and acc < self.col:
            acc += row[idx].w
            idx += 1
        if acc > self.col:  # 전각 문자 한가운데를 덮는 경우: 그 칸을 공백으로 대체
            idx -= 1
            row[idx] = Cell(" ", 1, Style())
            row.insert(idx + 1, Cell(" ", 1, Style()))
            idx += 1
        remove, covered = 0, 0
        while idx + remove < len(row) and covered < w:
            covered += row[idx + remove].w
            remove += 1
        del row[idx:idx + remove]
        row.insert(idx, Cell(ch, w, self.style))
        for k in range(covered - w):
            row.insert(idx + 1, Cell(" ", 1, Style()))
        self.col += w

    def feed(self, text):
        text = unicodedata.normalize("NFC", text)
        pos = 0
        for m in _TOKEN.finditer(text):
            for ch in text[pos:m.start()]:
                self._put(ch)
            pos = m.end()
            tok = m.group(0)
            if tok in ("\n", "\r\n"):
                self._newline()
            elif tok == "\r":
                self.col = 0
            elif tok == "\t":
                nxt = (self.col // 8 + 1) * 8
                for _ in range(min(nxt, self.cols) - self.col):
                    self._put(" ")
            elif tok == "\b":
                self.col = max(0, self.col - 1)
            elif m.group(2) == "m" and tok.startswith("\x1b["):
                raw = m.group(1).replace(":", ";")
                if raw.startswith(("?", "<", "=", ">")):
                    continue
                params = [int(x) if x else 0 for x in raw.split(";")] if raw else []
                self.style = apply_sgr(self.style, params)
            elif m.group(2) == "K" and tok.startswith("\x1b["):
                # 줄 지우기(대부분 진행 표시줄): 커서 오른쪽을 지운다
                row = self._row()
                keep, acc = [], 0
                for c in row:
                    if acc + c.w > self.col:
                        break
                    keep.append(c)
                    acc += c.w
                self.lines[-1] = keep
            # 그 밖의 CSI/OSC 는 무시
        for ch in text[pos:]:
            self._put(ch)

    def finish(self):
        """끝의 빈 줄들을 지운 줄 목록을 돌려준다 (각 줄은 Cell 목록)."""
        lines = list(self.lines)
        while len(lines) > 1 and not lines[-1]:
            lines.pop()
        return lines if lines != [[]] else []


def line_runs(cells):
    """셀 목록을 (시작칸, 텍스트, Style) 같은 스타일 구간으로 묶는다."""
    runs, col = [], 0
    for c in cells:
        if runs and runs[-1][2] == c.style and runs[-1][0] + text_width(runs[-1][1]) == col:
            s, t, st = runs[-1]
            runs[-1] = (s, t + c.ch * 1, st)
        else:
            runs.append((col, c.ch, c.style))
        col += c.w
    return runs


_ESC_ONLY = re.compile(r"\x1b\[[0-9;:?<=>]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")


def strip_ansi(text):
    return _ESC_ONLY.sub("", text)
