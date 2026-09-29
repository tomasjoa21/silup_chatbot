import logging
import io
from datetime import datetime, date, timedelta
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)

from src.config import TELEGRAM_BOT_TOKEN
from src.database import (
    init_db, save_user_and_generate_schedule, get_user,
    get_user_schedules, get_next_schedule, update_user_google_email
)
from src.calendar_gen import generate_ics_calendar
from src.scheduler import start_scheduler
from src.qa_engine import ask_question, QUICK_FAQ_ANSWERS

# 로깅 설정
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 대화 위저드 상태
SET_DATE, SET_TYPE, SET_DAYS = range(3)
WAIT_EMAIL = 10

# 로컬 전용 실물 데이터가 있는 경우 로드 (깃허브 오픈소스 배포 시 제외됨)
try:
    from src.real_data import REAL_USER_DATA, bind_real_user_data
    HAS_LOCAL_SEED = True
except ImportError:
    HAS_LOCAL_SEED = False
    REAL_USER_DATA = None
    bind_real_user_data = None

async def safe_reply(message, text: str, reply_markup=None):
    """
    마크다운 특수문자 오류 방지를 위해 실패 시 플레인 텍스트로 폴백 전송
    """
    try:
        return await message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)
    except Exception as e:
        logger.warning(f"Markdown send failed ({e}), falling back to plain text.")
        return await message.reply_text(text, parse_mode=None, reply_markup=reply_markup)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    # 로컬 시드 데이터가 있는 경우 자동 바인딩
    if HAS_LOCAL_SEED and bind_real_user_data:
        bind_real_user_data(chat_id)
        
    user = get_user(chat_id)
    
    if user:
        # 조기재취업 1/2 시점 및 만료일 계산
        first_d = datetime.strptime(user["first_recognition_date"], "%Y-%m-%d").date()
        tot_days = user["total_benefit_days"]
        half_d = first_d + timedelta(days=tot_days // 2)
        
        schedules = get_user_schedules(chat_id)
        last_date = schedules[-1]["recognition_date"] if schedules else (first_d + timedelta(days=tot_days)).strftime("%Y-%m-%d")
        
        welcome_text = (
            f"👋 안녕하세요, *{user['user_name']}님*!\n"
            f"고용노동부 실업급여 전문 전담 비서봇입니다. 😊\n\n"
            f"현재 등록된 수급 정보에 따라 모든 일정과 리마인더가 관리되고 있습니다.\n\n"
            f"📋 *[{user['user_name']}님의 수급 정보]*\n"
            f"• 수급 유형: *{user['user_type']}*\n"
            f"• 관할 창구: *{user['center_window']}*\n"
            f"• 1차 인정일: *{user['first_recognition_date']}*\n"
            f"• 소정급여일수: *{user['total_benefit_days']}일* (만료일: {last_date})\n"
            f"• 조기재취업 1/2 시점: *{half_d.strftime('%Y-%m-%d')}*\n\n"
            f"아래 메뉴를 눌러 전체 일정을 조회하시거나, 궁금한 점을 언제든 물어보세요!"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 내 전체 일정 조회", callback_data="view_status"),
             InlineKeyboardButton("📅 구글/스마트폰 캘린더에 일정 등록", callback_data="download_ics")],
            [InlineKeyboardButton("📄 증빙서류 제출 가이드", callback_data="faq_doc"),
             InlineKeyboardButton("❓ 자주 묻는 질문 (FAQ)", callback_data="view_faq")]
        ])
        await safe_reply(update.message, welcome_text, reply_markup=keyboard)
        return ConversationHandler.END
    else:
        welcome_text = (
            "👋 안녕하세요! *고용노동부 실업급여 전문 전담 비서봇*입니다.\n\n"
            "실업급여 인정 일정과 행동 지침을 하루도 놓치지 않도록 관리해 드립니다.\n"
            "먼저 본인의 *1차 실업인정일 (교육받은 날)*을 입력해 주세요.\n"
            "형식: `YYYY-MM-DD` (예: `2026-09-28`)"
        )
        await safe_reply(update.message, welcome_text)
        return SET_DATE

