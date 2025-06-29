# app.py
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FlexSendMessage
import os
import bcrypt
import uuid
import time
from datetime import datetime, timedelta
import json
import re

from dotenv import load_dotenv

# 加載 .env 檔中的環境變數
load_dotenv()

# 從環境變數獲取 LINE Channel 憑證
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
PASSWORD_SALT = os.getenv("PASSWORD_SALT").encode('utf-8') # 確保是 bytes

if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_CHANNEL_SECRET or not PASSWORD_SALT:
    raise ValueError("LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET, or PASSWORD_SALT are not set in .env")

app = Flask(__name__)

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 導入資料庫相關
from database import init_db, get_db # 確保這裡只導入需要的，避免循環引用
from models import Team, Card, TeamCard, AdminPassword, TradeRequest # 這裡導入所有模型

# --- 硬編碼卡牌資料 (根據您提供的資訊) ---
INITIAL_CARDS_DATA = [
    # 稀有
    {"card_number": "1", "name_zh": "鍍白金"},
    {"card_number": "2", "name_zh": "XRD D8"},
    {"card_number": "3", "name_zh": "接觸角分析器"},
    {"card_number": "4", "name_zh": "SEM SU8010"},
    {"card_number": "5", "name_zh": "霍爾量測器"},
    # 中等
    {"card_number": "6", "name_zh": "青銅"},
    {"card_number": "7", "name_zh": "黃銅"},
    {"card_number": "8", "name_zh": "鋼"},
    {"card_number": "9", "name_zh": "不鏽鋼"},
    {"card_number": "10", "name_zh": "超合金"},
    {"card_number": "11", "name_zh": "黃金"},
    {"card_number": "12", "name_zh": "高熵合金"},
    {"card_number": "13", "name_zh": "銀"},
    {"card_number": "14", "name_zh": "鑽石"},
    {"card_number": "15", "name_zh": "記憶合金"},
    # 初等
    {"card_number": "16", "name_zh": "鋁"},
    {"card_number": "17", "name_zh": "鎳"},
    {"card_number": "18", "name_zh": "銅"},
    {"card_number": "19", "name_zh": "鎂"},
    {"card_number": "20", "name_zh": "錫"},
    {"card_number": "21", "name_zh": "鉻"},
    {"card_number": "22", "name_zh": "鋅"},
    {"card_number": "23", "name_zh": "碳"},
    {"card_number": "24", "name_zh": "鐵"},
    {"card_number": "25", "name_zh": "鈷"},
]

# --- 輔助函數 ---

def hash_password(password):
    """雜湊密碼"""
    return bcrypt.hashpw(password.encode('utf-8'), PASSWORD_SALT).decode('utf-8')

def check_password(password, hashed_password):
    """驗證密碼"""
    try:
        return bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8'))
    except ValueError:
        # 如果 hashed_password 格式不正確 (例如不是 bcrypt 雜湊)，會拋出 ValueError
        return False

def get_user_session(user_id):
    """獲取用戶會話狀態 (簡化為字典，實際可考慮 Redis 或資料庫)"""
    if not hasattr(get_user_session, 'sessions'):
        get_user_session.sessions = {}
    return get_user_session.sessions.get(user_id, {})

def set_user_session(user_id, session_data):
    """設置用戶會話狀態"""
    if not hasattr(get_user_session, 'sessions'):
        get_user_session.sessions = {}
    get_user_session.sessions[user_id] = session_data

def clear_user_session(user_id):
    """清除用戶會話狀態"""
    if hasattr(get_user_session, 'sessions'):
        if user_id in get_user_session.sessions:
            del get_user_session.sessions[user_id]

def find_card_by_input(db, card_input):
    """根據卡號或名稱查找卡牌"""
    card = db.query(Card).filter((Card.card_number == card_input) | (Card.name_zh == card_input)).first()
    return card

