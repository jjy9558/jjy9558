from demomaker.runner import Recording


def sample_recording(n_out=30, height=10, width=60):
    out = "".join("줄 %d: \x1b[32m한글 ok\x1b[0m\n" % i for i in range(n_out))
    steps = [
        {"type": "comment", "text": "# 설명 & <태그>", "typing_speed": 0.03},
        {"type": "cmd", "text": "echo 한글", "typing_speed": 0.05,
         "output": [[0.01, "한글\n"]], "exit_code": 0},
        {"type": "pause", "seconds": 1},
        {"type": "cmd", "text": "seq 긴출력 " + "x" * 80, "typing_speed": 0.02,
         "output": [[0.1, out[:50]], [0.3, out[50:]]], "exit_code": 0},
    ]
    return Recording("테스트 & <데모>", width, height, 0.05, steps)
