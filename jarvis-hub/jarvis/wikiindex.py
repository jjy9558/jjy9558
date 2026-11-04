"""로컬 위키 색인: 사용자가 저장한 md/txt/html 노트 폴더 -> sqlite 색인 -> 문단 검색.

- 웹 접속 없음. 설정한 폴더 안의 파일만 읽는다 (심볼릭 링크로 폴더 밖을 가리키면 건너뜀).
- 검색 토큰: 한글/한자는 글자 2-gram(조사가 붙어도 매칭), 영문/숫자는 소문자 단어.
- SQLite FTS5가 있으면 bm25로 순위, 없으면 같은 토큰으로 만든 postings 테이블 + 파이썬 점수(tf-idf).
"""
import html.parser
import math
import os
import re
import sqlite3

EXTS = (".md", ".markdown", ".txt", ".html", ".htm")
MAX_FILE_BYTES = 2 * 1024 * 1024
_RUN = re.compile(r"[^\W_]+", re.UNICODE)
_CJK = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af\u3040-\u30ff\u4e00-\u9fff]")


def tokenize(text):
    out = []
    for run in _RUN.findall((text or "").lower()):
        buf = ""
        kind = None
        parts = []
        for ch in run:
            k = bool(_CJK.match(ch))
            if kind is None or k == kind:
                buf += ch
            else:
                parts.append((kind, buf))
                buf = ch
            kind = k
        if buf:
            parts.append((kind, buf))
        for k, seg in parts:
            if not k:
                out.append(seg)
            elif len(seg) == 1:
                out.append(seg)
            else:
                out.extend(seg[i:i + 2] for i in range(len(seg) - 1))
    return out


