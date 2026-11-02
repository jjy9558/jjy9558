import unittest

import helpers  # noqa: F401
from jarvis.intents import compact, parse, strip_wake


def names(text, **kw):
    return [i.name for i in parse(text, **kw).intents]


class WakeWordTest(unittest.TestCase):
    def test_wake_variants(self):
        for t in ("자비스, 상태", "자비스야 상태", "헤이 자비스 상태", "오케이 자비스, 상태", "Jarvis 상태",
                  "  자비스!! 상태", "쟈비스 상태", "자비스상태", "야 자비스 상태"):
            ok, rest = strip_wake(t)
            self.assertTrue(ok, t)
            self.assertEqual(compact(rest), "상태", t)

    def test_no_wake(self):
        for t in ("상태 알려줘", "오늘 자비스 영화 봤어", ""):
            self.assertFalse(parse(t).wake, t)


class IntentTest(unittest.TestCase):
    CASES = [
        ("자비스, 오늘 일정이랑 작업 상태 알려줘", ["schedule", "status"]),
        ("자비스 작업 상태랑 오늘 일정", ["status", "schedule"]),
        ("자비스, 작업 상태 알려줘", ["status"]),
        ("자비스 작업상태", ["status"]),
        ("자비스 지금 뭐 하고 있어?", ["status"]),
        ("자비스 진행 상황 어때", ["status"]),
        ("자비스 큐에 몇 개 있어", ["status"]),
        ("자비스, 작업 몇 개 남았어?", ["status"]),
        ("자비스 승인 대기 뭐 있어", ["status"]),
        ("자비스 상태 알려줘", ["status"]),
        ("자비스, 이 PDF 요약해줘", ["summarize"]),
        ("자비스 방금 받은 파일 요약 좀", ["summarize"]),
        ("자비스 report.pdf 요약해 줘", ["summarize"]),
        ("자비스 이 문서 핵심만 정리해줘", ["summarize"]),
        ("자비스, 컴퓨터 켜", ["wake_pc"]),
        ("자비스 컴퓨터 좀 켜줘", ["wake_pc"]),
        ("자비스 PC 켜", ["wake_pc"]),
        ("자비스 피씨 켜 줘", ["wake_pc"]),
        ("헤이 자비스, 본컴 깨워줘", ["wake_pc"]),
        ("자비스 5080 켜", ["wake_pc"]),
        ("자비스 메인 컴퓨터 부팅해", ["wake_pc"]),
        ("자비스 컴퓨터 켜져 있어?", ["devices"]),
        ("자비스 5080 꺼져 있어?", ["devices"]),
        ("자비스 기기 상태", ["devices"]),
        ("자비스 컴퓨터 살아 있어", ["devices"]),
        ("자비스, 오늘 스케줄 뭐야", ["schedule"]),
        ("자비스 오늘 약속 있어?", ["schedule"]),
        ("자비스 일정 알려주고 컴퓨터 켜", ["schedule", "wake_pc"]),
        ("자비스, 컴퓨터 켜고 이 PDF 요약해줘", ["wake_pc", "summarize"]),
        ("자비스 뭐 할 수 있어?", ["help"]),
        ("자비스 도움말", ["help"]),
        ("자비스 지금 몇 시야", ["time"]),
        ("자비스 3번 승인해", ["approve"]),
        ("자비스, 승인", ["approve"]),
        ("자비스 2번 작업 취소해", ["reject"]),
        ("자비스 거절", ["reject"]),
        ("자비스, 클로드한테 README 오타 고쳐달라고 해줘", ["claude"]),
        ("자비스 클로드 코드한테 테스트 돌려보라고 시켜", ["claude"]),
        ("자비스 로컬 모델한테 양자역학 쉽게 설명해 달라고 물어봐", ["ask"]),
        ("자비스 내일 서울 날씨 물어봐줘", ["ask"]),
        ("자비스 바나나 노래 불러", []),
    ]

    def test_cases(self):
        for text, want in self.CASES:
            self.assertEqual(names(text), want, text)

    def test_args(self):
        self.assertEqual(parse("자비스 3번 승인해").intents[0].args, {"id": 3})
        self.assertEqual(parse("자비스 승인해").intents[0].args, {"id": None})
        s = parse("자비스 report-11.pdf 요약해줘").intents[0].args
        self.assertEqual((s["file"], s["kind"]), ("report-11.pdf", "pdf"))
        self.assertEqual(parse("자비스 이 PDF 요약해줘").intents[0].args["file"], "@latest")
        self.assertEqual(parse("자비스, 클로드한테 README 오타 고쳐달라고 해줘").intents[0].args["prompt"],
                         "README 오타 고쳐달라고")
        self.assertEqual(parse("자비스 클로드한테 테스트 돌려서 실패 원인 정리 좀 시켜줘").intents[0].args["prompt"],
                         "테스트 돌려서 실패 원인 정리")
        self.assertEqual(parse("자비스 내일 서울 날씨 물어봐줘").intents[0].args["question"], "내일 서울 날씨")

    def test_claude_prompt_keeps_status_words(self):
        # 클로드 프롬프트 안의 '상태', '켜' 같은 단어 때문에 다른 의도가 끼어들면 안 된다
        p = parse("자비스 클로드한테 서버 상태 확인하고 로그 켜는 코드 추가하라고 시켜줘")
        self.assertEqual([i.name for i in p.intents], ["claude"])
        self.assertEqual(p.intents[0].args["prompt"], "서버 상태 확인하고 로그 켜는 코드 추가하라고")

    def test_aliases(self):
        al = {"백업": {"name": "backup"}, "깃 상태": {"name": "git_status"}}
        self.assertEqual(names("자비스 백업 실행해", aliases=al), ["run"])
        self.assertEqual(names("자비스 백업 돌려줘", aliases=al), ["run"])
        self.assertEqual(parse("자비스 깃 상태 실행", aliases=al).intents[0].args["spec"], {"name": "git_status"})
        self.assertEqual(names("자비스 백업 얘기 좀 하자", aliases=al), [])


if __name__ == "__main__":
    unittest.main()