# --- 數據初始化函數 ---
def add_initial_data(db_session):
    print("Checking and adding initial data...")

    # 添加隊伍 (8隊，編號 1 到 8)
    team_names = [f"小隊{i}" for i in range(1, 9)] # 小隊1, 小隊2, ..., 小隊8
    team_passwords = [f"team_{i}_pass" for i in range(1, 9)]
    
    for i, name in enumerate(team_names):
        if not db_session.query(Team).filter_by(name=name).first():
            new_team = Team(name=name, password_hash=hash_password(team_passwords[i]))
            db_session.add(new_team)
            print(f"Added Team: {name} with password '{team_passwords[i]}'")
    db_session.commit()

    # 獲取所有隊伍的 ID，用於關主權限設定
    all_teams = db_session.query(Team).all()
    team_id_map = {team.name: team.id for team in all_teams}
    all_team_ids_str = ",".join(str(t.id) for t in all_teams)

    # 添加卡牌
    for card_data in INITIAL_CARDS_DATA:
        if not db_session.query(Card).filter_by(card_number=card_data["card_number"]).first():
            new_card = Card(
                card_number=card_data["card_number"],
                name_zh=card_data["name_zh"]
            )
            db_session.add(new_card)
            print(f"Added Card: {card_data['name_zh']} ({card_data['card_number']})")
    db_session.commit()

    # 添加關主和主辦方密碼
    # 關主密碼 (8組：A1-A4, B1-B4)
    # 每組關主負責2個小隊，這裡隨機分配，您可以根據實際需求調整
    admin_passwords_data = [
        {"role": "game_master", "password": "gm_A1_pass", "team_scope": f"{team_id_map.get('小隊1')},{team_id_map.get('小隊2')}"},
        {"role": "game_master", "password": "gm_A2_pass", "team_scope": f"{team_id_map.get('小隊3')},{team_id_map.get('小隊4')}"},
        {"role": "game_master", "password": "gm_A3_pass", "team_scope": f"{team_id_map.get('小隊5')},{team_id_map.get('小隊6')}"},
        {"role": "game_master", "password": "gm_A4_pass", "team_scope": f"{team_id_map.get('小隊7')},{team_id_map.get('小隊8')}"},
        {"role": "game_master", "password": "gm_B1_pass", "team_scope": f"{team_id_map.get('小隊1')},{team_id_map.get('小隊3')}"},
        {"role": "game_master", "password": "gm_B2_pass", "team_scope": f"{team_id_map.get('小隊2')},{team_id_map.get('小隊4')}"},
        {"role": "game_master", "password": "gm_B3_pass", "team_scope": f"{team_id_map.get('小隊5')},{team_id_map.get('小隊7')}"},
        {"role": "game_master", "password": "gm_B4_pass", "team_scope": f"{team_id_map.get('小隊6')},{team_id_map.get('小隊8')}"},
        
        # 主辦方密碼 (12組：A-L)
        {"role": "organizer", "password": "org_A_pass", "team_scope": all_team_ids_str},
        {"role": "organizer", "password": "org_B_pass", "team_scope": all_team_ids_str},
        {"role": "organizer", "password": "org_C_pass", "team_scope": all_team_ids_str},
        {"role": "organizer", "password": "org_D_pass", "team_scope": all_team_ids_str},
        {"role": "organizer", "password": "org_E_pass", "team_scope": all_team_ids_str},
        {"role": "organizer", "password": "org_F_pass", "team_scope": all_team_ids_str},
        {"role": "organizer", "password": "org_G_pass", "team_scope": all_team_ids_str},
        {"role": "organizer", "password": "org_H_pass", "team_scope": all_team_ids_str},
        {"role": "organizer", "password": "org_I_pass", "team_scope": all_team_ids_str},
        {"role": "organizer", "password": "org_J_pass", "team_scope": all_team_ids_str},
        {"role": "organizer", "password": "org_K_pass", "team_scope": all_team_ids_str},
        {"role": "organizer", "password": "org_L_pass", "team_scope": all_team_ids_str},
    ]

    for ad_data in admin_passwords_data:
        # 檢查是否存在相同角色和密碼雜湊的記錄
        existing_admin = db_session.query(AdminPassword).filter_by(
            role=ad_data["role"],
            password_hash=hash_password(ad_data["password"])
        ).first()

        if not existing_admin:
            db_session.add(AdminPassword(
                role=ad_data["role"],
                password_hash=hash_password(ad_data["password"]),
                team_scope=ad_data["team_scope"]
            ))
            print(f"Added Admin: {ad_data['role']} with password '{ad_data['password']}'")
    db_session.commit()

    print("Initial data check and addition complete.")


# 初始化資料庫（如果表不存在則創建）
with app.app_context():
    init_db() # 確保表已創建
    db = next(get_db())
    try:
        add_initial_data(db) # 添加初始數據
    finally:
        db.close()