async def setup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚙️ *실업급여 수급 정보 설정*\n\n"
        "본인의 *1차 실업인정일*을 입력해 주세요.\n"
        "형식: `YYYY-MM-DD` (예: `2026-09-28`)",
        parse_mode="Markdown"
    )
    return SET_DATE

async def received_first_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        dt = datetime.strptime(text, "%Y-%m-%d").date()
        context.user_data["first_date"] = text
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("일반 수급자", callback_data="type_일반수급자"),
             InlineKeyboardButton("반복 수급자", callback_data="type_반복수급자")],
            [InlineKeyboardButton("장기 수급자 (210일↑)", callback_data="type_장기수급자"),
             InlineKeyboardButton("만 60세 이상 / 장애인", callback_data="type_만60세이상_장애인")]
        ])
        await update.message.reply_text(
            f"✅ 1차 인정일: *{text}*\n\n"
            "취업드림수첩 1페이지(수급자격증)에 표기된 본인의 *수급자 유형*을 선택해 주세요.",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
        return SET_TYPE
    except ValueError:
        await update.message.reply_text(
            "⚠️ 날짜 형식이 올바르지 않습니다.\n"
            "반드시 `YYYY-MM-DD` 형식으로 다시 입력해 주세요 (예: `2026-09-28`).",
            parse_mode="Markdown"
        )
        return SET_DATE

async def received_user_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_type = query.data.replace("type_", "")
    context.user_data["user_type"] = user_type
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("120일", callback_data="days_120"),
         InlineKeyboardButton("150일", callback_data="days_150"),
         InlineKeyboardButton("180일", callback_data="days_180")],
        [InlineKeyboardButton("210일", callback_data="days_210"),
         InlineKeyboardButton("240일", callback_data="days_240"),
         InlineKeyboardButton("270일", callback_data="days_270")]
    ])
    await query.edit_message_text(
        f"✅ 수급 유형: *{user_type}*\n\n"
        "수급자격증에 적힌 본인의 *총 소정급여일수*를 선택해 주세요.",
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    return SET_DAYS

async def received_total_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    days = int(query.data.replace("days_", ""))
    chat_id = update.effective_chat.id
    user_name = update.effective_user.first_name or "수급자"
    first_date = context.user_data.get("first_date", "2026-09-28")
    user_type = context.user_data.get("user_type", "일반수급자")
    
    # DB 저장 및 전체 회차 자동 생성
    total_rounds = save_user_and_generate_schedule(
        chat_id=chat_id,
        user_name=user_name,
        first_date_str=first_date,
        total_days=days,
        user_type=user_type
    )
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 내 전체 일정 확인", callback_data="view_status")],
        [InlineKeyboardButton("📅 구글/스마트폰 캘린더에 일정 등록", callback_data="download_ics")]
    ])
    
    await query.edit_message_text(
        f"🎉 *설정이 완료되었습니다!*\n\n"
        f"• 사용자명: *{user_name}*\n"
        f"• 1차 인정일: *{first_date}*\n"
        f"• 수급 유형: *{user_type}*\n"
        f"• 소정급여일수: *{days}일* (총 *{total_rounds}회차* 자동 생성 완료)\n\n"
        f"이제 각 회차마다 *D-7, D-3, D-1, 당일 오전 9시*에 스마트폰 푸시 알림으로 미리 리마인드해 드립니다!",
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    return ConversationHandler.END

async def cancel_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("설정이 취소되었습니다.")
    return ConversationHandler.END

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    target_msg = update.effective_message
    
    # 미등록 시 실물 데이터 자동 바인딩
    user = get_user(chat_id)
    if not user:
        bind_real_user_data(chat_id)
        user = get_user(chat_id)
        
    schedules = get_user_schedules(chat_id)
    today_str = date.today().strftime("%Y-%m-%d")
    next_s = get_next_schedule(chat_id, today_str)
    
    first_d = datetime.strptime(user["first_recognition_date"], "%Y-%m-%d").date()
    tot_days = user["total_benefit_days"]
    half_d = first_d + timedelta(days=tot_days // 2)
    last_date = schedules[-1]["recognition_date"] if schedules else (first_d + timedelta(days=tot_days)).strftime("%Y-%m-%d")
    
    text = f"📋 *[{user['user_name']}님의 전체 실업인정 일정표]*\n"
    text += f"• 관할: {user['center_window']}\n"
    text += f"• 소정급여일수: {user['total_benefit_days']}일 (만료: {last_date})\n"
    text += f"• 조기재취업 1/2 시점: *{half_d.strftime('%Y-%m-%d')}*\n\n"
    
    if next_s:
        recog_d = datetime.strptime(next_s["recognition_date"], "%Y-%m-%d").date()
        d_day = (recog_d - date.today()).days
        d_str = "D-Day (오늘!)" if d_day == 0 else f"D-{d_day}"
        text += f"🔥 *다가오는 회차: {next_s['round_num']}차 실업인정 ({d_str})*\n"
        text += f"  - 인정일: *{next_s['recognition_date']}* (00:00~17:00)\n"
        text += f"  - 출석 형태: *{next_s['attendance_type']}*\n"
        text += f"  - 요구 활동: *{next_s['activity_type']}* ({next_s['required_count']}건)\n"
        text += f"  - 활동 인정 기간: {next_s['period_start']} ~ {next_s['period_end']}\n"
        if next_s["note"]:
            text += f"  - 💡 주의: {next_s['note']}\n"
        text += "\n"
        
    text += "*[전체 타임라인]*\n"
    for s in schedules:
        r_num = s["round_num"]
        r_date = s["recognition_date"]
        r_act = s["activity_type"]
        r_type = "방문" if "출석" in s["attendance_type"] else "온라인"
        is_past = "✅ " if s["recognition_date"] < today_str else "⏳ "
        text += f"{is_past}*{r_num}차*: {r_date} ({r_type}) - {r_act}\n"
        
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 구글/스마트폰 캘린더에 일정 등록", callback_data="download_ics")],
        [InlineKeyboardButton("❓ 질문하기 (FAQ)", callback_data="view_faq")]
    ])
    await safe_reply(target_msg, text, reply_markup=keyboard)

