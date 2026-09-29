import json
import logging
import requests
from pathlib import Path
from src.config import GEMINI_API_KEY, KNOWLEDGE_PATH, RULES_PATH

logger = logging.getLogger(__name__)

def load_knowledge_context() -> str:
    if Path(KNOWLEDGE_PATH).exists():
        with open(KNOWLEDGE_PATH, "r", encoding="utf-8") as f:
            return f.read()
    return ""

def load_rules() -> dict:
    if Path(RULES_PATH).exists():
        with open(RULES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

SYSTEM_INSTRUCTION = """당신은 고용노동부 실업급여 및 실업인정 전담 전문 AI 비서입니다.
제공된 [공식 교육자료 지식베이스]와 [수급자 개인 일정 및 수급 정보]를 바탕으로 정확하고 친절하며 전문성 있게 답변하세요.

[답변 원칙]
1. 할루시네이션(거짓 정보/추측)은 절대 금지됩니다. 지식베이스 및 제공된 수급자 정보에 근거하여 사실에 기반해 답변하세요.
2. [개인 일정 및 회차 질문 ("2회차 신청일이 언제야?", "회차별로 정리해줘", "내 인정일 보여줘" 등)]:
   - 제공된 [수급자 개인 일정 및 수급 정보]를 조회하여 사용자가 묻는 회차의 실업인정일, 출석 형태(방문/온라인), 필수 활동 요건(건수 및 유형)을 명확하게 답변해 주세요.
   - "회차별로 정리해줘"나 "내 일정 보여줘"를 요청받으면 1차부터 마지막 차수까지 한눈에 보기 쉽게 불릿 포인트로 일목요연하게 정리해 주세요.
3. [입금 및 지급 관련 질문 ("첫 입금은 1회차를 받은거야?", "오늘 입금된건 1회차인거야?" 등)]:
   - 챗봇은 금융기관이나 고용보험 전산망에 직접 접속하여 실시간 통장 잔고/입금 내역을 조회할 수는 없음을 정중히 밝히되,
   - 고용노동부 실업급여 규정에 따라 다음과 같이 명확히 안내하세요:
     ① 실업인정 신청 완료 후 통상 다음 날(익일) ~ 3영업일 이내에 계좌로 입금됩니다.
     ② **1회차 첫 입금의 비밀(8일분)**: 수급자격 신청일로부터 7일간은 법정 대기기간(미지급)이므로, 1차 실업인정 후 첫 입금되는 금액은 한 달 치가 아닌 **최초 8일분의 구직급여**만 입금되는 것이 정상입니다. (따라서 첫 입금액이 적더라도 1회차가 맞습니다)
     ③ 2회차부터는 통상 28일분(4주)씩 정상 입금됩니다.
     ④ 본인의 정확한 지급 심사 및 입금 상세 내역은 **고용24(work24.go.kr) > 마이페이지 > 실업급여 > '실업인정 신청내역/지급내역'**에서 확인 가능함을 안내하세요.
4. 실업급여 인정 규정(동일 날짜 1건 인정, 구직활동 기간 내 활동만 인정, 횟수 제한 등)은 엄격한 기준을 알기 쉽게 강조해 주세요.
5. [답변이 어렵거나 불확실한 경우 (Fallback 원칙)]:
   - 질문이 너무 모호하거나, 지식베이스 및 일정표에 명확한 근거가 없는 특수한 개별 사안의 경우, 절대 거짓 추측하지 말고 다음 3단계로 친절히 응대하세요:
     ① 솔직한 안내: "죄송합니다, 해당 질문에 대해서는 공식 교육자료 및 규정집에서 명확한 근거를 확인하기 어렵습니다. 실업급여는 잘못된 안내로 급여 미지급 등의 불이익을 받으실 수 있어 신중해야 합니다. 😥"
     ② 질문 재작성 팁: "혹시 이런 내용이 궁금하셨다면 조금 더 구체적으로 질문해 주세요. (예: '면접 증빙서류는 어떻게 내나요?', '인정일 변경 방법은?', '온라인 특강은 몇 번 인정되나요?')"
     ③ 공식 안전 창구 안내: "특이 사안이나 담당자 재량 사항은 관할 고용센터(부산북부고용센터 3층 11번 창구, 오후 3시 이후 통화 권장) 또는 고용노동부 1350으로 문의하시는 것이 가장 안전합니다."
6. [실업급여와 무관한 질문인 경우 (날씨, 잡담, 주식 등)]:
   - "저는 고용노동부 실업급여 및 실업인정 전담 전문 AI 비서입니다. 😊\n실업급여 수급 자격, 인정일 일정, 구직활동 증빙 서류, 입금 규정 등 실업급여에 관해 궁금하신 점을 편하게 질문해 주세요!"라고 정중하고 친절하게 역할을 안내하세요.
7. 문장은 가독성 좋게 이모지와 불릿 포인트를 활용하여 한국어로 명쾌하고 따뜻하게 작성하세요.
"""

# 빠른 응답(토큰 0개 소모) 템플릿
QUICK_FAQ_ANSWERS = {
    "faq_doc": """📄 *구직활동 증빙 서류 제출 방법 가이드*

1️⃣ *고용24 (워크넷) 입사지원 시*:
• 별도 서류 제출 불필요! 
• 실업인정 신청서에서 [고용24 활동내역 불러오기] 클릭만 하면 전산 자동 연동됩니다.

2️⃣ *사람인 / 잡코리아 / 알바몬 등 민간 취업포털*:
• *2가지 서류 필수 첨부*:
  ① *취업활동 증명서* (해당 사이트 마이페이지에서 발급/캡처)
  ② *채용공고문 캡처본* (모집기간, 담당직무 확인용)

3️⃣ *면접 응시 시*:
• 취업드림수첩 구직활동내역란에 *면접관 인사담당자 직인(도장)* 날인 필수!
• 도장이 없으면: *서명 + 인사담당자 명함* 함께 제출
• (본인이 수첩에 직접 작성하거나 단순 서명만 받아오면 불인정)

4️⃣ *이메일 지원*:
• 채용공고문 + 보낸 메일함 캡처 (보낸 날짜/시간 필수 표시)""",

    "faq_change": """🔄 *실업인정일 착오 변경 규정*

• *사유 무관*: 개인 사정, 깜빡함, 여행 등 이유 불문하고 *수급기간 중 단 1회* 허용!
• *필수 절차*: 원래 인정일로부터 *14일(2주) 이내에 관할 고용센터에 신분증 지참하여 직접 방문*해야 변경 가능.
• ⚠️ 2주가 지나거나 2회째 착오 시에는 해당 회차 급여가 영구 소멸되니 주의하세요!
*(예비군, 질병 입원 등 부득이한 사유는 증빙자료 제출 시 별도 사유변경 가능)*""",

    "faq_window": """🏢 *고용센터 방문 및 창구 안내*

• *담당 창구*: 3층 11번 창구 (실업급여팀)
• *방문 시간*:
  - 오전 (09:30~12:00): 5층에서 서류 접수
  - 오후 (12:00~18:00): 3층 담당 창구에서 직접 상담
• *전화 문의 팁*:
  - 오전에는 방문자가 많아 인턴 메모 접수만 가능
  - 담당자와 통화하려면 *오후 3시 이후* 연락 권장!
• *대표 전화*:
  - 1350: 고용노동부 상담센터 (급여/규정)
  - 1577-7114: 고용24 전산오류 문의""",

    "faq_time": """⏰ *실업인정 신청 전송 시간 안내*

• *전송 가능 시간*: 실업인정일 당일 *00:00(자정) ~ 17:00(오후 5시)*
• ⚠️ 오후 5시 정각에 시스템이 마감되므로 1분이라도 늦으면 전송 불가!
• 💡 *강력 권장*: 가급적 *오전 09:00~11:00* 사이에 미리 전송하세요.
  (오전에 전송해야 서류 누락이나 오류 발생 시 담당자 확인 후 오후에 보완 전송이 가능합니다.)"""
}

# 호출할 모델 우선순위 리스트 (가장 빠르고 안정적인 최신 Flash 모델)
FALLBACK_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-3-flash-preview",
    "gemini-3.8-flash",
    "gemini-3.5-flash"
]

