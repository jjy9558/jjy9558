"""판매자에게 물어볼 질문 생성 (빠진 정보 + 위험 신호 확인)."""

from dataclasses import dataclass
from typing import List, Optional

from .judge import Judgment
from .models import Parsed


@dataclass
class Question:
    topic: str
    text: str
    priority: int  # 1 = 꼭 물어볼 것, 2 = 물어보면 좋음, 3 = 참고


def make_questions(parsed: Parsed, j: Optional[Judgment] = None) -> List[Question]:
    qs: List[Question] = []
    excluded = parsed.excluded
    kind = parsed.val("kind", "full_pc")
    full = kind == "full_pc"

    def q(topic, text, pr):
        qs.append(Question(topic, text, pr))

    # ---- CPU ----
    if not parsed.has("cpu") and full:
        q("cpu", "혹시 CPU 정확한 모델명 알 수 있을까요? (예: i5-12400F, 라이젠 5600)", 1)
    elif parsed.has("cpu") and (parsed.fields["cpu"].meta.get("generic") or parsed.conf("cpu") < 0.7):
        q("cpu", "혹시 CPU 정확한 모델명(끝 알파벳 K/F 포함) 알 수 있을까요?", 1)

    # ---- GPU ----
    if not parsed.has("gpu") and full and "gpu" not in excluded:
        if not (parsed.has("cpu") and _igpu(parsed.val("cpu"))):
            q("gpu", "혹시 그래픽카드 모델명과 VRAM 용량 알 수 있을까요?", 1)
    elif parsed.has("gpu"):
        f = parsed.fields["gpu"]
        if f.meta.get("defaulted"):
            variants = f.meta.get("variants") or []
            v = "/".join("{}GB".format(x) for x in variants)
            q("gpu", "그래픽카드 VRAM이 {} 중 어느 쪽인가요? (제조사/모델명도 알려주세요)".format(v or "몇 GB"), 1)
        q("gpu_use", "그래픽카드 사용 기간이랑 채굴 이력은 없는지 알 수 있을까요? (AS 기간이나 구매 영수증 있으면 같이요)", 2)

    # ---- RAM ----
    if "ram" not in excluded and full:
        if not parsed.has("ram_gb"):
            q("ram", "혹시 램 용량이랑 DDR 규격(DDR4/DDR5), 클럭, 몇 개 꽂혀 있는지 알 수 있을까요?", 1)
        else:
            missing = []
            if not parsed.has("ram_type"):
                missing.append("DDR 규격")
            if not parsed.has("ram_speed"):
                missing.append("클럭")
            if not parsed.has("ram_sticks"):
                missing.append("슬롯 구성(예: 8x2)")
            if parsed.fields["ram_gb"].meta.get("guessed"):
                q("ram", "용량 표기 '{}'는 램이 맞나요? 램 용량과 DDR 규격 알려주세요.".format(parsed.fields["ram_gb"].raw), 1)
            elif missing:
                q("ram", "램 {} 알 수 있을까요?".format(", ".join(missing)), 2)

    # ---- 저장장치 ----
    if "ssd" not in excluded and full:
        st = parsed.val("storage")
        if not st:
            q("storage", "저장장치(SSD/HDD) 종류와 용량, NVMe인지 SATA인지 알 수 있을까요?", 1)
        else:
            if any(it["kind"] == "ssd" for it in st):
                q("storage", "SSD가 NVMe(M.2)인가요, SATA인가요? 제품명도 알려주세요.", 2)
            q("storage_health", "SSD 사용량/건강 상태(CrystalDiskInfo 캡처) 보여주실 수 있을까요?", 3)

    # ---- 파워 ----
    if "psu" not in excluded and full:
        if parsed.has_flag("psu_unknown") or not parsed.has("psu_brand"):
            q("psu", "혹시 파워 브랜드/모델 알 수 있을까요?", 1)
        if not parsed.has("psu_watt"):
            q("psu_watt", "파워 정격 용량(W)이랑 80PLUS 등급 알 수 있을까요?", 1)
        elif not parsed.has("psu_grade"):
            q("psu_grade", "파워 80PLUS 등급과 구매 시기 알 수 있을까요?", 2)

    # ---- 보드 ----
    if full and not parsed.has("board"):
        q("board", "메인보드 모델명(칩셋 포함) 알 수 있을까요?", 2)

    # ---- 쿨러/케이스 ----
    if full and "cooler" not in excluded and not parsed.has("cooler"):
        q("cooler", "CPU 쿨러는 정품인가요, 공랭/수랭 중 어떤 건가요?", 3)
    if parsed.has_flag("cooler_noise"):
        q("noise", "쿨러/팬 소음이 어느 정도인지(녹음이나 영상) 확인 가능할까요? 어느 부품 소리인가요?", 2)

    # ---- 보증/구매 ----
    w = parsed.val("warranty")
    if not w or w.get("has") is None:
        q("warranty", "무상 AS 남은 부품이 있나요? 있다면 기간과 구매 영수증(또는 정품등록) 확인 가능한가요?", 2)
    elif w.get("has") and w.get("months_left") is None:
        q("warranty", "남은 무상 AS 기간이 정확히 얼마나 되나요? 보증서/영수증 확인 가능할까요?", 2)
    if not parsed.has("age_years"):
        q("age", "구매 시기와 사용 기간 알 수 있을까요?", 3)

    # ---- 위험 신호별 확인 ----
    if parsed.has_flag("mining"):
        q("mining", "채굴 이력이 있다고 보이는데, 얼마나 오래/어떤 세팅(언더볼팅, 팬 교체 등)으로 돌렸는지 알 수 있을까요?", 1)
    if parsed.has_flag("refurb"):
        q("refurb", "리퍼 제품이라면 어떤 경로(제조사 리퍼/수리 교체)였는지, 보증은 어떻게 되는지 알 수 있을까요?", 2)
    if parsed.has_flag("boot_fail") or parsed.has_flag("unstable"):
        q("defect", "말씀하신 증상(부팅/꺼짐 등)이 정확히 어떤 상황에서 생기는지, 원인으로 의심되는 부품이 있는지 알 수 있을까요?", 1)
    has_receipt = bool((parsed.val("warranty") or {}).get("receipt"))
    if parsed.has_flag("no_receipt") or (parsed.has_flag("no_box") and not has_receipt):
        q("proof", "박스/영수증이 없다면 본인 구매 확인할 방법(주문내역 캡처, 시리얼 사진)이 있을까요?", 2)
    if parsed.has_flag("prepay_direct") or parsed.has_flag("prepay"):
        q("pay", "직거래로 만나서 현장 확인 후 결제하고 싶습니다. 선입금/예약금 없이 가능한가요? (택배면 안전결제 이용 가능한가요?)", 1)
    if parsed.has_flag("no_safe_pay") or parsed.has_flag("messenger"):
        q("pay2", "플랫폼 안전결제(또는 채팅 내 거래)로 진행 가능할까요? 외부 메신저 대신 이 채팅에서 얘기하고 싶습니다.", 1)
    if parsed.has_flag("no_test"):
        q("test", "구매 전에 부팅/벤치 확인(직거래 현장 테스트 또는 영상)은 가능한가요?", 1)
    if parsed.has_flag("third_party"):
        q("owner", "본인 소유 물건인지 확인하고 싶습니다. 구매 내역이나 시리얼 인증 가능할까요?", 1)
    if parsed.has_flag("urgent"):
        q("reason", "급처 사유가 있으실까요? (이사/교체 등) 가격 조정 여지는 있나요?", 3)

    # ---- 가격/거래 ----
    if not parsed.has("price"):
        q("price", "희망 가격이 정확히 얼마인가요? 네고 가능한가요?", 1)
    elif parsed.val("negotiable") is None:
        q("nego", "가격 네고 가능할까요?", 3)
    if j is not None and j.verdict == "의심" and not parsed.has_flag("prepay_direct"):
        q("scam", "직거래 장소에서 부팅/벤치 확인 후 결제하고 싶은데 가능할까요? (영상 통화로 현재 상태 확인도 가능할까요?)", 1)
    q("verify", "직거래 시 부팅, 벤치마크(3DMark 등), 온도, 시리얼 확인해볼 수 있을까요?", 3)

    qs.sort(key=lambda x: x.priority)
    return qs


def _igpu(cpu: str) -> bool:
    from .judge import cpu_has_igpu
    return cpu_has_igpu(cpu)


def seller_message(qs: List[Question], max_priority: int = 2) -> str:
    """복사해서 보낼 수 있는 한국어 메시지 템플릿."""
    chosen = [x for x in qs if x.priority <= max_priority]
    if not chosen:
        chosen = qs[:3]
    lines = ["안녕하세요, 올리신 글 보고 연락드립니다. 구매 전에 몇 가지만 여쭤봐도 될까요?"]
    for i, x in enumerate(chosen, 1):
        lines.append("{}. {}".format(i, x.text))
    lines.append("답변 주시면 확인 후 바로 연락드리겠습니다. 감사합니다.")
    return "\n".join(lines)
