"""Recording -> 자체 완결형 애니메이션 SVG (CSS keyframes만 사용, JS 없음, 외부 리소스 없음).

그림 구조
  창 틀(제목 표시줄 + 점 3개) / 화면 영역(clipPath) / 스크롤용 <g> / 줄마다 <g>
애니메이션
  - 줄은 나타날 시각에 opacity 0 -> 1 (step-end)
  - 입력 중인 명령은 clipPath 사각형의 width 를 글자마다 늘려 타이핑처럼 보이게 함
  - 화면이 꽉 차면 스크롤용 <g> 를 위로 한 줄씩 밀어 올림
  - 모든 글자는 칸 격자에 맞춰 x 좌표를 직접 지정해서, 폰트가 달라도 한글(전각 2칸)이 어긋나지 않음
정적 렌더링
  animate=False 이거나 prefers-reduced-motion 환경에서는 마지막 화면이 그대로 보임
"""

import re
from xml.sax.saxutils import escape

from .ansi import PALETTE16, Cell, Screen, Style, line_runs, text_width, wrap_text

FONT_SIZE = 14
CELL_W = 8.4
LINE_H = 20
BASELINE = 15
PAD_X = 16
PAD_Y = 14
TITLE_H = 38
HOLD = 3.0            # 마지막 화면을 유지하는 시간(초)
START_DELAY = 0.4
OUT_WAIT_CAP = 2.0    # 느린 명령의 대기 표시는 최대 이만큼만
FONT_STACK = '"D2Coding","Nanum Gothic Coding","Malgun Gothic",ui-monospace,"DejaVu Sans Mono",monospace'

BG = "#1e1e1e"
FG = "#cccccc"
TITLE_BG = "#2d2d30"

_BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def _num(v):
    s = ("%.2f" % v).rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def _pct(t, total):
    return round(min(100.0, max(0.0, t / total * 100.0)), 4)


class _Keyframes:
    """(시각, {속성: 값}) 목록을 step-end 키프레임으로 바꾼다. 같은 퍼센트는 뒤의 값으로 덮어써서 단조 증가를 보장."""

    def __init__(self, total):
        self.total = total
        self.points = []   # [pct, props]
        self.state = {}

    def at(self, t, **props):
        p = _pct(t, self.total)
        if self.points and p < self.points[-1][0]:
            p = self.points[-1][0]
        self.state.update(props)
        if self.points and p == self.points[-1][0]:
            self.points[-1][1] = dict(self.state)
        else:
            self.points.append([p, dict(self.state)])

    def css(self, name):
        pts = [[p, dict(pr)] for p, pr in self.points]
        if pts[0][0] > 0:  # 호출부는 항상 0초 지점을 먼저 지정한다
            pts.insert(0, [0.0, dict(pts[0][1])])
        if pts[-1][0] < 100:
            pts.append([100.0, dict(pts[-1][1])])
        out = []
        for p, pr in pts:
            body = ";".join("%s:%s" % kv for kv in pr.items())
            pct = ("%.4f" % p).rstrip("0").rstrip(".") if p not in (0.0, 100.0) else str(int(p))
            out.append("%s%%{%s;animation-timing-function:step-end}" % (pct, body))
        return "@keyframes %s{%s}" % (name, "".join(out))


class _Line:
    def __init__(self, cells, show, kind="out"):
        self.cells = cells
        self.show = show
        self.kind = kind
        self.typed = None        # [(시각, 이 글자까지의 칸 수)]
        self.start_cols = 0      # 타이핑 시작 전 이미 보이는 칸 수(프롬프트)


def _style_cls(style):
    """Style -> (CSS 클래스 목록, 인라인 fill, 배경 토큰)."""
    fg, bg = style.fg, style.bg
    if style.reverse:
        fg, bg = (bg if bg is not None else "dbg"), (fg if fg is not None else "dfg")
    classes, fill = [], None
    if isinstance(fg, int):
        classes.append("c%d" % fg)
    elif fg == "dbg":
        classes.append("cb")
    elif isinstance(fg, str):
        fill = fg
    if style.bold:
        classes.append("b")
    if style.dim:
        classes.append("d")
    if style.underline:
        classes.append("u")
    return classes, fill, bg


def _bg_fill(tok):
    if isinstance(tok, int):
        return PALETTE16[tok]
    if tok == "dfg":
        return FG
    return tok


