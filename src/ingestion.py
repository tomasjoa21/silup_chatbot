import os
import json
import logging
import base64
import requests
from pathlib import Path
from pypdf import PdfReader
from src.config import GEMINI_API_KEY, RULES_PATH, KNOWLEDGE_PATH
from src.database import save_user_and_generate_schedule

logger = logging.getLogger(__name__)

INGESTION_SYSTEM_PROMPT = """당신은 고용노동부 실업인정 교육자료 및 수급자격증 문서 분석 전문 AI입니다.
주어진 문서(PDF 텍스트 또는 수첩 사진)를 정밀 분석하여 다음 정보를 JSON 형식으로만 추출하세요.

반드시 마크다운 코드블록(```json ... ```) 안에 유효한 JSON 형식으로만 출력하세요.

JSON 출력 포맷:
{
  "user_info": {
    "name": "수급자 성명 (미확인 시 '수급자')",
    "first_date": "1차 실업인정일 (YYYY-MM-DD 형식, 미확인 시 null)",
    "user_type": "수급자 유형 (일반수급자 / 반복수급자 / 장기수급자 / 만60세이상_장애인 중 하나)",
    "total_days": 150, // 총 소정급여일수 (숫자, 미확인 시 150)
    "center_window": "관할 센터 및 담당 창구 (예: 부산북부고용센터 3층 11번 창구, 미확인 시 null)"
  },
  "rules_update": {
    "summary": "새로 발견되거나 확인된 핵심 실업인정 지침 요약 (2~3줄)"
  },
  "confidence": "HIGH" // LOW, MEDIUM, HIGH
}
"""

def extract_text_from_pdf(pdf_path: str, max_pages: int = 25) -> str:
    """
    PDF 파일에서 텍스트를 추출 (토큰 효율을 위해 주요 페이지 위주)
    """
    try:
        reader = PdfReader(pdf_path)
        total_p = len(reader.pages)
        pages_to_read = min(total_p, max_pages)
        extracted = []
        for i in range(pages_to_read):
            text = reader.pages[i].extract_text()
            if text and text.strip():
                extracted.append(f"--- [Page {i+1}] ---\n{text.strip()}")
        return "\n\n".join(extracted)
    except Exception as e:
        logger.error(f"Failed to extract text from PDF: {e}")
        return ""

def analyze_document_content(text_content: str = "", image_bytes: bytes = None, mime_type: str = "image/jpeg") -> dict:
    """
    Gemini Flash API를 호출하여 문서 내용 분석 및 구조화 JSON 추출
    """
    if not GEMINI_API_KEY:
        return {"error": "GEMINI_API_KEY is not set"}

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent?key={GEMINI_API_KEY}"
    
    parts = []
    if text_content:
        parts.append({"text": f"[문서 본문 텍스트]\n{text_content[:20000]}"})
        
    if image_bytes:
        b64_data = base64.b64encode(image_bytes).decode("utf-8")
        parts.append({
            "inline_data": {
                "mime_type": mime_type,
                "data": b64_data
            }
        })
        parts.append({"text": "위 첨부된 실업급여 수첩/안내문 이미지에서 수급 정보와 인정 규칙을 분석해 주세요."})

    payload = {
        "system_instruction": {
            "parts": [{"text": INGESTION_SYSTEM_PROMPT}]
        },
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 1000
        }
    }

    try:
        resp = requests.post(url, json=payload, timeout=30)
        if resp.status_code == 200:
            result = resp.json()
            raw_text = result["candidates"][0]["content"]["parts"][0]["text"].strip()
            
            # JSON 블록 파싱
            if "```json" in raw_text:
                raw_text = raw_text.split("```json")[1].split("```")[0].strip()
            elif "```" in raw_text:
                raw_text = raw_text.split("```")[1].split("```")[0].strip()
                
            return json.loads(raw_text)
        else:
            logger.error(f"Gemini API returned status {resp.status_code}: {resp.text}")
            return {"error": f"API Error: {resp.status_code}"}
    except Exception as e:
        logger.error(f"Failed to analyze document with AI: {e}")
        return {"error": str(e)}

def apply_ingested_data(chat_id: int, parsed_data: dict) -> str:
    """
    AI가 분석한 결과를 바탕으로 사용자의 일정과 지식베이스를 실제로 갱신
    """
    user_info = parsed_data.get("user_info", {})
    name = user_info.get("name") or "수급자"
    first_date = user_info.get("first_date")
    user_type = user_info.get("user_type") or "일반수급자"
    total_days = int(user_info.get("total_days") or 150)
    
    if not first_date:
        return "⚠️ 문서에서 '1차 실업인정일'을 찾지 못했습니다. 날짜를 수동으로 입력해 주세요."

    # 1. DB 일정 재계산 및 갱신
    rounds_count = save_user_and_generate_schedule(
        chat_id=chat_id,
        user_name=name,
        first_date_str=first_date,
        total_days=total_days,
        user_type=user_type
    )

    # 2. 새로운 지침 요약이 있는 경우 knowledge_base.md 하단에 자동 추가
    rules_update = parsed_data.get("rules_update", {})
    summary = rules_update.get("summary")
    if summary and Path(KNOWLEDGE_PATH).exists():
        try:
            with open(KNOWLEDGE_PATH, "a", encoding="utf-8") as f:
                f.write(f"\n\n### 📝 추가 업데이트 지침 ({first_date} 수신)\n{summary}\n")
            logger.info("Knowledge base appended with new rules.")
        except Exception as e:
            logger.error(f"Failed to update knowledge base file: {e}")

    return f"✅ *{name}님의 최신 수급 정보로 일정이 자동 갱신되었습니다!*\n\n• 1차 인정일: *{first_date}*\n• 수급 유형: *{user_type}*\n• 소정일수: *{total_days}일* (총 *{rounds_count}회차* 캘린더 생성 완료)"
