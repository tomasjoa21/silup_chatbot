import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path
from src.config import DB_PATH, RULES_PATH

def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # 1. 사용자 테이블
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        chat_id INTEGER PRIMARY KEY,
        user_name TEXT,
        user_type TEXT DEFAULT '일반수급자',
        first_recognition_date TEXT NOT NULL,
        total_benefit_days INTEGER DEFAULT 150,
        center_window TEXT DEFAULT '3층 11번 창구',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    # 2. 회차별 실업인정 일정 테이블
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS schedules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        round_num INTEGER NOT NULL,
        period_start TEXT NOT NULL,
        period_end TEXT NOT NULL,
        recognition_date TEXT NOT NULL,
        required_count INTEGER NOT NULL,
        activity_type TEXT NOT NULL,
        attendance_type TEXT NOT NULL,
        note TEXT,
        reminded_d7 INTEGER DEFAULT 0,
        reminded_d3 INTEGER DEFAULT 0,
        reminded_d1 INTEGER DEFAULT 0,
        reminded_d0 INTEGER DEFAULT 0,
        is_completed INTEGER DEFAULT 0,
        FOREIGN KEY (chat_id) REFERENCES users(chat_id)
    )
    """)
    
    # 3. 구직외활동 카운트 테이블
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS activity_counts (
        chat_id INTEGER PRIMARY KEY,
        online_lecture_count INTEGER DEFAULT 0,
        psychology_test_count INTEGER DEFAULT 0,
        stability_program_count INTEGER DEFAULT 0,
        FOREIGN KEY (chat_id) REFERENCES users(chat_id)
    )
    """)
    
    conn.commit()
    conn.close()

def load_rules():
    if Path(RULES_PATH).exists():
        with open(RULES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def adjust_for_weekend(d: datetime.date) -> datetime.date:
    # 토요일(5)이면 월요일(+2일), 일요일(6)이면 월요일(+1일)
    if d.weekday() == 5:
        return d + timedelta(days=2)
    elif d.weekday() == 6:
        return d + timedelta(days=1)
    return d

def save_user_and_generate_schedule(chat_id: int, user_name: str, first_date_str: str, total_days: int = 150, user_type: str = "일반수급자"):
    """
    사용자 등록 및 1차부터 수급 만료일까지의 모든 회차 일정을 자동 계산하여 저장
    """
    conn = get_db()
    cursor = conn.cursor()
    
    # 사용자 정보 저장 (UPSERT)
    cursor.execute("""
    INSERT INTO users (chat_id, user_name, user_type, first_recognition_date, total_benefit_days)
    VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(chat_id) DO UPDATE SET
        user_name=excluded.user_name,
        user_type=excluded.user_type,
        first_recognition_date=excluded.first_recognition_date,
        total_benefit_days=excluded.total_benefit_days
    """, (chat_id, user_name, user_type, first_date_str, total_days))
    
    # 기존 일정 삭제 후 재계산
    cursor.execute("DELETE FROM schedules WHERE chat_id = ?", (chat_id,))
    
    # 활동 카운트 초기화
    cursor.execute("""
    INSERT INTO activity_counts (chat_id, online_lecture_count, psychology_test_count, stability_program_count)
    VALUES (?, 0, 0, 0)
    ON CONFLICT(chat_id) DO NOTHING
    """, (chat_id,))
    
    rules_data = load_rules()
    type_rules = rules_data.get("types", {}).get(user_type, {}).get("rules", {})
    
    first_date = datetime.strptime(first_date_str, "%Y-%m-%d").date()
    
    # 1차 실업인정일 (보통 8일치 수급)
    # 1차 인정일은 교육 당일
    schedules = []
    
    # 1차 등록
    schedules.append({
        "round_num": 1,
        "period_start": first_date_str,
        "period_end": first_date_str,
        "recognition_date": first_date_str,
        "required_count": 1,
        "activity_type": "1차 실업인정교육 (출석 또는 온라인)",
        "attendance_type": "센터 출석 또는 온라인",
        "note": "1차 집체교육 참석 완료"
    })
    
    # 2차 이후 회차 생성
    # 1차에 인정된 일수는 보통 8일
    remaining_days = total_days - 8
    current_date = first_date
    round_num = 2
    
    while remaining_days > 0:
        period_start = current_date + timedelta(days=1)
        # 통상 4주(28일) 주기
        cycle_days = min(28, remaining_days)
        target_recog_date = current_date + timedelta(days=28)
        
        # 주말 보정
        recog_date = adjust_for_weekend(target_recog_date)
        period_end = recog_date
        
        # 룰 적용
        round_key = str(round_num)
        if round_key in type_rules:
            rule = type_rules[round_key]
        elif round_num >= 5 and "5+" in type_rules:
            rule = type_rules["5+"]
        elif "all" in type_rules:
            rule = type_rules["all"]
        else:
            rule = {
                "required_count": 2 if round_num >= 5 else 1,
                "activity_type": "구직활동 1회 이상 포함 (총 2회)" if round_num >= 5 else "구직활동 또는 구직외활동 1회",
                "attendance_type": "온라인 전송"
            }
            
        attendance = rule.get("attendance", "온라인 전송")
        if round_num == 4 and user_type == "일반수급자":
            attendance = "센터 의무 출석 (3층 11번 창구)"
            
        schedules.append({
            "round_num": round_num,
            "period_start": period_start.strftime("%Y-%m-%d"),
            "period_end": period_end.strftime("%Y-%m-%d"),
            "recognition_date": recog_date.strftime("%Y-%m-%d"),
            "required_count": rule.get("required_count", 1),
            "activity_type": rule.get("activity_type", "구직활동"),
            "attendance_type": attendance,
            "note": rule.get("note", "")
        })
        
        remaining_days -= 28
        current_date = recog_date
        round_num += 1
        
    for s in schedules:
        cursor.execute("""
        INSERT INTO schedules 
        (chat_id, round_num, period_start, period_end, recognition_date, required_count, activity_type, attendance_type, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            chat_id, s["round_num"], s["period_start"], s["period_end"],
            s["recognition_date"], s["required_count"], s["activity_type"],
            s["attendance_type"], s["note"]
        ))
        
    conn.commit()
    conn.close()
    return len(schedules)

def get_user(chat_id: int):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,)).fetchone()
    conn.close()
    return user

def get_all_users():
    conn = get_db()
    users = conn.execute("SELECT * FROM users").fetchall()
    conn.close()
    return users

def get_user_schedules(chat_id: int):
    conn = get_db()
    rows = conn.execute("SELECT * FROM schedules WHERE chat_id = ? ORDER BY round_num ASC", (chat_id,)).fetchall()
    conn.close()
    return rows

def get_next_schedule(chat_id: int, current_date_str: str):
    conn = get_db()
    row = conn.execute("""
    SELECT * FROM schedules 
    WHERE chat_id = ? AND recognition_date >= ?
    ORDER BY round_num ASC LIMIT 1
    """, (chat_id, current_date_str)).fetchone()
    conn.close()
    return row

def update_reminder_flag(schedule_id: int, flag_name: str):
    conn = get_db()
    conn.execute(f"UPDATE schedules SET {flag_name} = 1 WHERE id = ?", (schedule_id,))
    conn.commit()
    conn.close()
