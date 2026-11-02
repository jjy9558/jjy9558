"""선택 기능: Playwright(Python) + Pillow 가 있을 때만 SVG 애니메이션을 GIF 로 만든다.
표준 라이브러리만으로는 GIF 를 만들 수 없으므로, 없으면 안내 메시지와 함께 건너뛴다."""

import io
import os
import tempfile


class GifUnavailable(RuntimeError):
    pass


def svg_to_gif(svg_text, total_seconds, out_path, fps=10):
    try:
        from playwright.sync_api import sync_playwright
        from PIL import Image
    except ImportError:
        raise GifUnavailable("GIF 는 playwright 와 Pillow 가 있어야 만들 수 있어요 (pip install playwright pillow). "
                             "SVG 는 GitHub README 에서 그대로 움직이니 그대로 쓰셔도 됩니다.")
    frames = []
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "demo.svg")
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg_text)
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto("file://" + path)
            for i in range(int(total_seconds * fps)):
                ms = i * 1000 / fps
                page.evaluate("ms => document.getAnimations().forEach(a => { a.pause(); a.currentTime = ms; })", ms)
                frames.append(Image.open(io.BytesIO(page.screenshot())).convert("P", palette=Image.ADAPTIVE))
            browser.close()
    frames[0].save(out_path, save_all=True, append_images=frames[1:], duration=int(1000 / fps), loop=0, optimize=True)