def _typed_lines(prompt, text, cols, prompt_style, text_style, show_t, t_start, speed):
    """프롬프트+입력 텍스트를 줄바꿈해 _Line 목록을 만들고 글자마다 입력 시각을 매긴다.
    (줄 목록, 마지막 글자 시각)을 돌려준다."""
    scr = Screen(cols)
    scr.style = prompt_style
    scr.feed(prompt)
    scr.style = text_style
    scr.feed(text.replace("\r", " ").replace("\n", " ").replace("\t", " ").replace("\x1b", ""))
    cells_lines = scr.finish() or [[]]
    out, k, t = [], 0, t_start
    for j, cells in enumerate(cells_lines):
        ln = _Line(cells, show_t, "typed")
        ln.typed = []
        skip = len(prompt) if j == 0 else 0
        col = 0
        for idx, c in enumerate(cells):
            col += c.w
            if idx < skip:
                continue
            k += 1
            t = t_start + k * speed
            ln.typed.append((t, col))
        ln.start_cols = sum(c.w for c in cells[:skip])
        if j > 0 and ln.typed:
            ln.show = ln.typed[0][0]
        out.append(ln)
    return out, t


class _Renderer:
    def __init__(self, rec, animate):
        self.rec = rec
        self.animate = animate
        self.cols = rec.width
        self.lines = []
        self.cursors = []     # (t_on, t_off, [(t, line, col)])
        self.final_t = 0
        self._build_timeline()

    # ---------- 타임라인 ----------
    def _build_timeline(self):
        rec, cols = self.rec, self.cols
        t = START_DELAY
        prompt_style = Style(fg=2, bold=True)
        for step in rec.steps:
            kind = step["type"]
            if kind == "pause":
                t += float(step["seconds"])
                continue
            is_cmd = kind == "cmd"
            speed = float(step.get("typing_speed", 0.05 if is_cmd else 0.03))
            text_style = Style(fg=15) if is_cmd else Style(fg="#6a9955")
            t_start = t + (0.25 if speed > 0 else 0)
            lines, t_end = _typed_lines("$ ", step["text"], cols, prompt_style, text_style, t, t_start, speed)
            first_line = len(self.lines)
            self.lines.extend(lines)
            # 커서 경로
            path = [(t, first_line, lines[0].start_cols)]
            for j, ln in enumerate(lines):
                for tt, col in ln.typed:
                    path.append((tt, first_line + j, col))
            if is_cmd:
                t_exec = t_end + (0.3 if speed > 0 else 0.15)
                self.cursors.append((t, t_exec, path))
                t = self._add_output(step, t_exec)
            else:
                t_off = t_end + 0.3
                self.cursors.append((t, t_off, path))
                t = t_off + 0.1
        # 마지막 빈 프롬프트
        scr = Screen(cols)
        scr.style = prompt_style
        scr.feed("$ ")
        ln = _Line(scr.finish()[0], t, "final")
        self.final_idx = len(self.lines)
        self.lines.append(ln)
        self.final_t = t
        self.total = t + HOLD

    def _add_output(self, step, t_exec):
        chunks = step.get("output") or []
        scr = Screen(self.cols)
        done = {}
        for dt, text in chunks:
            scr.feed(text)
            for i in range(len(scr.lines) - 1):
                done.setdefault(i, dt)
        cells_lines = scr.finish()
        last_dt = chunks[-1][0] if chunks else 0
        for i in range(len(cells_lines)):
            done.setdefault(i, last_dt)
        maxl = step.get("max_lines")
        extra = 0
        if maxl and len(cells_lines) > int(maxl):
            extra = len(cells_lines) - int(maxl)
            cells_lines = cells_lines[:int(maxl)]
        delay = float(self.rec.line_delay)
        t = t_exec
        prev = t_exec
        for i, cells in enumerate(cells_lines):
            ti = max(prev + delay, t_exec + 0.12 + min(done.get(i, 0), OUT_WAIT_CAP))
            self.lines.append(_Line(cells, ti, "out"))
            prev = ti
        if extra:
            s = Screen(self.cols)
            s.style = Style(fg=8)
            s.feed("... (%d줄 더)" % extra)
            prev += delay
            self.lines.append(_Line(s.finish()[0], prev, "out"))
        if cells_lines:
            return prev + 0.7
        return t_exec + 0.45

    # ---------- SVG ----------
    def render(self):
        rec, cols = self.rec, self.cols
        n = len(self.lines)
        rows = max(1, min(rec.height, n))
        screen_w = cols * CELL_W
        W = screen_w + 2 * PAD_X
        H = TITLE_H + PAD_Y * 2 + rows * LINE_H
        final_shift = max(0, n - rows) * LINE_H
        anim = self.animate
        T = self.total
        css, defs, body = [], [], []
        used_classes = set()

        def anim_style(kf, name):
            css.append(kf.css(name))
            return ' class="an" style="animation-name:%s"' % name

        # 줄별 SVG
        uid = 0
        for i, ln in enumerate(self.lines):
            uid += 1
            y0 = i * LINE_H
            parts = []
            for start, txt, st in line_runs(ln.cells):
                classes, fill, bg = _style_cls(st)
                width = text_width(txt)
                if bg is not None:
                    parts.append('<rect x="%s" y="%s" width="%s" height="%d" fill="%s"/>'
                                 % (_num(start * CELL_W), _num(y0), _num(width * CELL_W), LINE_H, _bg_fill(bg)))
                parts.extend(self._text_elems(start, txt, y0 + BASELINE, classes, fill, used_classes))
            attrs = ""
            if anim and ln.show > 0:
                kf = _Keyframes(T)
                kf.at(0, opacity=0)
                kf.at(ln.show, opacity=1)
                attrs = anim_style(kf, "l%d" % i)
            if ln.kind == "typed" and ln.typed:
                cid = "t%d" % i
                final_w = ln.typed[-1][1] * CELL_W
                rect = '<rect x="0" y="%s" width="%s" height="%d"%s/>' % (
                    _num(y0), _num(final_w), LINE_H, "")
                if anim:
                    kf = _Keyframes(T)
                    kf.at(0, width="%spx" % _num(ln.start_cols * CELL_W))
                    for tt, col in ln.typed:
                        kf.at(tt, width="%spx" % _num(col * CELL_W))
                    rect = '<rect class="an" style="animation-name:w%d" x="0" y="%s" width="%s" height="%d"/>' % (
                        i, _num(y0), _num(final_w), LINE_H)
                    css.append(kf.css("w%d" % i))
                defs.append('<clipPath id="%s">%s</clipPath>' % (cid, rect))
                inner = '<g clip-path="url(#%s)">%s</g>' % (cid, "".join(parts))
            else:
                inner = "".join(parts)
            if ln.kind == "final":
                inner += self._final_cursor(y0, anim, css, T, ln.show)
            body.append("<g%s>%s</g>" % (attrs, inner))

        # 커서
        cursor_svg = []
        if anim:
            for ci, (t_on, t_off, path) in enumerate(self.cursors):
                kf = _Keyframes(T)
                x0, y0 = path[0][2] * CELL_W, path[0][1] * LINE_H
                kf.at(0, opacity=0, transform="translate(%spx,%spx)" % (_num(x0), _num(y0)))
                kf.at(t_on, opacity=0.85)
                for tt, li, col in path[1:]:
                    kf.at(tt, transform="translate(%spx,%spx)" % (_num(col * CELL_W), _num(li * LINE_H)))
                kf.at(t_off, opacity=0)
                css.append(kf.css("k%d" % ci))
                cursor_svg.append('<rect class="an cu" style="animation-name:k%d" x="0" y="2" width="%s" height="%d" opacity="0"/>'
                                  % (ci, _num(CELL_W), LINE_H - 4))

        # 스크롤
        scroll_attr = ' transform="translate(0,%s)"' % _num(-final_shift) if final_shift else ""
        if anim and final_shift:
            kf = _Keyframes(T)
            kf.at(0, transform="translateY(0px)")
            for i, ln in enumerate(self.lines):
                if i >= rows:
                    kf.at(ln.show, transform="translateY(%spx)" % _num(-(i - rows + 1) * LINE_H))
            css.append(kf.css("sc"))
            scroll_attr = ' class="an" style="animation-name:sc" transform="translate(0,%s)"' % _num(-final_shift)

        svg = []
        title = escape(_BAD_XML.sub("", rec.title))
        desc = escape(_BAD_XML.sub("", self._transcript()))
        svg.append('<svg xmlns="http://www.w3.org/2000/svg" width="%s" height="%s" viewBox="0 0 %s %s" role="img" aria-label="%s">'
                   % (_num(W), _num(H), _num(W), _num(H), title))
        svg.append("<title>%s</title><desc>%s</desc>" % (title, desc))
        svg.append("<style>%s</style>" % self._base_css(used_classes, anim, T, css))
        svg.append("<defs>%s<clipPath id=\"screen\"><rect x=\"-4\" y=\"0\" width=\"%s\" height=\"%d\"/></clipPath></defs>"
                   % ("".join(defs), _num(screen_w + 8), rows * LINE_H))
        svg.append('<rect width="%s" height="%s" rx="10" fill="%s"/>' % (_num(W), _num(H), BG))
        svg.append('<path d="M0 10a10 10 0 0 1 10-10h%sa10 10 0 0 1 10 10v%sH0z" fill="%s"/>'
                   % (_num(W - 20), _num(TITLE_H - 10), TITLE_BG))
        for k, color in enumerate(("#ff5f56", "#ffbd2e", "#27c93f")):
            svg.append('<circle cx="%d" cy="%d" r="6" fill="%s"/>' % (20 + k * 20, TITLE_H // 2, color))
        shown_title = wrap_text(rec.title, max(10, int(screen_w / CELL_W) - 14))[0]
        svg.append('<text class="tt" x="%s" y="%d" text-anchor="middle">%s</text>'
                   % (_num(W / 2), TITLE_H // 2 + 4, escape(_BAD_XML.sub("", shown_title))))
        svg.append('<g transform="translate(%d,%d)"><g clip-path="url(#screen)"><g%s>%s%s</g></g></g>'
                   % (PAD_X, TITLE_H + PAD_Y, scroll_attr, "".join(body), "".join(cursor_svg)))
        svg.append("</svg>")
        return "\n".join(svg) + "\n"

    def _final_cursor(self, y0, anim, css, T, show):
        rect = '<rect class="cu bl" x="0" y="%s" width="%s" height="%d" transform="translate(%s,0)"/>' % (
            _num(y0 + 2), _num(CELL_W), LINE_H - 4, _num(2 * CELL_W))
        return rect if anim else rect.replace('class="cu bl"', 'class="cu"')

    def _text_elems(self, start, txt, y, classes, fill, used):
        """칸 격자에 맞춰 글자마다 x 를 지정한 <text> 요소들."""
        out = []
        cur_x, cur_chars = [], []
        col = start

        def flush():
            if cur_chars:
                cls = (' class="%s"' % " ".join(classes)) if classes else ""
                f = (' style="fill:%s"' % fill) if fill else ""
                used.update(classes)
                out.append('<text%s%s y="%s" x="%s">%s</text>' % (
                    cls, f, _num(y), " ".join(cur_x), escape(_BAD_XML.sub("", "".join(cur_chars)))))
                del cur_x[:], cur_chars[:]

        for ch in txt:
            w = text_width(ch)
            if w == 0:
                continue
            if ch.strip() == "" :
                flush()
            elif ord(ch) > 0xFFFF:
                flush()
                cur_x.append(_num(col * CELL_W + (w * CELL_W - FONT_SIZE) / 2))
                cur_chars.append(ch)
                flush()
            else:
                cur_x.append(_num(col * CELL_W + ((w * CELL_W - FONT_SIZE) / 2 if w == 2 else 0)))
                cur_chars.append(ch)
            col += w
        flush()
        return out

    def _transcript(self):
        out = []
        for s in self.rec.steps:
            if s["type"] == "cmd":
                out.append("$ " + s["text"])
            elif s["type"] == "comment":
                out.append("$ " + s["text"])
        return "\n".join(out)[:2000]

    def _base_css(self, used, anim, T, anim_css):
        rules = [
            "text{font-family:%s;font-size:%dpx;fill:%s;white-space:pre}" % (FONT_STACK, FONT_SIZE, FG),
            ".tt{fill:#9d9d9d;font-size:13px}",
            ".b{font-weight:700}", ".d{opacity:.6}", ".u{text-decoration:underline}", ".cb{fill:%s}" % BG,
            ".cu{fill:#d4d4d4}",
        ]
        for c in sorted(used):
            if re.fullmatch(r"c\d+", c):
                rules.append(".%s{fill:%s}" % (c, PALETTE16[int(c[1:])]))
        if anim:
            rules.append(".an{animation-duration:%ss;animation-iteration-count:infinite;animation-timing-function:step-end}"
                         % _num(T))
            rules.append(".bl{animation:bl 1s step-end infinite}@keyframes bl{0%{opacity:1}50%{opacity:0}}")
            rules.extend(anim_css)
            rules.append("@media (prefers-reduced-motion:reduce){.an,.bl{animation:none!important}}")
        return "".join(rules)


def render_svg(rec, animate=True):
    """Recording 을 SVG 문자열로 만든다. animate=False 면 마지막 화면만 담은 정적 SVG."""
    return _Renderer(rec, animate).render()
