"""명령줄: run / render / readme"""

import argparse
import json
import os
import sys

from . import __version__
from .readme import DemoError, make_section, write_readme
from .render import render_svg
from .runner import Recording, record
from .script import Script, ScriptError


def _cmd_run(a):
    script = Script.load(a.script)
    rec = record(script)
    out = a.output or os.path.splitext(a.script)[0] + ".svg"
    if a.trace:
        with open(a.trace, "w", encoding="utf-8") as f:
            f.write(rec.dumps())
    svg = render_svg(rec, animate=not a.static)
    with open(out, "w", encoding="utf-8") as f:
        f.write(svg)
    print("저장: %s (%.1f KB)" % (out, len(svg.encode("utf-8")) / 1024))
    if a.gif:
        from .gif import GifUnavailable, svg_to_gif
        from .render import _Renderer
        try:
            svg_to_gif(svg, _Renderer(rec, True).total, a.gif)
            print("저장: %s" % a.gif)
        except GifUnavailable as e:
            print("GIF 건너뜀: %s" % e, file=sys.stderr)
    return 0


def _cmd_render(a):
    with open(a.trace, encoding="utf-8") as f:
        rec = Recording.from_dict(json.load(f))
    svg = render_svg(rec, animate=not a.static)
    with open(a.output, "w", encoding="utf-8") as f:
        f.write(svg)
    print("저장: %s (%.1f KB)" % (a.output, len(svg.encode("utf-8")) / 1024))
    return 0


def _cmd_readme(a):
    section, info = make_section(a.project, a.svg, with_sub_help=not a.no_sub_help, heading=a.heading)
    if not a.write:
        sys.stdout.write(section)
        return 0
    path = a.readme or os.path.join(info["dir"], "README.md")
    changed = write_readme(path, section)
    print(("README 갱신: " if changed else "변경 없음: ") + path)
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="demomaker", description="CLI 프로젝트의 터미널 데모(SVG)와 README 사용법 섹션을 만듭니다.")
    p.add_argument("--version", action="version", version="demomaker " + __version__)
    sub = p.add_subparsers(dest="cmd", metavar="<명령>")
    sub.required = True

    s = sub.add_parser("run", help="데모 스크립트를 실제로 실행하고 애니메이션 SVG 로 저장")
    s.add_argument("script", help="demo.json 경로")
    s.add_argument("-o", "--output", help="출력 SVG (기본: 스크립트와 같은 이름 .svg)")
    s.add_argument("--static", action="store_true", help="애니메이션 없이 마지막 화면만 담은 SVG")
    s.add_argument("--trace", metavar="PATH", help="실행 기록(JSON)도 저장 (render 로 다시 그릴 수 있음)")
    s.add_argument("--gif", metavar="PATH", help="(선택) GIF 도 만들기. playwright+Pillow 가 있을 때만")
    s.set_defaults(fn=_cmd_run)

    s = sub.add_parser("render", help="저장해 둔 실행 기록(JSON)을 다시 SVG 로 그리기 (명령 재실행 없음)")
    s.add_argument("trace")
    s.add_argument("-o", "--output", required=True)
    s.add_argument("--static", action="store_true")
    s.set_defaults(fn=_cmd_render)

    s = sub.add_parser("readme", help="프로젝트를 살펴보고 README '사용법' 섹션 출력 (--write 로 삽입)")
    s.add_argument("project", help="프로젝트 폴더")
    s.add_argument("--svg", default="docs/demo.svg", help="README 에 넣을 데모 이미지 경로 (기본: docs/demo.svg)")
    s.add_argument("--write", action="store_true", help="README 의 <!-- demo:start --> ~ <!-- demo:end --> 사이를 바꿔 씀 (없으면 끝에 추가)")
    s.add_argument("--readme", help="대상 README 경로 (기본: <project>/README.md)")
    s.add_argument("--heading", default="## 사용법", help="섹션 제목 줄 (기본: '## 사용법')")
    s.add_argument("--no-sub-help", action="store_true", help="명령별 --help 는 싣지 않음")
    s.set_defaults(fn=_cmd_readme)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except (DemoError, ScriptError) as e:
        print("오류: %s" % e, file=sys.stderr)
        return 1
