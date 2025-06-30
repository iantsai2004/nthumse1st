# app.py

# IMPORTANT: Gevent monkey patching MUST be done as early as possible.
# This ensures that standard library modules like 'ssl' are patched
# before other libraries (like requests, urllib3) import them.
import gevent.monkey
gevent.monkey.patch_all()

import os
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from dotenv import load_dotenv
from datetime import datetime, timedelta
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# --- Configuration ---
# Load environment variables from .env file
load_dotenv()

# Line Bot API Configuration
CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')

if not CHANNEL_ACCESS_TOKEN:
    raise ValueError("LINE_CHANNEL_ACCESS_TOKEN environment variable not set.")
if not CHANNEL_SECRET:
    raise ValueError("LINE_CHANNEL_SECRET environment variable not set.")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

app = Flask(__name__)

# Directory containing password files
PASSWORD_DIR = os.path.join(os.path.dirname(__file__), 'passwords')

# Helper to load passwords from a file (one per line)
def load_passwords(filename):
    path = os.path.join(PASSWORD_DIR, filename)
    if not os.path.isfile(path):
        return []
    with open(path, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip()]

# --- Database Configuration (SQLite for simplicity) ---
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///app.db')
engine = create_engine(DATABASE_URL)
Base = declarative_base()
Session = sessionmaker(bind=engine)

# --- Database Models ---
class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    user_id = Column(String(50), unique=True, nullable=False)
    role = Column(String(20), default='guest')  # 'guest', 'team', 'admin'
    team_name = Column(String(50), nullable=True)
    last_active = Column(DateTime, default=datetime.utcnow)
    team_password = Column(String(50), nullable=True) # Storing passwords directly for simplicity, hash in real app
    admin_password = Column(String(50), nullable=True) # Storing passwords directly for simplicity, hash in real app

class Mission(Base):
    __tablename__ = 'missions'
    id = Column(Integer, primary_key=True)
    mission_code = Column(String(20), unique=True, nullable=False)
    name = Column(String(100), nullable=False)
    description = Column(String(500))
    is_completed = Column(Boolean, default=False)
    completion_time = Column(DateTime, nullable=True)
    completed_by_team = Column(String(50), nullable=True)

class Announcement(Base):
    __tablename__ = 'announcements'
    id = Column(Integer, primary_key=True)
    message = Column(String(500), nullable=False)
    scheduled_time = Column(DateTime, nullable=True)
    sent = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

# --- Database Initialization ---
def init_db():
    print("Initializing database...")
    Base.metadata.create_all(engine)
    print("Database initialized.")

def add_initial_data():
    print("Checking and adding initial data...")
    session = Session()

    # Load passwords from external files
    gm_passwords = load_passwords('gm_passwords.txt')
    organizer_passwords = load_passwords('organizer_passwords.txt')
    team_passwords = load_passwords('team_passwords.txt')

    # Add Game Master admin accounts
    for idx, pwd in enumerate(gm_passwords, start=1):
        if not session.query(User).filter_by(role='admin', admin_password=pwd).first():
            new_admin = User(
                user_id=f'gm_placeholder_{idx}',
                role='admin',
                team_name='game_master',
                admin_password=pwd
            )
            session.add(new_admin)
            print(f"Added Admin: game_master #{idx} with password '{pwd}'")

    # Add Organizer admin accounts
    for idx, pwd in enumerate(organizer_passwords, start=1):
        if not session.query(User).filter_by(role='admin', admin_password=pwd).first():
            new_admin = User(
                user_id=f'organizer_placeholder_{idx}',
                role='admin',
                team_name='organizer',
                admin_password=pwd
            )
            session.add(new_admin)
            print(f"Added Admin: organizer #{idx} with password '{pwd}'")

    # Add Team placeholders
    for idx, pwd in enumerate(team_passwords, start=1):
        if not session.query(User).filter_by(role='team', team_password=pwd).first():
            new_team = User(
                user_id=f'team_placeholder_{idx}',
                role='team',
                team_name=f'隊伍-{idx}',
                team_password=pwd
            )
            session.add(new_team)
            print(f"Added Team: 隊伍-{idx} with password '{pwd}'")

    session.commit()
    session.close()
    print("Initial data check and addition complete.")

