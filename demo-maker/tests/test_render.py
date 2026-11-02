import os
import re
import unittest
import xml.etree.ElementTree as ET

from demomaker.render import render_svg
from tests.helpers import sample_recording

NS = "{http://www.w3.org/2000/svg}"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def keyframe_blocks(svg):
    return re.findall(r"@keyframes ([\w-]+)\{(.*?)\}\}?(?=@keyframes|@media|</style>|\.|$)", svg, re.S)


def parse_keyframes(svg):
    """{이름: [퍼센트...]}"""
    style = re.search(r"<style>(.*?)</style>", svg, re.S).group(1)
    out = {}
    for m in re.finditer(r"@keyframes ([\w-]+)\{((?:[\d.]+%\{[^}]*\})+)\}", style):
        out[m.group(1)] = [float(p) for p in re.findall(r"([\d.]+)%\{", m.group(2))]
    return out


class SvgTests(unittest.TestCase):
    def setUp(self):
        self.rec = sample_recording()
        self.svg = render_svg(self.rec)

    def test_well_formed_xml(self):
        root = ET.fromstring(self.svg)
        self.assertEqual(root.tag, NS + "svg")
        self.assertTrue(root.get("viewBox"))

    def test_escaping_of_special_characters(self):
        root = ET.fromstring(self.svg)
        title = root.find(NS + "title").text
        self.assertEqual(title, "테스트 & <데모>")
        self.assertNotIn("<태그>", self.svg)

    def test_self_contained(self):
        low = self.svg.lower()
        for bad in ("<script", "<image", "<foreignobject", "@import", "javascript:", "<link", "onload=", "<use"):
            self.assertNotIn(bad, low)
        self.assertEqual(re.findall(r'(?:href|src)="[^"]*"', self.svg), [])
        self.assertEqual(re.findall(r"https?://[^\"')\s]+", self.svg), ["http://www.w3.org/2000/svg"])
        for ref in re.findall(r"url\(([^)]*)\)", self.svg):
            self.assertTrue(ref.startswith("#"), ref)

    def test_fonts_declared_with_korean_fallbacks(self):
        for f in ('"D2Coding"', '"Nanum Gothic Coding"', '"Malgun Gothic"', "monospace"):
            self.assertIn(f, self.svg)

    def test_keyframes_monotonic_and_referenced(self):
        kfs = parse_keyframes(self.svg)
        self.assertGreater(len(kfs), 10)
        for name, pcts in kfs.items():
            self.assertEqual(pcts, sorted(pcts), name)
            self.assertEqual(len(pcts), len(set(pcts)), name)
            self.assertGreaterEqual(pcts[0], 0)
            self.assertLessEqual(pcts[-1], 100)
            self.assertEqual(pcts[0], 0.0, name)
            if name != "bl":   # 커서 깜빡임(0%,50%)만 예외
                self.assertEqual(pcts[-1], 100.0, name)
        used = set(re.findall(r"animation-name:([\w-]+)", self.svg))
        self.assertTrue(used <= set(kfs), used - set(kfs))
        self.assertTrue(set(kfs) - used <= {"bl"})   # 깜빡임 키프레임만 클래스로 참조

    def test_line_reveal_times_follow_script_order(self):
        kfs = parse_keyframes(self.svg)
        starts = []
        for i in range(60):
            if ("l%d" % i) in kfs:
                starts.append(kfs["l%d" % i][1])
        self.assertEqual(starts, sorted(starts))

    def test_scroll_when_output_exceeds_height(self):
        kfs = parse_keyframes(self.svg)
        self.assertIn("sc", kfs)
        self.assertGreater(len(kfs["sc"]), 5)
        root = ET.fromstring(self.svg)
        # 화면 영역 clipPath 높이 = height(10줄) * 20
        clip = [c for c in root.iter(NS + "clipPath") if c.get("id") == "screen"][0]
        self.assertEqual(float(clip[0].get("height")), 10 * 20)

    def test_no_scroll_when_short(self):
        from demomaker.runner import Recording
        rec = Recording("t", 40, 24, 0.05, [{"type": "cmd", "text": "ls", "output": [[0.0, "a\n"]]}])
        svg = render_svg(rec)
        self.assertNotIn("animation-name:sc", svg)

    def test_static_has_no_animation(self):
        svg = render_svg(self.rec, animate=False)
        ET.fromstring(svg)
        self.assertNotIn("@keyframes", svg)
        self.assertNotIn("animation", svg)
        self.assertIn(">29:<", svg)   # 마지막 출력 줄이 최종 프레임에 있음

    def test_glyph_positions_on_cell_grid_for_wide_chars(self):
        root = ET.fromstring(render_svg(sample_recording(3, 10, 40), animate=False))
        xs = None
        for t in root.iter(NS + "text"):
            if (t.text or "").startswith("한글") and t.get("class") is None:
                xs = [float(v) for v in t.get("x").split()]
                break
        self.assertIsNotNone(xs)
        step = xs[1] - xs[0]
        self.assertAlmostEqual(step, 16.8, places=1)   # 전각 한 글자 = 2칸 = 16.8px

    def test_ansi_colors_become_classes(self):
        self.assertIn(".c2{fill:#23d18b}", self.svg)
        self.assertRegex(self.svg, r'class="c2"')

    def test_reduced_motion_rule_present(self):
        self.assertIn("prefers-reduced-motion", self.svg)

    def test_total_duration_covers_all_events(self):
        m = re.search(r"animation-duration:([\d.]+)s", self.svg)
        self.assertGreater(float(m.group(1)), 5)


class ShippedExamples(unittest.TestCase):
    def test_examples_valid_and_small(self):
        d = os.path.join(ROOT, "examples")
        svgs = [f for f in os.listdir(d) if f.endswith(".svg")]
        if not svgs:
            self.skipTest("예제 SVG 없음")
        for f in svgs:
            p = os.path.join(d, f)
            self.assertLess(os.path.getsize(p), 300 * 1024, f)
            with open(p, encoding="utf-8") as fh:
                text = fh.read()
            ET.fromstring(text)
            self.assertNotIn("<script", text)
            for name, pcts in parse_keyframes(text).items():
                self.assertEqual(pcts, sorted(set(pcts)), (f, name))


if __name__ == "__main__":
    unittest.main()
