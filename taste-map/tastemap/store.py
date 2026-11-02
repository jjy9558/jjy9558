"""취향 데이터를 JSON 파일 하나에 저장한다. 위치는 TASTEMAP_HOME 또는 ~/.taste-map.

저장하는 것(개인정보 최소):
- steam: appid, 이름, 구매일, 낸 돈, 플레이 분, 만족도, 덱/안티치트 등급
- diary: 게임별 스크린샷 수와 태그/분위기 집계 (캡션 원문과 파일 경로는 저장하지 않음)
- youtube: 영상 제목과 시청 시각만 (URL, 채널, 계정 정보는 저장하지 않음)
- manual_tags: 사용자가 직접 붙인 태그
"""
import json
import os
from pathlib import Path

FORMAT = 1


def data_dir() -> Path:
    return Path(os.environ.get("TASTEMAP_HOME") or Path.home() / ".taste-map")


def _empty() -> dict:
    return {"format": FORMAT, "steam": {}, "diary": {}, "youtube": {}, "manual_tags": {}, "compat": {}}


class Store:
    def __init__(self, path=None):
        self.path = Path(path) if path else data_dir() / "data.json"
        self.data = _empty()
        if self.path.exists():
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                for k, v in loaded.items():
                    self.data[k] = v
        for k, v in _empty().items():
            self.data.setdefault(k, v)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    @property
    def steam(self) -> dict:
        return self.data["steam"]

    @property
    def diary(self) -> dict:
        return self.data["diary"]

    @property
    def youtube(self) -> dict:
        return self.data["youtube"]

    @property
    def compat(self) -> dict:
        return self.data["compat"]

    @property
    def manual_tags(self) -> dict:
        return self.data["manual_tags"]