# Ensure the database and initial accounts are ready when the module is imported
init_db()
add_initial_data()

# --- Helper Functions ---
def get_user(user_id):
    session = Session()
    user = session.query(User).filter_by(user_id=user_id).first()
    session.close()
    return user

def create_or_update_user(user_id, role='guest', team_name=None, team_password=None, admin_password=None):
    session = Session()
    user = session.query(User).filter_by(user_id=user_id).first()
    if user:
        user.role = role
        user.team_name = team_name
        user.last_active = datetime.utcnow()
        if team_password:
            user.team_password = team_password
        if admin_password:
            user.admin_password = admin_password
    else:
        user = User(user_id=user_id, role=role, team_name=team_name,
                    team_password=team_password, admin_password=admin_password,
                    last_active=datetime.utcnow())
        session.add(user)
    session.commit()
    session.close()
    return user

def get_mission_by_code(mission_code):
    session = Session()
    mission = session.query(Mission).filter_by(mission_code=mission_code).first()
    session.close()
    return mission

def get_all_missions():
    session = Session()
    missions = session.query(Mission).all()
    session.close()
    return missions

def get_all_teams():
    session = Session()
    teams = session.query(User).filter_by(role='team').all()
    session.close()
    return teams

def get_all_admins():
    session = Session()
    admins = session.query(User).filter_by(role='admin').all()
    session.close()
    return admins

# --- Scheduler for Announcements ---
scheduler = BackgroundScheduler(daemon=True)

def send_announcement(announcement_id, user_id=None):
    session = Session()
    announcement = session.query(Announcement).filter_by(id=announcement_id).first()
    if announcement and not announcement.sent:
        try:
            # Send to all users if user_id is not specified (broadcast)
            if user_id is None:
                users = session.query(User).all()
                for user in users:
                    try:
                        line_bot_api.push_message(user.user_id, TextSendMessage(text=f"📢 公告：\n{announcement.message}"))
                    except LineBotApiError as e:
                        app.logger.error(f"Failed to send announcement to user {user.user_id}: {e}")
                        if e.status_code == 401:
                            app.logger.error("Authentication failed. Check LINE_CHANNEL_ACCESS_TOKEN.")
                    except Exception as e:
                        app.logger.error(f"Failed to send announcement to user {user.user_id}: {e}")
            else:
                # Send to a specific user
                 try:
                    line_bot_api.push_message(user_id, TextSendMessage(text=f"📢 公告：\n{announcement.message}"))
                 except LineBotApiError as e:
                    app.logger.error(f"Failed to send announcement to user {user_id}: {e}")
                    if e.status_code == 401:
                        app.logger.error("Authentication failed. Check LINE_CHANNEL_ACCESS_TOKEN.")
                 except Exception as e:
                    app.logger.error(f"Failed to send announcement to user {user_id}: {e}")

            announcement.sent = True
            session.commit()
            app.logger.info(f"Announcement '{announcement.message}' sent successfully.")
        except Exception as e:
            app.logger.error(f"Error sending announcement ID {announcement_id}: {e}")
            session.rollback()
    session.close()

def schedule_announcement(message, scheduled_time_str):
    session = Session()
    try:
        # Assuming scheduled_time_str is in 'YYYY-MM-DD HH:MM' format and local timezone (Taiwan)
        taiwan_tz = pytz.timezone('Asia/Taipei')
        scheduled_time = taiwan_tz.localize(datetime.strptime(scheduled_time_str, '%Y-%m-%d %H:%M'))
        # Convert to UTC for APScheduler
        scheduled_time_utc = scheduled_time.astimezone(pytz.utc)

        new_announcement = Announcement(message=message, scheduled_time=scheduled_time_utc)
        session.add(new_announcement)
        session.commit()

        # Schedule the job
        scheduler.add_job(
            send_announcement,
            DateTrigger(run_date=scheduled_time_utc),
            args=[new_announcement.id],
            id=f'announcement_{new_announcement.id}',
            replace_existing=True
        )
        app.logger.info(f"Announcement '{message}' scheduled for {scheduled_time_str}.")
        return True
    except ValueError:
        app.logger.error(f"Invalid datetime format: {scheduled_time_str}")
        return False
    except Exception as e:
        app.logger.error(f"Error scheduling announcement: {e}")
        session.rollback()
        return False
    finally:
        session.close()

