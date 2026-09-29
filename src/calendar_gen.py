from datetime import datetime, timedelta
from icalendar import Calendar, Event, Alarm
from src.database import get_user_schedules, get_user

def generate_ics_calendar(chat_id: int) -> bytes:
    """
    사용자의 전체 실업인정 일정을 iCalendar (.ics) 파일로 생성
    """
    user = get_user(chat_id)
    schedules = get_user_schedules(chat_id)
    
    cal = Calendar()
    cal.add('prodid', '-//Silup Assistant Bot//ko//')
    cal.add('version', '2.0')
    cal.add('x-wr-calname', f'실업급여 인정 일정 ({user["user_name"] if user else "내 일정"})')
    cal.add('x-wr-timezone', 'Asia/Seoul')
    
    for s in schedules:
        recog_date = datetime.strptime(s["recognition_date"], "%Y-%m-%d").date()
        
        # 1. 실업인정일 당일 이벤트
        ev = Event()
        # 동일 회차 일정이 이미 존재하는 경우 중복 생성을 막고 자동 갱신(편집)하도록 고유 UID 부여
        ev.add('uid', f'silup-{chat_id}-round-{s["round_num"]}@silup.assistant')
        ev.add('sequence', 1)
        ev.add('status', 'CONFIRMED')
        ev.add('summary', f'[실업급여] {s["round_num"]}차 실업인정일 ({s["attendance_type"]})')
        
        desc = (
            f"📌 회차: {s['round_num']}차 실업인정일\n"
            f"⏱ 전송시간: 당일 00:00 ~ 17:00 (오전 권장)\n"
            f"📋 필수 활동: {s['activity_type']} ({s['required_count']}건)\n"
            f"🏢 출석유형: {s['attendance_type']}\n"
            f"📅 인정대상기간: {s['period_start']} ~ {s['period_end']}\n"
        )
        if s["note"]:
            desc += f"⚠️ 주의사항: {s['note']}\n"
            
        ev.add('description', desc)
        ev.add('dtstart', recog_date)
        ev.add('dtend', recog_date + timedelta(days=1))
        
        # 알림(Alarm) 설정: 당일 오전 9시 알림, 1일 전 오전 10시 알림
        alarm_d0 = Alarm()
        alarm_d0.add('action', 'DISPLAY')
        alarm_d0.add('description', f'{s["round_num"]}차 실업인정 전송일입니다! (17시 마감)')
        alarm_d0.add('trigger', timedelta(hours=9)) # 당일 09:00
        ev.add_component(alarm_d0)
        
        alarm_d1 = Alarm()
        alarm_d1.add('action', 'DISPLAY')
        alarm_d1.add('description', f'내일은 {s["round_num"]}차 실업인정일입니다. 서류를 미리 확인하세요.')
        alarm_d1.add('trigger', -timedelta(days=1, hours=14)) # 1일 전 10:00
        ev.add_component(alarm_d1)
        
        cal.add_component(ev)
        
        # 2. 구직활동 시작 알림 이벤트 (2차 이후)
        if s["round_num"] > 1:
            start_date = datetime.strptime(s["period_start"], "%Y-%m-%d").date()
            ev_start = Event()
            ev_start.add('uid', f'silup-{chat_id}-round-{s["round_num"]}-start@silup.assistant')
            ev_start.add('sequence', 1)
            ev_start.add('status', 'CONFIRMED')
            ev_start.add('summary', f'[실업급여] {s["round_num"]}차 구직활동 기간 시작')
            ev_start.add('description', f'{s["round_num"]}차 구직활동을 진행할 수 있는 기간입니다.\n마감 인정일: {s["recognition_date"]}\n필요활동: {s["activity_type"]}')
            ev_start.add('dtstart', start_date)
            ev_start.add('dtend', start_date + timedelta(days=1))
            cal.add_component(ev_start)
            
    return cal.to_ical()

import urllib.parse

def get_google_calendar_url(round_num: int, recog_date_str: str, attend_type: str, act_type: str, req_count: int, note: str = "", google_email: str = "") -> str:
    """
    구글 캘린더 웹에서 클릭 한 번으로 새 일정을 추가할 수 있는 웹 URL 생성
    (google_email이 제공되면 해당 계정으로 즉시 전환되는 authuser 파라미터 자동 적용)
    """
    # YYYY-MM-DD -> YYYYMMDD
    d_clean = recog_date_str.replace("-", "")
    dt_start = f"{d_clean}T000000Z"
    dt_end = f"{d_clean}T080000Z" # UTC 기준 08:00 (KST 17:00 마감)
    
    title = f"[실업급여] {round_num}차 실업인정일 ({attend_type})"
    desc = (
        f"📌 회차: {round_num}차 실업인정일\n"
        f"⏱ 전송 시간: 당일 00:00 ~ 17:00 (오전 권장)\n"
        f"📋 필수 활동: {act_type} ({req_count}건)\n"
        f"🏢 출석 방식: {attend_type}\n"
    )
    if note:
        desc += f"⚠️ 주의: {note}\n"
    desc += "\n고용24: https://www.work24.go.kr"
    
    params = {
        "action": "TEMPLATE",
        "text": title,
        "dates": f"{dt_start}/{dt_end}",
        "details": desc,
        "location": "부산지방고용노동청 부산북부고용센터 3층 11번 창구 (또는 고용24 온라인)"
    }
    if google_email:
        params["authuser"] = google_email.strip()
        
    return f"https://calendar.google.com/calendar/render?{urllib.parse.urlencode(params)}"

