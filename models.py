# models.py
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, UniqueConstraint, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base # 從 database.py 導入 Base

class Team(Base):
    __tablename__ = 'teams'
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False) # 儲存雜湊後的密碼

    cards = relationship("TeamCard", back_populates="team") # 關聯 TeamCard

class Card(Base):
    __tablename__ = 'cards'
    id = Column(Integer, primary_key=True, index=True)
    card_number = Column(String, unique=True, index=True, nullable=False)
    name_zh = Column(String, nullable=False)
    name_en = Column(String, nullable=True) # 英文名稱可能為空

    team_cards = relationship("TeamCard", back_populates="card") # 關聯 TeamCard

class TeamCard(Base):
    __tablename__ = 'team_cards'
    id = Column(Integer, primary_key=True, index=True)
    team_id = Column(Integer, ForeignKey('teams.id'), nullable=False)
    card_id = Column(Integer, ForeignKey('cards.id'), nullable=False)
    quantity = Column(Integer, default=0, nullable=False)

    team = relationship("Team", back_populates="cards")
    card = relationship("Card", back_populates="team_cards")

    __table_args__ = (UniqueConstraint('team_id', 'card_id', name='_team_card_uc'),) # 確保每個隊伍的每種卡牌只有一條記錄

class AdminPassword(Base):
    __tablename__ = 'admin_passwords'
    id = Column(Integer, primary_key=True, index=True)
    role = Column(String, nullable=False) # 'game_master', 'organizer'
    password_hash = Column(String, nullable=False)
    # team_scope 可以儲存 JSON 格式的隊伍 ID 列表或逗號分隔字串，例如 '1,2,3'
    # 這裡暫用 Text，實際應用中可考慮 JSONB 或更精確的關聯表
    team_scope = Column(Text, nullable=True) # 關聯的隊伍 ID 列表，用於限制特定關主只能操作某些隊伍

class TradeRequest(Base):
    __tablename__ = 'trade_requests'
    id = Column(Integer, primary_key=True, index=True)
    request_id = Column(String, unique=True, nullable=False) # 請求的唯一 ID
    requester_user_id = Column(String, nullable=False) # 發起請求的 LINE User ID
    status = Column(String, default='pending', nullable=False) # 'pending', 'confirmed_one', 'confirmed_both', 'expired', 'completed'
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    confirmed_by_users = Column(Text, default='', nullable=False) # 已確認的 LINE User ID，以逗號分隔

    # 針對卡牌交換的詳細資訊，這裡直接儲存，或可以設計為多對多關係
    # 簡化起見，直接儲存交換相關的數據
    team_a_id = Column(Integer, ForeignKey('teams.id'), nullable=True) # 僅在交換類型為 team_to_team 時使用
    team_b_id = Column(Integer, ForeignKey('teams.id'), nullable=True)
    card_a_id = Column(Integer, ForeignKey('cards.id'), nullable=True)
    card_a_quantity = Column(Integer, nullable=True)
    card_b_id = Column(Integer, ForeignKey('cards.id'), nullable=True)
    card_b_quantity = Column(Integer, nullable=True)

    # 考慮主辦方/關主新增或刪除卡牌的臨時請求
    action_type = Column(String, nullable=False) # 例如: 'team_card_add', 'team_card_remove', 'team_to_team_trade'
    target_team_id = Column(Integer, ForeignKey('teams.id'), nullable=True) # 用於 add/remove card
    target_card_id = Column(Integer, ForeignKey('cards.id'), nullable=True) # 用於 add/remove card
    target_quantity = Column(Integer, nullable=True) # 用於 add/remove card

    team_a = relationship("Team", foreign_keys=[team_a_id])
    team_b = relationship("Team", foreign_keys=[team_b_id])
    card_a = relationship("Card", foreign_keys=[card_a_id])
    card_b = relationship("Card", foreign_keys=[card_b_id])
    target_team = relationship("Team", foreign_keys=[target_team_id])
    target_card = relationship("Card", foreign_keys=[target_card_id])