def ask_question(question: str, user_context: str = "") -> str:
    """
    질문에 대해 지식 베이스와 개인 수급 정보를 바탕으로 Gemini API(REST)를 호출하여 답변 생성
    """
    if not GEMINI_API_KEY:
        return "⚠️ Gemini API 키가 설정되지 않았습니다. 관리자에게 문의하세요."
        
    context = load_knowledge_context()
    system_text = SYSTEM_INSTRUCTION + f"\n\n[공식 교육자료 지식베이스]\n{context}"
    if user_context:
        system_text += f"\n\n[수급자 개인 일정 및 수급 정보]\n{user_context}"
    
    payload = {
        "system_instruction": {
            "parts": [{"text": system_text}]
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": question}]
            }
        ],
        "generationConfig": {
            "temperature": 0.2, # 환각 방지를 위한 낮은 온도
            "maxOutputTokens": 1500
        }
    }
    
    for model_name in FALLBACK_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
        try:
            resp = requests.post(url, json=payload, timeout=20)
            if resp.status_code == 200:
                result = resp.json()
                text = result["candidates"][0]["content"]["parts"][0]["text"]
                return text.strip()
            elif resp.status_code == 503:
                logger.warning(f"Model {model_name} is overloaded (503), trying next model...")
                continue
            else:
                logger.error(f"Model {model_name} returned status {resp.status_code}: {resp.text}")
                continue
        except Exception as e:
            logger.error(f"Error calling {model_name}: {e}")
            continue
            
    return (
        "⚠️ *AI 서비스 연결에 일시적인 지연이 발생했습니다.*\n\n"
        "잠시 후 질문을 다시 보내주시거나, 아래 바로가기 버튼을 통해 공식 규정을 즉시 확인하실 수 있습니다.\n\n"
        "💡 *급한 문의가 있으신 경우*:\n"
        "• 🏢 *관할 고용센터*: 부산북부고용센터 3층 11번 창구 (오후 3시 이후 통화 권장)\n"
        "• 📞 *고용노동부 상담센터*: 국번 없이 1350\n"
        "• 🌐 *고용24 전산오류 문의*: 1577-7114"
    )
