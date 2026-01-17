"""
Conversation Database Model
- 会话和消息存储
"""
import time
from typing import Optional, List
from sqlalchemy import String, Integer, Float, Text, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Conversation(Base):
    """会话表"""
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    model: Mapped[str] = mapped_column(String(100), default="gemini-2.5-flash")
    system_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 用户ID（用于隔离不同用户的会话）
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    # 用户名（用于搜索和展示）
    username: Mapped[str] = mapped_column(String(100), default="", index=True)

    # 会话来源: web, cli, api
    source: Mapped[str] = mapped_column(String(20), default="web")

    # 绑定信息
    account_index: Mapped[int] = mapped_column(Integer, default=0)
    team_id: Mapped[str] = mapped_column(String(100), default="")
    session_name: Mapped[str] = mapped_column(String(200), default="")
    image_dir: Mapped[str] = mapped_column(String(500), default="")
    image_count: Mapped[int] = mapped_column(Integer, default=0)

    # 时间戳
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)
    last_active_at: Mapped[float] = mapped_column(Float, default=time.time)

    # 关联消息
    messages: Mapped[List["ConversationMessage"]] = relationship(
        "ConversationMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ConversationMessage.timestamp"
    )

    def touch(self):
        """更新活跃时间"""
        self.last_active_at = time.time()
        self.updated_at = time.time()

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "id": self.id,
            "name": self.name,
            "model": self.model,
            "system_prompt": self.system_prompt,
            "user_id": self.user_id,
            "username": self.username,
            "source": self.source,
            "account_index": self.account_index,
            "team_id": self.team_id,
            "session_name": self.session_name,
            "image_dir": self.image_dir,
            "image_count": self.image_count,
            "message_count": len(self.messages) if self.messages else 0,
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }

    def to_summary_dict(self) -> dict:
        """转换为摘要字典"""
        from datetime import datetime
        display_name = self.name
        if not display_name or display_name == self.id or display_name.startswith("conv_"):
            # 尝试从第一条用户消息生成名称
            if self.messages:
                for msg in self.messages:
                    if msg.role == "user" and msg.content:
                        content = msg.content.strip()
                        display_name = content[:30] + "..." if len(content) > 30 else content
                        break

        return {
            "id": self.id,
            "name": display_name or self.id,
            "model": self.model,
            "user_id": self.user_id,
            "username": self.username,
            "message_count": len(self.messages) if self.messages else 0,
            "created_at": datetime.fromtimestamp(self.created_at).isoformat(),
            "updated_at": datetime.fromtimestamp(self.updated_at).isoformat(),
            "has_binding": bool(self.team_id),
            "image_count": self.image_count
        }


class ConversationMessage(Base):
    """会话消息表"""
    __tablename__ = "conversation_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True
    )
    role: Mapped[str] = mapped_column(String(20))  # user / assistant / system
    content: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[float] = mapped_column(Float, default=time.time)
    images: Mapped[Optional[str]] = mapped_column(JSON, nullable=True)  # JSON array of image filenames

    # 关联会话
    conversation: Mapped["Conversation"] = relationship(
        "Conversation",
        back_populates="messages"
    )

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "images": self.images or []
        }