class _Text(html.parser.HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table", "ul", "ol"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "noscript"):
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")
            if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                self.out.append("\n# ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "noscript"):
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(src):
    p = _Text()
    try:
        p.feed(src)
        p.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", src)
    return "".join(p.out)


def read_note(path):
    with open(path, "rb") as f:
        raw = f.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        return ""
    for enc in ("utf-8-sig", "cp949"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", "replace")
    if path.lower().endswith((".html", ".htm")):
        text = html_to_text(text)
    return text


def split_passages(text, max_chars=700):
    """제목(#) 단위 + 빈 줄 단위 문단. 반환: [(heading, text)]"""
    heading = ""
    cur = []
    out = []

    def flush():
        body = "\n".join(cur).strip()
        cur.clear()
        while len(body) > max_chars:
            cut = max(body.rfind("\n", 0, max_chars), body.rfind(". ", 0, max_chars), body.rfind(" ", 0, max_chars))
            cut = cut if cut > max_chars // 2 else max_chars
            out.append((heading, body[:cut].strip()))
            body = body[cut:].strip()
        if body:
            out.append((heading, body))

    size = 0
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"^#{1,6}\s+(.*)$", s)
        if m:
            flush()
            size = 0
            heading = m.group(1).strip()
            continue
        if not s:
            if size >= max_chars // 3:
                flush()
                size = 0
            continue
        cur.append(s)
        size += len(s)
    flush()
    return out


def fts5_available():
    try:
        c = sqlite3.connect(":memory:")
        try:
            c.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        finally:
            c.close()
        return True
    except sqlite3.Error:
        return False


class WikiIndex:
    def __init__(self, db_path=":memory:", use_fts=None, max_passage_chars=700):
        self.db = sqlite3.connect(db_path or ":memory:")
        self.max_chars = max_passage_chars
        self.fts = fts5_available() if use_fts is None else bool(use_fts and fts5_available())
        d = self.db
        d.execute("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)")
        row = d.execute("SELECT v FROM meta WHERE k='mode'").fetchone()
        mode = "fts" if self.fts else "bigram"
        if row and row[0] != mode:  # 모드가 바뀌면 처음부터 다시
            for t in ("files", "passages", "postings", "fts"):
                d.execute("DROP TABLE IF EXISTS %s" % t)
        d.execute("INSERT OR REPLACE INTO meta VALUES('mode', ?)", (mode,))
        d.execute("CREATE TABLE IF NOT EXISTS files(source TEXT, rel TEXT, mtime REAL, size INTEGER, "
                  "PRIMARY KEY(source, rel))")
        d.execute("CREATE TABLE IF NOT EXISTS passages(id INTEGER PRIMARY KEY, source TEXT, rel TEXT, "
                  "heading TEXT, body TEXT, ntok INTEGER)")
        d.execute("CREATE INDEX IF NOT EXISTS passages_file ON passages(source, rel)")
        if self.fts:
            d.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(toks, tokenize='unicode61')")
        else:
            d.execute("CREATE TABLE IF NOT EXISTS postings(tok TEXT, pid INTEGER, tf INTEGER)")
            d.execute("CREATE INDEX IF NOT EXISTS postings_tok ON postings(tok)")
        d.commit()

    def close(self):
        self.db.close()

    # ---- 색인 ----
    def _drop_file(self, source, rel):
        d = self.db
        ids = [r[0] for r in d.execute("SELECT id FROM passages WHERE source=? AND rel=?", (source, rel))]
        for pid in ids:
            if self.fts:
                d.execute("DELETE FROM fts WHERE rowid=?", (pid,))
            else:
                d.execute("DELETE FROM postings WHERE pid=?", (pid,))
        d.execute("DELETE FROM passages WHERE source=? AND rel=?", (source, rel))
        d.execute("DELETE FROM files WHERE source=? AND rel=?", (source, rel))

    def _add_file(self, source, rel, path, st):
        text = read_note(path)
        d = self.db
        title = os.path.splitext(os.path.basename(rel))[0]
        for heading, body in split_passages(text, self.max_chars):
            toks = tokenize(title + " " + heading + " " + body)
            if not toks:
                continue
            cur = d.execute("INSERT INTO passages(source, rel, heading, body, ntok) VALUES(?,?,?,?,?)",
                            (source, rel, heading, body, len(toks)))
            pid = cur.lastrowid
            if self.fts:
                d.execute("INSERT INTO fts(rowid, toks) VALUES(?,?)", (pid, " ".join(toks)))
            else:
                tf = {}
                for t in toks:
                    tf[t] = tf.get(t, 0) + 1
                d.executemany("INSERT INTO postings VALUES(?,?,?)", [(t, pid, n) for t, n in tf.items()])
        d.execute("INSERT OR REPLACE INTO files VALUES(?,?,?,?)", (source, rel, st.st_mtime, st.st_size))

    def scan_folder(self, source, folder):
        """폴더를 훑어 바뀐 파일만 다시 색인. {"added": n, "removed": n, "files": n}"""
        root = os.path.realpath(folder)
        if not os.path.isdir(root):
            return {"added": 0, "removed": 0, "files": 0, "missing": True}
        seen = {}
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = sorted(x for x in dirs if not x.startswith("."))
            for f in sorted(files):
                if f.startswith(".") or not f.lower().endswith(EXTS):
                    continue
                path = os.path.join(dirpath, f)
                real = os.path.realpath(path)
                if os.path.commonpath([root, real]) != root or not os.path.isfile(real):
                    continue  # 폴더 밖을 가리키는 링크
                seen[os.path.relpath(real, root).replace(os.sep, "/")] = real
        known = {r[0]: (r[1], r[2]) for r in self.db.execute("SELECT rel, mtime, size FROM files WHERE source=?",
                                                              (source,))}
        removed = [r for r in known if r not in seen]
        for rel in removed:
            self._drop_file(source, rel)
        added = 0
        for rel, path in seen.items():
            st = os.stat(path)
            if rel in known and known[rel] == (st.st_mtime, st.st_size):
                continue
            self._drop_file(source, rel)
            self._add_file(source, rel, path, st)
            added += 1
        self.db.commit()
        return {"added": added, "removed": len(removed), "files": len(seen)}

    # ---- 검색 ----
    def search(self, query, source=None, top_k=5):
        qt = list(dict.fromkeys(tokenize(query)))
        if not qt:
            return []
        if self.fts:
            return self._search_fts(qt, source, top_k)
        return self._search_postings(qt, source, top_k)

    def _row(self, pid, score):
        r = self.db.execute("SELECT source, rel, heading, body FROM passages WHERE id=?", (pid,)).fetchone()
        return {"source": r[0], "file": r[1], "heading": r[2], "text": r[3], "score": round(score, 4)}

    def _search_fts(self, qt, source, top_k):
        match = " OR ".join('"%s"' % t.replace('"', '""') for t in qt[:64])
        sql = ("SELECT fts.rowid, bm25(fts) FROM fts JOIN passages p ON p.id = fts.rowid "
               "WHERE fts MATCH ? " + ("AND p.source = ? " if source else "") + "ORDER BY bm25(fts) LIMIT ?")
        args = [match] + ([source] if source else []) + [top_k]
        try:
            rows = self.db.execute(sql, args).fetchall()
        except sqlite3.OperationalError:
            return []
        return [self._row(pid, -score) for pid, score in rows]

    def _search_postings(self, qt, source, top_k):
        d = self.db
        n_docs = d.execute("SELECT COUNT(*) FROM passages" + (" WHERE source=?" if source else ""),
                           [source] if source else []).fetchone()[0] or 1
        scores = {}
        for t in qt[:64]:
            rows = d.execute("SELECT po.pid, po.tf, p.ntok FROM postings po JOIN passages p ON p.id=po.pid "
                             "WHERE po.tok=?" + (" AND p.source=?" if source else ""),
                             [t] + ([source] if source else [])).fetchall()
            if not rows:
                continue
            idf = math.log(1 + n_docs / len(rows))
            for pid, tf, ntok in rows:
                scores[pid] = scores.get(pid, 0.0) + idf * (1 + math.log(tf)) / math.sqrt(ntok)
        best = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:top_k]
        return [self._row(pid, s) for pid, s in best]
