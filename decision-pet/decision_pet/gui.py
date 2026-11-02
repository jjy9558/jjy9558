"""tkinter GUI: 항상 위, 테두리 없는 작은 펫 창. 이 모듈은 `gui` 명령에서만 import 된다."""
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, simpledialog

from .core import Pet, epitaph
from .decide import parse_options

FONT_CANDIDATES = ["Malgun Gothic", "Apple SD Gothic Neo", "Noto Sans CJK KR", "TkDefaultFont"]
W, H = 260, 270
BG = "#fdf6e3"
KEY = "#fe00fe"  # Windows 투명 색 (지원 안 하면 무시됨)


def pick_font_family(root):
    available = set(tkfont.families(root))
    for fam in FONT_CANDIDATES:
        if fam in available:
            return fam
    return "TkDefaultFont"


class PetWindow:
    def __init__(self, pet=None):
        self.pet = pet or Pet()
        self.root = tk.Tk()
        self.root.title("결정 펫")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.geometry("%dx%d+%d+%d" % (W, H, self.root.winfo_screenwidth() - W - 40,
                                            self.root.winfo_screenheight() - H - 80))
        self.family = pick_font_family(self.root)
        self.font = (self.family, 10)
        bg = BG
        try:
            self.root.attributes("-transparentcolor", KEY)  # Windows 전용
            bg = KEY
        except tk.TclError:
            pass
        self.root.configure(bg=bg)
        self.canvas = tk.Canvas(self.root, width=W, height=H, bg=bg, highlightthickness=0)
        self.canvas.pack()
        self.bubble_items = []
        self.bubble_after = None
        self.tick = 0
        self.blinking = False
        self._drag = (0, 0)
        self._draw_pet()
        self._build_menu()
        self.canvas.bind("<ButtonPress-1>", self._drag_start)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.canvas.bind("<Button-3>", self._popup)
        self.canvas.bind("<Button-2>", self._popup)  # macOS
        self.canvas.bind("<Double-Button-1>", lambda e: self.what_now())
        self.root.after(500, self._animate)
        self.root.after(1500, self._greet)
        self._schedule_nudge()

    # ---------------------------------------------------------- drawing
    def _draw_pet(self):
        c = self.canvas
        cx, base = W // 2, 235
        self.body_ids = []

        def add(item):
            self.body_ids.append(item)
            return item

        add(c.create_polygon(cx - 50, base - 95, cx - 38, base - 140, cx - 15, base - 105,
                             fill="#f4a261", outline="#b5651d", width=2))
        add(c.create_polygon(cx + 50, base - 95, cx + 38, base - 140, cx + 15, base - 105,
                             fill="#f4a261", outline="#b5651d", width=2))
        add(c.create_oval(cx - 62, base - 120, cx + 62, base, fill="#f4a261", outline="#b5651d", width=2))
        add(c.create_oval(cx - 38, base - 60, cx + 38, base - 5, fill="#ffe8cc", outline=""))
        self.eye_l = add(c.create_oval(cx - 30, base - 85, cx - 16, base - 65, fill="#222", outline=""))
        self.eye_r = add(c.create_oval(cx + 16, base - 85, cx + 30, base - 65, fill="#222", outline=""))
        add(c.create_oval(cx - 48, base - 62, cx - 33, base - 50, fill="#ff9aa2", outline=""))
        add(c.create_oval(cx + 33, base - 62, cx + 48, base - 50, fill="#ff9aa2", outline=""))
        add(c.create_arc(cx - 12, base - 62, cx + 12, base - 38, start=200, extent=140,
                         style=tk.ARC, outline="#222", width=2))
        self.eye_coords = {}

    def _blink(self):
        c = self.canvas
        self.blinking = True
        for i in (self.eye_l, self.eye_r):
            self.eye_coords[i] = c.coords(i)  # 현재 위치 기준 (바운스 중일 수 있음)
            x1, y1, x2, y2 = self.eye_coords[i]
            c.coords(i, x1, (y1 + y2) / 2 - 1, x2, (y1 + y2) / 2 + 1)
        self.root.after(150, self._unblink)

    def _unblink(self):
        for i in (self.eye_l, self.eye_r):
            self.canvas.coords(i, *self.eye_coords[i])
        self.blinking = False

    def _animate(self):
        self.tick += 1
        offsets = [0, -2, -4, -2]  # 살짝 통통
        dy = offsets[self.tick % 4] - offsets[(self.tick - 1) % 4]
        for i in self.body_ids:
            self.canvas.move(i, 0, dy)
        if self.tick % 9 == 0 and not self.blinking:
            self._blink()
        self.root.after(350, self._animate)

    # ----------------------------------------------------------- bubble
    def say(self, text, ms=9000):
        c = self.canvas
        for i in self.bubble_items:
            c.delete(i)
        self.bubble_items = []
        if self.bubble_after:
            self.root.after_cancel(self.bubble_after)
        t = c.create_text(W // 2, 12, text=text, width=W - 40, anchor="n", font=self.font,
                          fill="#222")
        x1, y1, x2, y2 = c.bbox(t)
        r = c.create_rectangle(x1 - 8, y1 - 5, x2 + 8, y2 + 5, fill="white", outline="#555", width=2)
        tail = c.create_polygon(W // 2 - 8, y2 + 5, W // 2 + 8, y2 + 5, W // 2, y2 + 17,
                                fill="white", outline="#555", width=2)
        cover = c.create_line(W // 2 - 6, y2 + 5, W // 2 + 6, y2 + 5, fill="white", width=3)
        c.tag_raise(t)
        self.bubble_items = [r, tail, cover, t]
        self.bubble_after = self.root.after(ms, self.hide_bubble)

    def hide_bubble(self):
        for i in self.bubble_items:
            self.canvas.delete(i)
        self.bubble_items = []
        self.bubble_after = None

    # ------------------------------------------------------------- drag
    def _drag_start(self, e):
        self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def _drag_move(self, e):
        self.root.geometry("+%d+%d" % (e.x_root - self._drag[0], e.y_root - self._drag[1]))

    # ------------------------------------------------------------- menu
    def _build_menu(self):
        m = tk.Menu(self.root, tearoff=0, font=self.font)
        for label, cmd in [
            ("지금 뭐 해?", self.what_now),
            ("할 일 추가", self.add_task),
            ("결정해줘", self.decide),
            ("오늘의 실험", self.experiment),
            ("묘지 보기", self.show_graveyard),
            (None, None),
            ("끝냈어", self.done),
            ("미룰래", self.skip),
            (None, None),
            ("종료", self.root.destroy),
        ]:
            if label is None:
                m.add_separator()
            else:
                m.add_command(label=label, command=cmd)
        self.menu = m

    def _popup(self, e):
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    # ---------------------------------------------------------- actions
    def _greet(self):
        pending = self.pet.pending_followups()
        if pending:
            self.ask_followup(pending[0])
        else:
            self.say("안녕! 우클릭하면 메뉴가 나와. 오늘은 뭐 할까?")

    def what_now(self):
        for t in self.pet.sweep():
            self.say("🪦 " + epitaph(t), 12000)
        res = self.pet.suggest()
        if res is None:
            self.say("할 일이 없어! '할 일 추가'로 알려줘.")
            return
        t = res["task"]
        self.say("오늘은 이거 해: %s (%d분)\n(%s)" % (t["title"], t["effort"], res["explanation"]), 15000)

    def add_task(self):
        title = simpledialog.askstring("할 일 추가", "무슨 일이야?", parent=self.root)
        if not title:
            return
        effort = simpledialog.askinteger("시간", "얼마나 걸려? (5 / 15 / 60분)", parent=self.root,
                                         initialvalue=15)
        try:
            t = self.pet.add(title, effort or 15)
            self.say("추가했어: %s (%d분)" % (t["title"], t["effort"]))
        except ValueError as e:
            messagebox.showwarning("앗", str(e), parent=self.root)

    def done(self):
        try:
            t = self.pet.done()
            self.say("짝짝! '%s' 끝냈네. 최고야!" % t["title"])
        except LookupError as e:
            self.say(str(e))

    def skip(self):
        try:
            t, buried = self.pet.skip()
        except LookupError as e:
            self.say(str(e))
            return
        if buried:
            self.say("🪦 " + epitaph(t), 12000)
        else:
            self.say("괜찮아, 미뤄도 돼. (%d번째)" % t["times_skipped"])

    def experiment(self):
        text, done = self.pet.experiment_today()
        if done:
            self.say("오늘의 실험은 이미 했어! 대단해.\n'%s'" % text)
        elif messagebox.askyesno("오늘의 15분 실험", "%s\n\n해냈어?" % text, parent=self.root):
            self.pet.complete_experiment()
            self.say("실험 성공! 15분이 쌓였어.")
        else:
            self.say("오늘의 15분 실험: %s" % text, 12000)

    def decide(self):
        raw = simpledialog.askstring("결정해줘", "선택지 2~5개 (예: 라면 vs 김밥 vs 굶기)", parent=self.root)
        if not raw:
            return
        try:
            options = parse_options(raw)
        except ValueError as e:
            messagebox.showwarning("앗", str(e), parent=self.root)
            return
        mode = self._ask_mode()
        if mode is None:
            return
        criteria = scores = None
        try:
            if mode == "score":
                crit_raw = simpledialog.askstring(
                    "점수표", "기준 2~3개를 '이름:가중치'로 (예: 맛:3, 가격:2)", parent=self.root)
                if not crit_raw:
                    return
                criteria = []
                for part in crit_raw.split(","):
                    name, _, w = part.strip().partition(":")
                    if name.strip():
                        criteria.append((name.strip(), float(w) if w.strip() else 1.0))
                scores = {}
                for o in options:
                    scores[o] = {}
                    for name, _w in criteria:
                        v = simpledialog.askinteger("점수", "%s - %s (1~5)" % (o, name),
                                                    parent=self.root, minvalue=1, maxvalue=5)
                        if v is None:
                            return
                        scores[o][name] = v
            res = self.pet.decide(options, mode, criteria, scores)
        except ValueError as e:
            messagebox.showwarning("앗", str(e), parent=self.root)
            return
        self.say("결정! %s\n%s" % (res["choice"], res["reason"]), 15000)

    def _ask_mode(self):
        top = tk.Toplevel(self.root)
        top.title("방법 고르기")
        top.attributes("-topmost", True)
        var = tk.StringVar(value="coin")
        result = {"mode": None}
        for text, val in [("동전 던지기", "coin"), ("점수표", "score"), ("LLM에게 물어보기", "llm")]:
            tk.Radiobutton(top, text=text, value=val, variable=var, font=self.font).pack(anchor="w", padx=12)

        def ok():
            result["mode"] = var.get()
            top.destroy()

        tk.Button(top, text="결정!", command=ok, font=self.font).pack(pady=8)
        top.transient(self.root)
        top.grab_set()
        self.root.wait_window(top)
        return result["mode"]

    def ask_followup(self, d):
        v = simpledialog.askinteger(
            "어제 결정 만족?",
            "'%s' 중에 '%s'(으)로 했었지?\n만족도 1~5 (취소=건너뛰기)" % (
                " vs ".join(d["options"]), d["choice"]),
            parent=self.root, minvalue=1, maxvalue=5)
        if v is None:
            self.pet.dismiss_followup(d["id"])
            self.say("알겠어, 안 물어볼게.")
        else:
            self.pet.rate_decision(d["id"], v)
            self.say("기록했어! 다음 결정에 참고할게.")

    def show_graveyard(self):
        top = tk.Toplevel(self.root)
        top.title("안 한 일 묘지")
        top.attributes("-topmost", True)
        lb = tk.Listbox(top, width=70, height=12, font=self.font)
        lb.pack(padx=8, pady=8)
        graves = self.pet.graveyard()
        for t in graves:
            lb.insert(tk.END, "#%d 🪦 %s" % (t["id"], epitaph(t)))
        if not graves:
            lb.insert(tk.END, "묘지가 비어 있어요. 평화롭네요.")

        def revive():
            sel = lb.curselection()
            if not sel or not graves:
                return
            t = self.pet.resurrect(graves[sel[0]]["id"])
            lb.delete(sel[0])
            graves.pop(sel[0])
            self.say("부활! '%s' 다시 해보자." % t["title"])

        tk.Button(top, text="부활시키기", command=revive, font=self.font).pack(pady=(0, 8))

    # ------------------------------------------------------------ nudge
    def _schedule_nudge(self):
        minutes = max(1, int(self.pet.config.get("nudge_minutes", 30)))
        self.root.after(minutes * 60 * 1000, self._nudge)

    def _nudge(self):
        t = self.pet.current_task()
        if t is not None:
            self.say("아직 '%s' 하는 중이지? 끝났으면 '끝냈어'!" % t["title"], 12000)
        self._schedule_nudge()

    def run(self):
        self.root.mainloop()


def run_gui():
    PetWindow().run()