from src.calendar_gen import generate_ics_calendar, get_google_calendar_url
async def send_calendar_with_options(target_msg, chat_id: int):
    user = get_user(chat_id)
    if not user and HAS_LOCAL_SEED and bind_real_user_data:
        bind_real_user_data(chat_id)
        user = get_user(chat_id)
        
    today_str = date.today().strftime("%Y-%m-%d")
    next_s = get_next_schedule(chat_id, today_str)
    
    # 등록된 구글 계정 확인
    google_email = ""
    if user:
        try:
            google_email = user["google_email"] if user["google_email"] else ""
        except (KeyError, IndexError):
            google_email = ""
    
    ics_data = generate_ics_calendar(chat_id)
    bio = io.BytesIO(ics_data)
    bio.name = f"silup_schedule_{chat_id}.ics"
    
    keyboard_buttons = []
    if next_s:
        gcal_url = get_google_calendar_url(
            round_num=next_s["round_num"],
            recog_date_str=next_s["recognition_date"],
            attend_type=next_s["attendance_type"],
            act_type=next_s["activity_type"],
            req_count=next_s["required_count"],
            note=next_s["note"] or "",
            google_email=google_email
        )
        keyboard_buttons.append([InlineKeyboardButton(f"🔗 {next_s['round_num']}차 구글 캘린더 바로 등록", url=gcal_url)])
    
    # 구글 계정 이메일 등록/변경 버튼
    email_btn_text = f"✉️ 구글 계정 변경 ({google_email})" if google_email else "✉️ 구글 계정 연동 (Gmail)"
    keyboard_buttons.append([InlineKeyboardButton(email_btn_text, callback_data="change_google_email")])
    keyboard_buttons.append([InlineKeyboardButton("📊 내 전체 일정표 보기", callback_data="view_status")])
    keyboard = InlineKeyboardMarkup(keyboard_buttons)
    
    next_round_str = f"{next_s['round_num']}차({next_s['recognition_date']})" if next_s else "인정일"
    email_status_str = f"• 연동 계정: `{google_email}` (해당 계정으로 즉시 오픈)\n" if google_email else "• 연동 계정: *미등록* (아래 버튼으로 Gmail 등록 시 해당 계정으로 직행)\n"
    
    caption_text = (
        "📅 *실업급여 전체 실업인정 캘린더 등록*\n\n"
        f"{email_status_str}"
        "✨ *동일 일정 중복 방지 & 자동 편집(갱신)*\n"
        "• 전송된 `.ics` 파일을 클릭하여 스마트폰(구글/삼성/애플) 캘린더에 추가하세요.\n"
        "• **이미 등록된 일정이 있더라도 중복 생성되지 않고 최신 일정 및 지침으로 자동 갱신(편집)**됩니다.\n\n"
        f"💡 다가오는 *{next_round_str}*만 구글 캘린더 웹에서 즉시 추가하시려면 아래 버튼을 누르세요!"
    )
    
    await target_msg.reply_document(
        document=bio,
        caption=caption_text,
        parse_mode="Markdown",
        reply_markup=keyboard
    )

