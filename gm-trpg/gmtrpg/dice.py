"""결정적 주사위 엔진. LLM은 굴리지 않고 코드가 판정한다."""
import random
import re

_EXPR = re.compile(r"^\s*(\d*)\s*[dD]\s*(\d+)\s*([+-]\s*\d+)?\s*$")


class Dice:
    def __init__(self, seed=None):
        self.rng = random.Random(seed)

    def roll(self, expr):
        """'2d6+1', 'd20', '3d8-2' -> (total, [개별 눈], modifier)"""
        m = _EXPR.match(expr)
        if not m:
            raise ValueError("잘못된 주사위 표기: %r" % expr)
        n = int(m.group(1) or 1)
        sides = int(m.group(2))
        mod = int((m.group(3) or "0").replace(" ", ""))
        if not (1 <= n <= 100 and 2 <= sides <= 1000):
            raise ValueError("주사위 범위 초과: %r" % expr)
        rolls = [self.rng.randint(1, sides) for _ in range(n)]
        return sum(rolls) + mod, rolls, mod

    def check(self, skill, dc, modifier=0, mode="normal"):
        """d20 + modifier vs dc. mode: normal|advantage|disadvantage"""
        mode = mode if mode in ("advantage", "disadvantage") else "normal"
        first = self.rng.randint(1, 20)
        rolls = [first]
        if mode != "normal":
            rolls.append(self.rng.randint(1, 20))
        nat = max(rolls) if mode == "advantage" else min(rolls) if mode == "disadvantage" else first
        total = nat + modifier
        crit = nat == 20
        fumble = nat == 1
        success = crit or (not fumble and total >= dc)
        grade = ("대성공" if crit else "대실패" if fumble
                 else "성공" if success else "실패")
        return {"skill": skill, "dc": dc, "modifier": modifier, "mode": mode,
                "rolls": rolls, "natural": nat, "total": total,
                "success": success, "grade": grade}


def format_check(r):
    mode = {"advantage": " (유리)", "disadvantage": " (불리)"}.get(r["mode"], "")
    rolls = "/".join(str(x) for x in r["rolls"])
    sign = "%+d" % r["modifier"]
    return "[판정] %s 난이도 %d%s: d20(%s) %s = %d -> %s" % (
        r["skill"], r["dc"], mode, rolls, sign, r["total"], r["grade"])
