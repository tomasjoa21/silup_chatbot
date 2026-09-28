import asyncio
import logging
from datetime import datetime, date
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Bot, InlineKeyboardMarkup, InlineKeyboardButton
from src.database import get_all_users, get_user_schedules, update_reminder_flag
from src.config import TIMEZONE

logger = logging.getLogger(__name__)

async def check_and_send_reminders(bot: Bot):
    """
    매일 아침 실행되어 모든 사용자의 실업인정 일정을 검사하고 맞춤 리마인더 푸시 발송
    """
    today = date.today()
    today_str = today.strftime("%Y-%m-%d")
    users = get_all_users()
    
    logger.info(f"Checking reminders for {len(users)} users on {today_str}")
    
    for user in users:
        chat_id = user["chat_id"]
        user_name = user["user_name"] or "수급자"
        schedules = get_user_schedules(chat_id)
        
        for s in schedules:
            # 이미 지난 회차는 건너뜀
            recog_date = datetime.strptime(s["recognition_date"], "%Y-%m-%d").date()
            days_left = (recog_date - today).days
            
            round_num = s["round_num"]
            act_type = s["activity_type"]
            req_count = s["required_count"]
            attend_type = s["attendance_type"]
            
            # 1. D-7 리마인더 (구직활동 시작 안내)
            if days_left == 7 and not s["reminded_d7"]:
                msg = (
                    f"🔔 *[{round_num}차 실업인정 D-7 안내]*\n\n"
                    f"안녕하세요, {user_name}님!\n"
                    f"일주일 뒤인 *{s['recognition_date']}*은 *{round_num}차 실업인정일*입니다.\n\n"
                    f"📌 *이번 회차 숙제*: {act_type} (*{req_count}건*)\n"
                    f"🏢 *출석 방식*: {attend_type}\n\n"
                    f"💡 *팁*: 활동은 반드시 서로 다른 날에 진행하셔야 하며, 고용24 온라인 취업특강(전체 기간 중 최대 2회) 또는 워크넷 입사지원을 활용하시면 편리합니다."
                )
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📄 증빙서류 안내", callback_data="faq_doc"),
                     InlineKeyboardButton("📅 내 전체일정", callback_data="view_schedule")]
                ])
                try:
                    await bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown", reply_markup=keyboard)
                    update_reminder_flag(s["id"], "reminded_d7")
                except Exception as e:
                    logger.error(f"Failed to send D-7 reminder to {chat_id}: {e}")
                    
            # 2. D-3 리마인더 (활동 점검 & 서류 확인)
            elif days_left == 3 and not s["reminded_d3"]:
                msg = (
                    f"⏰ *[{round_num}차 실업인정 D-3 점검]*\n\n"
                    f"{user_name}님, 실업인정일이 3일 남았습니다!\n\n"
                    f"✅ *체크리스트*:\n"
                    f"1. 이번 회차에 필요한 재취업활동({act_type})을 완료하셨나요?\n"
                    f"2. 민간 취업포털(사람인 등)로 지원하셨다면 *취업활동증명서 + 채용공고문*을 캡처해 두셨나요?\n"
                    f"3. 4차 회차는 *센터 의무 방문일*이므로 신분증과 취업드림수첩을 미리 챙겨두세요!"
                )
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📄 증빙서류 가이드", callback_data="faq_doc")]
                ])
                try:
                    await bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown", reply_markup=keyboard)
                    update_reminder_flag(s["id"], "reminded_d3")
                except Exception as e:
                    logger.error(f"Failed to send D-3 reminder to {chat_id}: {e}")

            # 3. D-1 리마인더 (내일 전송 예고)
            elif days_left == 1 and not s["reminded_d1"]:
                msg = (
                    f"🚨 *[{round_num}차 실업인정 D-1 내일 전송!]*\n\n"
                    f"{user_name}님, 내일(*{s['recognition_date']}*)은 {round_num}차 실업인정일입니다!\n\n"
                    f"⏱ *전송 가능 시간*: 내일 00:00(자정) ~ 17:00(오후 5시)\n"
                    f"💡 *중요 권고*: 오후 5시 정각에 마감되므로, 서류 오류나 전산 장애를 대비해 가급적 *내일 오전 09:00 ~ 11:00*에 여유 있게 전송하세요.\n\n"
                    f"{'⚠️ *주의*: 이번 4차는 온라인 전송이 아닌 3층 11번 창구 방문 출석입니다!' if '출석' in attend_type or round_num == 4 else ''}"
                )
                try:
                    await bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
                    update_reminder_flag(s["id"], "reminded_d1")
                except Exception as e:
                    logger.error(f"Failed to send D-1 reminder to {chat_id}: {e}")

            # 4. 당일 D-0 리마인더 (오전 09:00 전송 독려)
            elif days_left == 0 and not s["reminded_d0"]:
                if "출석" in attend_type or round_num == 4:
                    msg = (
                        f"📢 *[{round_num}차 실업인정일 당일 - 센터 방문일]*\n\n"
                        f"오늘은 {round_num}차 의무 출석일입니다!\n\n"
                        f"🏢 *장소*: 관할 고용센터 3층 11번 창구 (오전 5층 접수 / 오후 3층)\n"
                        f"🎒 *준비물*: 신분증, 취업드림수첩, 구직활동 증빙자료\n\n"
                        f"꼭 방문하셔서 실업인정을 완료하세요!"
                    )
                else:
                    msg = (
                        f"🚀 *[{round_num}차 실업인정일 당일입니다!]*\n\n"
                        f"{user_name}님, 지금 바로 고용24에서 인터넷 실업인정 신청을 전송하세요!\n\n"
                        f"• 전송 마감: *오늘 17:00 (오후 5시)*\n"
                        f"• 오류 문의: 1577-7114\n"
                        f"• 지금 바로 고용24에 접속하여 임시저장 후 [제출] 버튼까지 눌렀는지 꼭 확인하세요."
                    )
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🌐 고용24 바로가기", url="https://www.work24.go.kr")]
                ])
                try:
                    await bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown", reply_markup=keyboard)
                    update_reminder_flag(s["id"], "reminded_d0")
                except Exception as e:
                    logger.error(f"Failed to send D-0 reminder to {chat_id}: {e}")

def start_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=TIMEZONE)
    # 매일 아침 09:00에 리마인더 실행
    scheduler.add_job(
        check_and_send_reminders,
        CronTrigger(hour=9, minute=0, timezone=TIMEZONE),
        args=[bot],
        id="daily_reminder_morning",
        replace_existing=True
    )
    # 오후 14:00에 미전송자 당일 재확인 (D-0 리마인더 추가 체크)
    scheduler.add_job(
        check_and_send_reminders,
        CronTrigger(hour=14, minute=0, timezone=TIMEZONE),
        args=[bot],
        id="daily_reminder_afternoon",
        replace_existing=True
    )
    scheduler.start()
    logger.info("Scheduler started successfully (Cron: 09:00 & 14:00).")
    return scheduler