async def calendar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    target_msg = update.effective_message
    await send_calendar_with_options(target_msg, chat_id)

async def start_email_change(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    target_msg = update.effective_message
    if update.callback_query:
        await update.callback_query.answer()
        
    # /email 명령어에 직접 인자를 넘긴 경우 (예: /email test@gmail.com)
    if context.args:
        new_email = context.args[0].strip()
        if "@" in new_email and "." in new_email:
            update_user_google_email(chat_id, new_email)
            await safe_reply(
                target_msg,
                f"✅ 연동 구글 계정이 *{new_email}*로 성공적으로 변경되었습니다!\n"
                "다음 수정 전까지 영구적으로 유지되며, 캘린더 링크를 누르면 항상 이 계정으로 열립니다. 😊"
            )
            await send_calendar_with_options(target_msg, chat_id)
            return ConversationHandler.END
        else:
            await safe_reply(target_msg, "⚠️ 올바른 이메일 형식이 아닙니다. (예: `example@gmail.com`)")
            return WAIT_EMAIL

    user = get_user(chat_id)
    current_email = "없음 (미등록)"
    if user:
        try:
            if user["google_email"]:
                current_email = user["google_email"]
        except (KeyError, IndexError):
            pass
            
    text = (
        "✉️ *구글 캘린더 연동 계정 설정/변경*\n\n"
        f"• 현재 등록된 계정: `{current_email}`\n\n"
        "캘린더 일정을 연동할 본인의 *구글 이메일(Gmail 주소)*을 채팅창에 입력해 주세요.\n"
        "(예: `example@gmail.com`)\n\n"
        "취소하시려면 /cancel 을 입력하세요."
    )
    await safe_reply(target_msg, text)
    return WAIT_EMAIL

async def received_google_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    target_msg = update.effective_message
    text = update.message.text.strip()
    
    if text.startswith("/cancel"):
        await update.message.reply_text("구글 계정 변경이 취소되었습니다.")
        return ConversationHandler.END
        
    if "@" not in text or "." not in text or len(text) < 5:
        await update.message.reply_text(
            "⚠️ 올바른 이메일 형식이 아닙니다. 다시 입력해 주세요.\n(예: `abc@gmail.com` / 취소: /cancel)",
            parse_mode="Markdown"
        )
        return WAIT_EMAIL
        
    update_user_google_email(chat_id, text)
    await update.message.reply_text(
        f"🎉 *구글 계정 이메일이 저장되었습니다!*\n\n"
        f"• 연동 계정: `{text}`\n\n"
        "다음 번 수정 전까지 영구적으로 유지되며, 캘린더 등록 시 항상 이 계정으로 바로 열립니다. 😊",
        parse_mode="Markdown"
    )
    await send_calendar_with_options(target_msg, chat_id)
    return ConversationHandler.END

async def cancel_email_change(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("구글 계정 변경이 취소되었습니다.")
    return ConversationHandler.END

async def faq_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target_msg = update.effective_message
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 증빙 서류 제출 방법", callback_data="faq_doc"),
         InlineKeyboardButton("🔄 실업인정일 착오 변경", callback_data="faq_change")],
        [InlineKeyboardButton("⏰ 전송 시간 및 마감", callback_data="faq_time"),
         InlineKeyboardButton("🏢 담당 창구 및 연락처", callback_data="faq_window")]
    ])
    await safe_reply(
        target_msg,
        "💡 *자주 묻는 규정 및 지침 가이드*\n\n"
        "궁금하신 버튼을 누르면 공식 지침을 즉시 확인하실 수 있습니다.\n"
        "그 외 세부 질문은 채팅창에 바로 질문해 주세요!",
        reply_markup=keyboard
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    chat_id = update.effective_chat.id
    await query.answer()
    target_msg = update.effective_message
    
    if data in QUICK_FAQ_ANSWERS:
        await safe_reply(target_msg, QUICK_FAQ_ANSWERS[data])
    elif data == "view_status":
        await status_cmd(update, context)
    elif data == "download_ics":
        await send_calendar_with_options(target_msg, chat_id)
    elif data == "view_faq":
        await faq_cmd(update, context)
    elif data == "start_setup":
        await safe_reply(
            target_msg,
            "⚙️ 수급 정보를 재설정합니다.\n"
            "본인의 *1차 실업인정일*을 입력해 주세요 (형식: `YYYY-MM-DD`):"
        )
        return SET_DATE
    elif data == "apply_ingestion":
        parsed = context.user_data.get("pending_ingestion")
        if parsed:
            result_msg = apply_ingested_data(chat_id, parsed)
            await safe_reply(target_msg, result_msg)
            # 갱신된 일정 보여주기
            await status_cmd(update, context)
            context.user_data.pop("pending_ingestion", None)
        else:
            await safe_reply(target_msg, "⚠️ 반영할 분석 데이터가 만료되었습니다. 파일을 다시 전송해 주세요.")
    elif data == "cancel_ingestion":
        context.user_data.pop("pending_ingestion", None)
        await safe_reply(target_msg, "❌ 문서 분석 반영이 취소되었습니다. 기존 정보가 그대로 유지됩니다.")

def build_user_context(chat_id: int) -> str:
    user = get_user(chat_id)
    if not user:
        return ""
    schedules = get_user_schedules(chat_id)
    first_d = datetime.strptime(user["first_recognition_date"], "%Y-%m-%d").date()
    tot_days = user["total_benefit_days"]
    half_d = first_d + timedelta(days=tot_days // 2)
    last_date = schedules[-1]["recognition_date"] if schedules else (first_d + timedelta(days=tot_days)).strftime("%Y-%m-%d")
    
    ctx = f"• 수급자 성명: {user['user_name']}\n"
    ctx += f"• 수급 유형: {user['user_type']}\n"
    ctx += f"• 관할 창구: {user['center_window']}\n"
    ctx += f"• 1차 실업인정일: {user['first_recognition_date']}\n"
    ctx += f"• 소정급여일수: {user['total_benefit_days']}일 (만료일: {last_date})\n"
    ctx += f"• 조기재취업 1/2 시점: {half_d.strftime('%Y-%m-%d')}\n"
    ctx += f"• 오늘 기준 일자: {date.today().strftime('%Y-%m-%d')}\n"
    
    if HAS_LOCAL_SEED and REAL_USER_DATA and "daily_benefit" in REAL_USER_DATA:
        daily = REAL_USER_DATA["daily_benefit"]
        ctx += f"• 1일 구직급여액: {daily:,.2f}원 (1회차 8일분 급여: 약 {int(daily * 8):,}원, 28일분 급여: 약 {int(daily * 28):,}원)\n"
        
    if schedules:
        ctx += "\n[전체 회차별 실업인정 상세 일정표]\n"
        for s in schedules:
            ctx += (
                f"- {s['round_num']}차: 인정일 {s['recognition_date']} "
                f"(인정기간: {s['period_start']}~{s['period_end']}) | "
                f"출석방식: {s['attendance_type']} | "
                f"활동: {s['activity_type']} ({s['required_count']}건) | "
                f"메모: {s['note'] or '없음'}\n"
            )
    return ctx

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    raw_text = update.message.text.strip()
    
    # 챗봇 타이핑 액션
    await update.message.chat.send_action("typing")
    
    # 미등록 시 자동 바인딩 시도
    user = get_user(chat_id)
    if not user and HAS_LOCAL_SEED and bind_real_user_data:
        bind_real_user_data(chat_id)
        user = get_user(chat_id)

    # 사용자 개인 컨텍스트 구성
    user_context = build_user_context(chat_id)

    # Gemini API + 지식베이스 + 개인 수급 정보 종합 질의응답
    answer = ask_question(raw_text, user_context=user_context)
    
    # 상황별 인라인 퀵 버튼 구성 (UX 최적화)
    reply_markup = None
    is_fallback = any(w in answer for w in ["죄송합니다", "지연이 발생했습니다", "확인하기 어렵습니다", "명확한 근거를"])
    is_schedule_query = any(k in raw_text for k in ["일정", "회차", "인정일", "스케줄", "신청일", "날짜", "언제"])
    
    if is_schedule_query:
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 1~11차 전체 타임라인 보기", callback_data="view_status")],
            [InlineKeyboardButton("📅 구글/스마트폰 캘린더에 일정 등록", callback_data="download_ics")]
        ])
    elif is_fallback:
        # 질문에 바로 답하지 못했을 때 헤매지 않도록 공식 핵심 바로가기 버튼 제공
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📄 증빙서류 제출 방법", callback_data="faq_doc"),
             InlineKeyboardButton("🔄 실업인정일 착오 변경", callback_data="faq_change")],
            [InlineKeyboardButton("📊 내 전체 일정 확인", callback_data="view_status"),
             InlineKeyboardButton("❓ 자주 묻는 질문 (FAQ)", callback_data="view_faq")]
        ])
    else:
        # 일반 질문 답변 후에도 언제든 일정을 볼 수 있는 보조 퀵 버튼
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 내 전체 일정 조회", callback_data="view_status"),
             InlineKeyboardButton("❓ 자주 묻는 질문 (FAQ)", callback_data="view_faq")]
        ])
        
    await safe_reply(update.message, answer, reply_markup=reply_markup)

