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
반드시 아래에 제공된 [공식 교육자료 지식베이스]의 내용을 바탕으로만 정확하고 친절하게 답변하세요.

[답변 원칙]
1. 할루시네이션(거짓 정보/추측)은 절대 금지됩니다. 지식베이스에 근거가 없는 내용은 절대 추측하여 답변하지 마세요.
2. 실업급여 인정 규정(동일 날짜 1건 인정, 구직활동 기간 내 활동만 인정, 횟수 제한 등)은 엄격한 기준을 알기 쉽게 강조해 주세요.
3. 지식베이스에 명시되지 않은 담당자 재량 사항이나 특수한 개별 사안의 경우, "해당 사항은 관할 고용센터 3층 11번 창구(오후 3시 이후 전화 권장) 또는 고용노동부 1350으로 확인하셔야 안전합니다"라고 명확히 안내하세요.
4. 문장은 가독성 좋게 이모지와 불릿 포인트를 활용하여 한국어로 명쾌하게 작성하세요.
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

def ask_question(question: str) -> str:
    """
    질문에 대해 지식 베이스를 바탕으로 Gemini API(REST)를 호출하여 답변 생성
    """
    if not GEMINI_API_KEY:
        return "⚠️ Gemini API 키가 설정되지 않았습니다. 관리자에게 문의하세요."
        
    context = load_knowledge_context()
    system_text = SYSTEM_INSTRUCTION + f"\n\n[공식 교육자료 지식베이스]\n{context}"
    
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
            "maxOutputTokens": 1000
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
        "⚠️ 죄송합니다. AI 서비스 연결에 일시적인 지연이 발생했습니다.\n\n"
        "자주 묻는 질문은 아래 버튼을 눌러 확인하시거나, "
        "정확한 규정은 고용노동부 고객상담센터(1350) 또는 관할 고용센터(3층 11번 창구)에 문의하시기 바랍니다."
    )
