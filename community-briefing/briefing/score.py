"""Interest scoring: weighted keywords per topic, mute words, comment boost."""
import math
import re


def _pattern(word):
    w = word.lower().strip()
    if re.fullmatch(r"[a-z0-9 .+#_\-]+", w):
        # ASCII keyword: word boundaries so "ai" does not match "said"
        return re.compile(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])")
    return re.compile(re.escape(w))  # Korean etc.: substring (조사가 붙어도 매칭)


class Scorer:
    TITLE_W, SUMMARY_W = 1.0, 0.5

    def __init__(self, interests):
        topics = dict(interests.get("topics") or {})
        if interests.get("keywords"):  # flat form is also accepted
            topics.setdefault("관심사", {}).update(interests["keywords"])
        self.keywords = [(topic, kw, float(w), _pattern(kw))
                         for topic, kws in topics.items() for kw, w in kws.items()]
        self.mute = [_pattern(m) for m in interests.get("mute") or []]
        self.comment_boost = float(interests.get("comment_boost", 0.5))
        self.min_score = float(interests.get("min_score", 1.0))

    def is_muted(self, item):
        text = (item.title + " " + item.summary).lower()
        return any(p.search(text) for p in self.mute)

    def score(self, item):
        """Set item.score/topic/matched. Returns False if the item should be dropped."""
        if self.is_muted(item):
            return False
        title, summary = item.title.lower(), item.summary.lower()
        per_topic, matched, total = {}, [], 0.0
        for topic, kw, weight, pat in self.keywords:
            s = (self.TITLE_W if pat.search(title) else 0) + (self.SUMMARY_W if pat.search(summary) else 0)
            if s:
                total += s * weight
                per_topic[topic] = per_topic.get(topic, 0) + s * weight
                matched.append(kw)
        if not matched:
            return False
        if item.comments:
            total += self.comment_boost * math.log10(1 + item.comments)
        item.score, item.matched = total, matched
        item.topic = max(per_topic, key=per_topic.get)
        return total >= self.min_score