from src.ingestion import extract_text_from_pdf, analyze_document_content, apply_ingested_data
import tempfile

async def handle_document_or_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    사용자가 PDF나 수첩 사진을 전송했을 때 자동 감지하여 AI 분석 수행
    """
    target_msg = update.effective_message
    chat_id = update.effective_chat.id
    
    await safe_reply(target_msg, "📥 *새로운 교육자료/수첩 문서를 수신했습니다!*\nAI가 내용을 정밀 분석하고 있습니다. 잠시만 기다려 주세요 (약 5~10초)...")
    await target_msg.chat.send_action("typing")
    
    parsed = None
    
    # 1. PDF 문서인 경우
    if update.message.document:
        doc = update.message.document
        if doc.file_name.lower().endswith(".pdf"):
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                file_obj = await doc.get_file()
                await file_obj.download_to_drive(tmp.name)
                tmp_path = tmp.name
                
            pdf_text = extract_text_from_pdf(tmp_path)
            parsed = analyze_document_content(text_content=pdf_text)
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        else:
            await safe_reply(target_msg, "⚠️ 현재는 PDF 문서 및 사진(JPG, PNG)만 자동 분석을 지원합니다.")
            return
            
    # 2. 사진 이미지인 경우
    elif update.message.photo:
        photo = update.message.photo[-1] # 가장 해상도 높은 사진
        file_obj = await photo.get_file()
        img_bytes = await file_obj.download_as_bytearray()
        parsed = analyze_document_content(image_bytes=bytes(img_bytes), mime_type="image/jpeg")

    if not parsed or "error" in parsed:
        err_msg = parsed.get("error", "알 수 없는 오류") if parsed else "분석 실패"
        await safe_reply(target_msg, f"⚠️ 문서 분석 중 오류가 발생했습니다: {err_msg}\n자료가 선명한지 확인 후 다시 시도해 주세요.")
        return

    # 분석 결과 캐싱 (승인 대기)
    context.user_data["pending_ingestion"] = parsed
    
    user_info = parsed.get("user_info", {})
    rules_update = parsed.get("rules_update", {})
    
    preview_text = (
        "🔍 *[새 문서 분석 결과 요약]*\n\n"
        f"• 감지된 성명: *{user_info.get('name', '수급자')}*\n"
        f"• 1차 실업인정일: *{user_info.get('first_date', '미확인')}*\n"
        f"• 수급 유형: *{user_info.get('user_type', '일반수급자')}*\n"
        f"• 소정급여일수: *{user_info.get('total_days', 150)}일*\n"
        f"• 관할 창구: *{user_info.get('center_window', '고용센터')}*\n\n"
        f"📌 *확인된 최신 지침*:\n{rules_update.get('summary', '특이 지침 없음')}\n\n"
        "이 분석 결과를 내 일정과 비서봇 지식 베이스에 반영하시겠습니까?"
    )
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ 내 일정 & 지식에 반영하기", callback_data="apply_ingestion"),
         InlineKeyboardButton("❌ 취소", callback_data="cancel_ingestion")]
    ])
    await safe_reply(target_msg, preview_text, reply_markup=keyboard)

def main():
    init_db()
    
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is missing!")
        return
        
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # 대화 위저드 핸들러
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start_cmd),
            CommandHandler("setup", setup_cmd),
            CallbackQueryHandler(received_first_date, pattern="^start_setup$")
        ],
        states={
            SET_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_first_date)],
            SET_TYPE: [CallbackQueryHandler(received_user_type, pattern="^type_")],
            SET_DAYS: [CallbackQueryHandler(received_total_days, pattern="^days_")]
        },
        fallbacks=[CommandHandler("cancel", cancel_setup)],
        allow_reentry=True
    )
    
    # 구글 계정 이메일 변경 대화 핸들러
    email_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("email", start_email_change),
            CallbackQueryHandler(start_email_change, pattern="^change_google_email$")
        ],
        states={
            WAIT_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_google_email)]
        },
        fallbacks=[CommandHandler("cancel", cancel_email_change)],
        allow_reentry=True
    )
    
    app.add_handler(conv_handler)
    app.add_handler(email_conv_handler)
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("calendar", calendar_cmd))
    app.add_handler(CommandHandler("faq", faq_cmd))
    
    # 인라인 버튼 핸들러
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    # PDF 문서 및 사진 자동 수신 핸들러 추가
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_document_or_photo))
    
    # 일반 텍스트 질문 핸들러
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    
    async def on_startup(application: Application):
        start_scheduler(application.bot)
        logger.info("Bot started and scheduler initialized successfully.")

    app.post_init = on_startup

    logger.info("Bot is running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