def get_all_scheduled_announcements():
    session = Session()
    announcements = session.query(Announcement).filter_by(sent=False).order_by(Announcement.scheduled_time).all()
    session.close()
    return announcements

def cancel_announcement_by_id(announcement_id):
    session = Session()
    announcement = session.query(Announcement).filter_by(id=announcement_id).first()
    if announcement:
        try:
            scheduler.remove_job(f'announcement_{announcement_id}')
            session.delete(announcement)
            session.commit()
            app.logger.info(f"Announcement ID {announcement_id} cancelled and deleted.")
            return True
        except Exception as e:
            app.logger.error(f"Error cancelling announcement ID {announcement_id}: {e}")
            session.rollback()
            return False
    session.close()
    return False

# --- Webhook Handler ---
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info("Request body: %s", body)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        app.logger.error("Invalid signature. Please check your channel access token/channel secret.")
        abort(400)
    except LineBotApiError as e:
        app.logger.error(f"LineBot API error: {e}")
        if e.status_code == 401:
            app.logger.error("Authentication failed. Check LINE_CHANNEL_ACCESS_TOKEN.")
        return 'OK'
    except Exception as e:
        app.logger.error(f"Error handling webhook: {e}")
        # Here's the crucial part: if an error occurs while handling a message,
        # ensure you don't re-trigger the same error by attempting to reply
        # in a way that creates an infinite loop.
        # The recursion itself is usually within the handle_message,
        # so this outer handler might catch it after the fact.
        return 'OK' # Return OK to LINE to prevent retries, but log the error

    return 'OK'