# --- LINE Bot Webhook 處理 ---

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        print("Invalid signature. Please check your channel access token/channel secret.")
        abort(400)

    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text.strip()
    user_id = event.source.user_id
    reply_token = event.reply_token
    session = get_user_session(user_id)
    db = next(get_db()) # 獲取資料庫會話

    try:
        # --- 登入邏輯 ---
        if text.startswith("密碼 "):
            password_input = text.split(" ", 1)[1]
            
            found_team = None
            teams = db.query(Team).all()
            for t in teams:
                if check_password(password_input, t.password_hash):
                    found_team = t
                    break

            if found_team:
                set_user_session(user_id, {"logged_in_as": "team", "team_id": found_team.id, "team_name": found_team.name})
                line_bot_api.reply_message(reply_token, TextSendMessage(text=f"登入成功！您已連接到 {found_team.name} 的資料。"))
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="密碼錯誤，請重試。"))
            return

        elif text.startswith("管理員密碼 "): # 統一使用「管理員密碼」作為前綴
            password_input = text.split(" ", 1)[1]
            found_admin = None
            admins = db.query(AdminPassword).all()
            for admin in admins:
                if check_password(password_input, admin.password_hash):
                    found_admin = admin
                    break

            if found_admin:
                set_user_session(user_id, {"logged_in_as": found_admin.role, "admin_id": found_admin.id, "team_scope": found_admin.team_scope})
                line_bot_api.reply_message(reply_token, TextSendMessage(text=f"管理員登入成功！您的權限為：{found_admin.role}"))
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="管理員密碼錯誤，請重試。"))
            return

        # --- 檢查登入狀態 ---
        if "logged_in_as" not in session:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="請先輸入密碼登入 (例如：密碼 [您的隊伍密碼] 或 管理員密碼 [您的管理員密碼])。"))
            return

        # --- 隊伍功能 ---
        if session["logged_in_as"] == "team":
            team_id = session["team_id"]
            team_name = session["team_name"]

            if text.startswith("新增卡牌 "):
                parts = text.split(" ", 2)
                if len(parts) == 3:
                    card_input = parts[1]
                    try:
                        quantity = int(parts[2])
                        if quantity <= 0: raise ValueError
                    except ValueError:
                        line_bot_api.reply_message(reply_token, TextSendMessage(text="數量必須為正整數。"))
                        return

                    card = find_card_by_input(db, card_input)
                    if not card:
                        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"找不到卡牌：{card_input}"))
                        return

                    team_card = db.query(TeamCard).filter(TeamCard.team_id == team_id, TeamCard.card_id == card.id).first()
                    if team_card:
                        team_card.quantity += quantity
                    else:
                        new_team_card = TeamCard(team_id=team_id, card_id=card.id, quantity=quantity)
                        db.add(new_team_card)
                    db.commit()
                    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"已為 {team_name} 新增 {card.name_zh} x {quantity}。"))
                else:
                    line_bot_api.reply_message(reply_token, TextSendMessage(text="指令格式：新增卡牌 [卡號/卡片名稱] [數量]"))

            elif text.startswith("刪除卡牌 "):
                parts = text.split(" ", 2)
                if len(parts) == 3:
                    card_input = parts[1]
                    try:
                        quantity_to_remove = int(parts[2])
                        if quantity_to_remove <= 0: raise ValueError
                    except ValueError:
                        line_bot_api.reply_message(reply_token, TextSendMessage(text="數量必須為正整數。"))
                        return

                    card = find_card_by_input(db, card_input)
                    if not card:
                        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"找不到卡牌：{card_input}"))
                        return

                    team_card = db.query(TeamCard).filter(TeamCard.team_id == team_id, TeamCard.card_id == card.id).first()
                    if team_card:
                        if team_card.quantity >= quantity_to_remove:
                            team_card.quantity -= quantity_to_remove
                            if team_card.quantity == 0:
                                db.delete(team_card)
                            db.commit()
                            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"已從 {team_name} 刪除 {card.name_zh} x {quantity_to_remove}。"))
                        else:
                            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"{team_name} 的 {card.name_zh} 數量不足 ({team_card.quantity})。"))
                    else:
                        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"{team_name} 並沒有 {card.name_zh} 這張卡牌。"))
                else:
                    line_bot_api.reply_message(reply_token, TextSendMessage(text="指令格式：刪除卡牌 [卡號/卡片名稱] [數量]"))

            elif text == "查看卡牌":
                team_cards = db.query(TeamCard).filter(TeamCard.team_id == team_id).all()
                if not team_cards:
                    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"{team_name} 目前沒有任何卡牌。"))
                    return

                messages = [f"{team_name} 的卡牌列表："]
                for tc in team_cards:
                    messages.append(f"  - {tc.card.card_number} ({tc.card.name_zh}): {tc.quantity} 張")
                line_bot_api.reply_message(reply_token, TextSendMessage(text="\n".join(messages)))

            elif text == "登出":
                clear_user_session(user_id)
                line_bot_api.reply_message(reply_token, TextSendMessage(text="您已成功登出。"))

            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="無法識別的隊伍指令。請使用：新增卡牌、刪除卡牌、查看卡牌 或 登出。"))

        # --- 關主/主辦方功能 ---
        elif session["logged_in_as"] in ["game_master", "organizer"]:
            admin_role = session["logged_in_as"]
            team_scope = session.get("team_scope")
            
            # 將 team_scope 轉換為可操作的隊伍 ID 列表
            allowed_team_ids = None
            if team_scope:
                try:
                    allowed_team_ids = [int(x) for x in team_scope.split(',')]
                except ValueError:
                    app.logger.error(f"Invalid team_scope format: {team_scope} for user {user_id}")
                    allowed_team_ids = [] # 無效格式則無權限

            def check_team_access(team_obj_or_id):
                if allowed_team_ids is None: # None表示可以操作所有隊伍 (主辦方)
                    return True
                
                team_id_to_check = team_obj_or_id if isinstance(team_obj_or_id, int) else team_obj_or_id.id
                return team_id_to_check in allowed_team_ids

            # 輔助函數：獲取隊伍物件並檢查權限
            def get_team_by_name(team_name_input):
                team = db.query(Team).filter(Team.name == team_name_input).first()
                if not team:
                    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"找不到隊伍：{team_name_input}"))
                    return None
                if not check_team_access(team):
                    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"您無權操作隊伍：{team_name_input}"))
                    return None
                return team

            if text.startswith("新增 "): # 統一處理管理員新增卡牌
                match = re.match(r"新增 (.+) (.+) (\d+)", text)
                if match:
                    team_name_input, card_input, quantity_str = match.groups()
                    quantity = int(quantity_str)
                    if quantity <= 0:
                        line_bot_api.reply_message(reply_token, TextSendMessage(text="數量必須為正整數。"))
                        return

                    team = get_team_by_name(team_name_input)
                    if not team: return

                    card = find_card_by_input(db, card_input)
                    if not card:
                        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"找不到卡牌：{card_input}"))
                        return

                    team_card = db.query(TeamCard).filter(TeamCard.team_id == team.id, TeamCard.card_id == card.id).first()
                    if team_card:
                        team_card.quantity += quantity
                    else:
                        new_team_card = TeamCard(team_id=team.id, card_id=card.id, quantity=quantity)
                        db.add(new_team_card)
                    db.commit()
                    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"已為 {team.name} 新增 {card.name_zh} x {quantity}。"))
                else:
                    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"指令格式：新增 [隊伍名稱] [卡號/卡片名稱] [數量]"))

            elif text.startswith("刪除 "): # 統一處理管理員刪除卡牌
                match = re.match(r"刪除 (.+) (.+) (\d+)", text)
                if match:
                    team_name_input, card_input, quantity_str = match.groups()
                    quantity_to_remove = int(quantity_str)
                    if quantity_to_remove <= 0:
                        line_bot_api.reply_message(reply_token, TextSendMessage(text="數量必須為正整數。"))
                        return

                    team = get_team_by_name(team_name_input)
                    if not team: return

                    card = find_card_by_input(db, card_input)
                    if not card:
                        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"找不到卡牌：{card_input}"))
                        return

                    team_card = db.query(TeamCard).filter(TeamCard.team_id == team.id, TeamCard.card_id == card.id).first()
                    if team_card:
                        if team_card.quantity >= quantity_to_remove:
                            team_card.quantity -= quantity_to_remove
                            if team_card.quantity == 0:
                                db.delete(team_card)
                            db.commit()
                            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"已從 {team.name} 刪除 {card.name_zh} x {quantity_to_remove}。"))
                        else:
                            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"{team.name} 的 {card.name_zh} 數量不足 ({team_card.quantity})。"))
                    else:
                        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"{team.name} 並沒有 {card.name_zh} 這張卡牌。"))
                else:
                    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"指令格式：刪除 [隊伍名稱] [卡號/卡片名稱] [數量]"))

            elif text.startswith("交換 "): # 統一處理管理員交換指令
                # 預期格式: 交換 [隊伍A名稱] [隊伍B名稱] [卡A號/名] [卡A數量] [卡B號/名] [卡B數量]
                match = re.match(r"交換 (.+) (.+) (.+) (\d+) (.+) (\d+)", text)

                if match:
                    team_a_name, team_b_name, card_a_input, qty_a_str, card_b_input, qty_b_str = match.groups()
                    qty_a = int(qty_a_str)
                    qty_b = int(qty_b_str)

                    if qty_a <= 0 or qty_b <= 0:
                        line_bot_api.reply_message(reply_token, TextSendMessage(text="交換數量必須為正整數。"))
                        return

                    team_a = get_team_by_name(team_a_name)
                    team_b = get_team_by_name(team_b_name)
                    if not team_a or not team_b: return
                    if team_a.id == team_b.id:
                        line_bot_api.reply_message(reply_token, TextSendMessage(text="不能和同一個隊伍交換卡牌。"))
                        return

                    card_a = find_card_by_input(db, card_a_input)
                    card_b = find_card_by_input(db, card_b_input)
                    if not card_a or not card_b:
                        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"找不到卡牌，請檢查：{card_a_input} 或 {card_b_input}"))
                        return
                    if card_a.id == card_b.id and qty_a != qty_b:
                        line_bot_api.reply_message(reply_token, TextSendMessage(text="交換相同卡牌時，數量必須一致。"))
                        return

                    # 檢查隊伍卡牌數量是否足夠
                    team_a_has_card_a = db.query(TeamCard).filter(TeamCard.team_id == team_a.id, TeamCard.card_id == card_a.id).first()
                    team_b_has_card_b = db.query(TeamCard).filter(TeamCard.team_id == team_b.id, TeamCard.card_id == card_b.id).first()

                    if not team_a_has_card_a or team_a_has_card_a.quantity < qty_a:
                        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"{team_a.name} 的 {card_a.name_zh} 數量不足。"))
                        return
                    if not team_b_has_card_b or team_b_has_card_b.quantity < qty_b:
                        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"{team_b.name} 的 {card_b.name_zh} 數量不足。"))
                        return

                    # 交易請求邏輯 (兩階段確認)
                    current_time = datetime.now()
                    one_minute_ago = current_time - timedelta(minutes=1)
                    
                    existing_request = db.query(TradeRequest).filter(
                        TradeRequest.action_type == 'team_to_team_trade',
                        TradeRequest.team_a_id == team_a.id,
                        TradeRequest.team_b_id == team_b.id,
                        TradeRequest.card_a_id == card_a.id,
                        TradeRequest.card_a_quantity == qty_a,
                        TradeRequest.card_b_id == card_b.id,
                        TradeRequest.card_b_quantity == qty_b,
                        TradeRequest.status.in_(['pending', 'confirmed_one']),
                        TradeRequest.created_at >= one_minute_ago
                    ).first()

                    if existing_request:
                        # 這是第二人確認
                        confirmed_users_list = existing_request.confirmed_by_users.split(',') if existing_request.confirmed_by_users else []
                        if user_id not in confirmed_users_list: # 確保不是同一個人重複確認
                            confirmed_users_list.append(user_id)
                            existing_request.confirmed_by_users = ",".join(confirmed_users_list)
                            existing_request.status = 'completed' # 兩人確認即完成

                            # 執行交易
                            # A隊減少卡A，增加卡B
                            if team_a_has_card_a:
                                team_a_has_card_a.quantity -= qty_a
                                if team_a_has_card_a.quantity == 0:
                                    db.delete(team_a_has_card_a)
                            
                            team_a_gets_card_b = db.query(TeamCard).filter(TeamCard.team_id == team_a.id, TeamCard.card_id == card_b.id).first()
                            if team_a_gets_card_b:
                                team_a_gets_card_b.quantity += qty_b
                            else:
                                db.add(TeamCard(team_id=team_a.id, card_id=card_b.id, quantity=qty_b))

                            # B隊減少卡B，增加卡A
                            if team_b_has_card_b:
                                team_b_has_card_b.quantity -= qty_b
                                if team_b_has_card_b.quantity == 0:
                                    db.delete(team_b_has_card_b)
                            
                            team_b_gets_card_a = db.query(TeamCard).filter(TeamCard.team_id == team_b.id, TeamCard.card_id == card_a.id).first()
                            if team_b_gets_card_a:
                                team_b_gets_card_a.quantity += qty_a
                            else:
                                db.add(TeamCard(team_id=team_b.id, card_id=card_a.id, quantity=qty_a))

                            db.commit() # 提交卡牌數量變更
                            
                            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"交換成功！{team_a.name} 與 {team_b.name} 已完成卡牌交換。"))
                            
                            # 通知發起者 (如果不是同一個用戶)
                            if existing_request.requester_user_id != user_id:
                                try:
                                    line_bot_api.push_message(existing_request.requester_user_id, TextSendMessage(text="您發起的卡牌交換已成功確認並執行！"))
                                except Exception as e:
                                    app.logger.error(f"Failed to push message to requester: {e}")
                        else:
                            line_bot_api.reply_message(reply_token, TextSendMessage(text="您已確認過此交換請求，請等待另一位夥伴確認。"))

                    else:
                        # 這是第一次發起請求
                        new_request_id = str(uuid.uuid4()) # 生成唯一的請求 ID
                        new_trade_request = TradeRequest(
                            request_id=new_request_id,
                            requester_user_id=user_id,
                            status='pending',
                            action_type='team_to_team_trade',
                            team_a_id=team_a.id,
                            team_b_id=team_b.id,
                            card_a_id=card_a.id,
                            card_a_quantity=qty_a,
                            card_b_id=card_b.id,
                            card_b_quantity=qty_b,
                            confirmed_by_users=user_id # 記錄第一個確認者
                        )
                        db.add(new_trade_request)
                        db.commit()
                        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"卡牌交換請求已發起！請在1分鐘內由另一位具有相同權限的夥伴輸入**完全相同**的指令以確認交換。"))

                else:
                    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"指令格式：交換 [隊伍A名稱] [隊伍B名稱] [卡牌A號/名] [卡牌A數量] [卡牌B號/名] [卡牌B數量]"))

            elif text == "登出":
                clear_user_session(user_id)
                line_bot_api.reply_message(reply_token, TextSendMessage(text="您已成功登出管理員模式。"))

            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="無法識別的管理員指令。請使用：新增、刪除、交換 或 登出。"))

    except Exception as e:
        app.logger.error(f"Error handling message: {e}", exc_info=True)
        line_bot_api.reply_message(reply_token, TextSendMessage(text="系統發生錯誤，請稍後再試。"))
    finally:
        db.close() # 確保資料庫會話被關閉

# --- 後台任務 (例如清除過期交易請求) ---
from apscheduler.schedulers.background import BackgroundScheduler

def cleanup_expired_trade_requests():
    """清理過期的交易請求"""
    with app.app_context(): # 確保在 Flask 應用上下文中執行
        db = next(get_db())
        try:
            current_time = datetime.now()
            # 這裡的時間差必須和指令中的「1分鐘之內」保持一致
            expired_time_limit = timedelta(minutes=1)
            expired_requests = db.query(TradeRequest).filter(
                TradeRequest.status.in_(['pending', 'confirmed_one']), # 考慮這兩種狀態
                (current_time - TradeRequest.created_at) > expired_time_limit
            ).all()

            for req in expired_requests:
                req.status = 'expired'
                app.logger.info(f"Trade request {req.request_id} expired.")
            db.commit()
        except Exception as e:
            app.logger.error(f"Error cleaning up expired requests: {e}")
        finally:
            db.close()

# 啟動後台排程器
scheduler = BackgroundScheduler()
# 每 30 秒執行一次清理任務，確保及時處理過期請求
scheduler.add_job(cleanup_expired_trade_requests, 'interval', seconds=30, id='cleanup_trades')
scheduler.start()

# --- 運行 Flask 應用 ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True)