# --- Message Handler ---
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    reply_token = event.reply_token
    user_id = event.source.user_id
    text = event.message.text.strip()
    user = get_user(user_id)

    # --- Initial Login/Registration Logic ---
    if not user or user.role == 'guest':
        # Check for team password command
        if text.lower().startswith('密碼 '):
            parts = text.split(' ', 1)
            if len(parts) == 2:
                password_attempt = parts[1]
                session = Session()
                existing_team_user = session.query(User).filter_by(role='team', team_password=password_attempt).first()
                if existing_team_user:
                    # Update current user or create new if not exists
                    create_or_update_user(user_id, role='team', team_name=f'隊伍-{password_attempt}', team_password=password_attempt)
                    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"登入成功！您已加入隊伍 {password_attempt}。"))
                else:
                    line_bot_api.reply_message(reply_token, TextSendMessage(text="隊伍密碼錯誤，請重新輸入或輸入管理員密碼。"))
                session.close()
                return # Crucial: Exit after handling password input

        # Check for admin password command
        elif text.lower().startswith('管理員密碼 '):
            parts = text.split(' ', 1)
            if len(parts) == 2:
                admin_password_attempt = parts[1]
                session = Session()
                existing_admin_user = session.query(User).filter_by(role='admin', admin_password=admin_password_attempt).first()
                if existing_admin_user:
                    create_or_update_user(user_id, role='admin', team_name='game_master', admin_password=admin_password_attempt)
                    line_bot_api.reply_message(reply_token, TextSendMessage(text="管理員登入成功！您現在擁有管理員權限。"))
                else:
                    line_bot_api.reply_message(reply_token, TextSendMessage(text="管理員密碼錯誤，請重新輸入。"))
                session.close()
                return # Crucial: Exit after handling password input

        else:
            # This is the line that was likely causing recursion if not handled properly
            # by immediately returning after a successful login attempt.
            line_bot_api.reply_message(reply_token, TextSendMessage(text="請先輸入密碼登入 (例如：密碼 [您的隊伍密碼] 或 管理員密碼 [您的管理員密碼])。"))
            return # Ensure exit here if not logged in

    # --- Team User Logic ---
    if user and user.role == 'team':
        if text.lower() == '我的隊伍':
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"您的隊伍是：{user.team_name}"))
        elif text.lower().startswith('完成任務 '):
            parts = text.split(' ', 1)
            if len(parts) == 2:
                mission_code = parts[1].upper()
                session = Session()
                mission = session.query(Mission).filter_by(mission_code=mission_code).first()
                if mission:
                    if not mission.is_completed:
                        mission.is_completed = True
                        mission.completion_time = datetime.utcnow()
                        mission.completed_by_team = user.team_name
                        session.commit()
                        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"任務 '{mission.name}' 已成功標記為完成！"))
                    else:
                        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"任務 '{mission.name}' 已經被隊伍 {mission.completed_by_team} 完成了。"))
                else:
                    line_bot_api.reply_message(reply_token, TextSendMessage(text="任務代碼無效，請檢查後重試。"))
                session.close()
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="請輸入有效的任務代碼 (例如：完成任務 M001)。"))
        elif text.lower() == '查看任務':
            missions = get_all_missions()
            if missions:
                response = "目前任務列表：\n"
                for m in missions:
                    status = "✅ 已完成" if m.is_completed else "⏳ 未完成"
                    response += f"代碼：{m.mission_code}, 名稱：{m.name}, 狀態：{status}\n"
                    if m.is_completed:
                        completion_time_local = pytz.utc.localize(m.completion_time).astimezone(pytz.timezone('Asia/Taipei'))
                        response += f"  完成時間：{completion_time_local.strftime('%Y-%m-%d %H:%M')}, 完成隊伍：{m.completed_by_team}\n"
                line_bot_api.reply_message(reply_token, TextSendMessage(text=response))
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="目前沒有任何任務。"))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="您已登入為隊伍。可用的指令有：\n1. 我的隊伍\n2. 完成任務 [任務代碼]\n3. 查看任務"))
        return # Crucial: Exit after handling team commands

    # --- Admin User Logic ---
    if user and user.role == 'admin':
        if text.lower() == '管理員指令':
            line_bot_api.reply_message(reply_token, TextSendMessage(text="管理員指令列表：\n1. 添加任務 [代碼] [名稱] [描述]\n2. 查看所有任務\n3. 重置任務 [代碼] (管理員專用)\n4. 查看所有隊伍\n5. 發布公告 [時間(YYYY-MM-DD HH:MM)] [訊息]\n6. 查看所有公告\n7. 取消公告 [ID]"))
        elif text.lower().startswith('添加任務 '):
            parts = text.split(' ', 3) # Split into 4 parts: command, code, name, description
            if len(parts) == 4:
                mission_code = parts[1].upper()
                mission_name = parts[2]
                mission_description = parts[3]
                session = Session()
                if not session.query(Mission).filter_by(mission_code=mission_code).first():
                    new_mission = Mission(mission_code=mission_code, name=mission_name, description=mission_description)
                    session.add(new_mission)
                    session.commit()
                    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"任務 '{mission_name}' (代碼：{mission_code}) 已添加。"))
                else:
                    line_bot_api.reply_message(reply_token, TextSendMessage(text="任務代碼已存在，請使用不同的代碼。"))
                session.close()
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="請輸入有效的指令格式：添加任務 [代碼] [名稱] [描述]"))
        elif text.lower() == '查看所有任務':
            missions = get_all_missions()
            if missions:
                response = "所有任務列表：\n"
                for m in missions:
                    status = "✅ 已完成" if m.is_completed else "⏳ 未完成"
                    response += f"代碼：{m.mission_code}, 名稱：{m.name}, 狀態：{status}\n"
                    if m.is_completed:
                        completion_time_local = pytz.utc.localize(m.completion_time).astimezone(pytz.timezone('Asia/Taipei'))
                        response += f"  完成時間：{completion_time_local.strftime('%Y-%m-%d %H:%M')}, 完成隊伍：{m.completed_by_team}\n"
                line_bot_api.reply_message(reply_token, TextSendMessage(text=response))
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="目前沒有任何任務。"))
        elif text.lower().startswith('重置任務 '):
            parts = text.split(' ', 1)
            if len(parts) == 2:
                mission_code = parts[1].upper()
                session = Session()
                mission = session.query(Mission).filter_by(mission_code=mission_code).first()
                if mission:
                    mission.is_completed = False
                    mission.completion_time = None
                    mission.completed_by_team = None
                    session.commit()
                    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"任務 '{mission.name}' 已重置為未完成。"))
                else:
                    line_bot_api.reply_message(reply_token, TextSendMessage(text="任務代碼無效。"))
                session.close()
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="請輸入有效的任務代碼 (例如：重置任務 M001)。"))
        elif text.lower() == '查看所有隊伍':
            teams = get_all_teams()
            if teams:
                response = "所有隊伍列表：\n"
                for t in teams:
                    if t.team_name and t.role == 'team':
                        response += f"隊伍名稱：{t.team_name}, 用戶ID：{t.user_id}\n"
                line_bot_api.reply_message(reply_token, TextSendMessage(text=response))
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="目前沒有任何隊伍。"))
        elif text.lower().startswith('發布公告 '):
            parts = text.split(' ', 2) # Split into 3 parts: command, time, message
            if len(parts) == 3:
                scheduled_time_str = parts[1]
                announcement_message = parts[2]
                if schedule_announcement(announcement_message, scheduled_time_str):
                    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"公告已成功安排於 {scheduled_time_str} 發送。"))
                else:
                    line_bot_api.reply_message(reply_token, TextSendMessage(text="時間格式無效 (應為 YYYY-MM-DD HH:MM) 或排程失敗。"))
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="請輸入有效的指令格式：發布公告 [時間(YYYY-MM-DD HH:MM)] [訊息]"))
        elif text.lower() == '查看所有公告':
            announcements = get_all_scheduled_announcements()
            if announcements:
                response = "所有排程公告列表：\n"
                for a in announcements:
                    scheduled_time_local = pytz.utc.localize(a.scheduled_time).astimezone(pytz.timezone('Asia/Taipei'))
                    response += f"ID: {a.id}, 時間: {scheduled_time_local.strftime('%Y-%m-%d %H:%M')}, 訊息: {a.message}\n"
                line_bot_api.reply_message(reply_token, TextSendMessage(text=response))
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="目前沒有任何排程公告。"))
        elif text.lower().startswith('取消公告 '):
            parts = text.split(' ', 1)
            if len(parts) == 2 and parts[1].isdigit():
                announcement_id = int(parts[1])
                if cancel_announcement_by_id(announcement_id):
                    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"公告 ID {announcement_id} 已取消並刪除。"))
                else:
                    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"找不到公告 ID {announcement_id} 或取消失敗。"))
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="請輸入有效的公告 ID (例如：取消公告 1)。"))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="您已登入為管理員。輸入 '管理員指令' 查看可用指令。"))
        return # Crucial: Exit after handling admin commands

    # Fallback for unhandled messages (should not be reached if previous 'return' statements work)
    app.logger.warning(f"Unhandled message from user {user_id} ({user.role if user else 'guest'}): {text}")
    line_bot_api.reply_message(reply_token, TextSendMessage(text="對不起，我不明白您的意思。"))


# --- Initialization for WSGI environments ---
# Ensure the database and scheduler are initialized even when the application
# is launched by a WSGI server (e.g. Gunicorn) and the __main__ block is not
# executed.
init_db()
add_initial_data()
if not scheduler.running:
    scheduler.start()
    app.logger.info("Scheduler started.")

if __name__ == "__main__":

    # Render.com will set the PORT environment variable
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)