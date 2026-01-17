"""
会话管理服务（数据库版本）
- 会话-账号绑定
- 数据库持久化
- 图片目录管理
"""
import asyncio
import time
import uuid
import logging
from pathlib import Path
from typing import Optional, List

from sqlalchemy import select, delete
from sqlalchemy.orm import selectinload

from app.config import IMAGES_DIR
from app.database import async_session_factory
from app.db_models.conversation import Conversation, ConversationMessage
from app.services.account_manager import account_manager

logger = logging.getLogger(__name__)


class ConversationManager:
    """
    会话管理器（数据库版本）

    功能：
    1. 会话-账号绑定（新会话分配账号，同一会话固定账号）
    2. 数据库持久化
    3. 图片目录管理（每个会话独立目录）
    """

    def __init__(self):
        self._lock = asyncio.Lock()

    async def create_conversation(
        self,
        name: str = "",
        model: str = "gemini-2.5-flash",
        system_prompt: Optional[str] = None,
        source: str = "web",
        user_id: Optional[int] = None,
        username: str = ""
    ) -> Conversation:
        """
        创建新会话

        Args:
            name: 会话名称
            model: 模型名称
            system_prompt: 系统提示词
            source: 会话来源（web, cli, api）
            user_id: 用户ID（用于隔离不同用户的会话）
            username: 用户名（用于搜索和展示）
        """
        async with self._lock:
            # 生成会话ID
            conv_id = f"conv_{uuid.uuid4().hex[:12]}"

            # 轮训获取账号
            logger.info(f"创建新会话，准备轮训获取账号...")
            account = await account_manager.get_next_account()
            logger.info(f"新会话分配账号: index={account.index}, team_id={account.team_id[:20]}...")

            # 创建图片目录
            image_dir = IMAGES_DIR / conv_id
            image_dir.mkdir(parents=True, exist_ok=True)

            # 创建会话
            conversation = Conversation(
                id=conv_id,
                name=name or conv_id,
                model=model,
                system_prompt=system_prompt,
                user_id=user_id,
                username=username,
                source=source,
                account_index=account.index,
                team_id=account.team_id,
                session_name="",
                image_dir=str(image_dir)
            )

            # 保存到数据库并重新查询（确保 messages 关系被加载）
            async with async_session_factory() as session:
                session.add(conversation)
                await session.commit()

                # 重新查询以加载 messages 关系
                result = await session.execute(
                    select(Conversation)
                    .options(selectinload(Conversation.messages))
                    .where(Conversation.id == conv_id)
                )
                conversation = result.scalar_one()

            logger.info(f"创建会话 {conv_id}（来源: {source}, 用户: {user_id}），绑定账号 {account.index}")
            return conversation

    async def get_conversation(self, conv_id: str, user_id: Optional[int] = None) -> Optional[Conversation]:
        """
        获取会话

        Args:
            conv_id: 会话ID
            user_id: 用户ID（如果提供，会验证会话所有权）

        Returns:
            会话对象，如果不存在或不属于该用户则返回None
        """
        async with async_session_factory() as session:
            result = await session.execute(
                select(Conversation)
                .options(selectinload(Conversation.messages))
                .where(Conversation.id == conv_id)
            )
            conv = result.scalar_one_or_none()

            if conv and user_id is not None:
                # 严格验证会话所有权（只能访问自己的会话）
                if conv.user_id != user_id:
                    logger.warning(f"用户 {user_id} 尝试访问用户 {conv.user_id} 的会话 {conv_id}")
                    return None

            return conv

    async def get_or_create_conversation(
        self,
        conv_id: Optional[str] = None,
        name: str = "",
        model: str = "gemini-2.5-flash",
        source: str = "web",
        user_id: Optional[int] = None,
        username: str = ""
    ) -> Conversation:
        """
        获取或创建会话

        如果 conv_id 存在且有效，返回现有会话
        否则创建新会话
        """
        if conv_id:
            conv = await self.get_conversation(conv_id, user_id)
            if conv:
                # 检查绑定的账号是否仍可用
                account = None
                if conv.team_id:
                    account = account_manager.get_account_by_team_id(conv.team_id)
                if not account:
                    account = account_manager.get_account(conv.account_index)

                if account and account.is_usable():
                    # 更新绑定信息并重新查询
                    async with async_session_factory() as session:
                        result = await session.execute(
                            select(Conversation)
                            .options(selectinload(Conversation.messages))
                            .where(Conversation.id == conv_id)
                        )
                        conv = result.scalar_one_or_none()
                        if conv:
                            conv.account_index = account.index
                            conv.team_id = account.team_id
                            conv.touch()
                            await session.commit()
                            # 重新查询确保 messages 关系仍然可用
                            result = await session.execute(
                                select(Conversation)
                                .options(selectinload(Conversation.messages))
                                .where(Conversation.id == conv_id)
                            )
                            conv = result.scalar_one()
                    return conv

                # 账号不可用，迁移到新账号
                return await self._migrate_conversation(conv)

        # 创建新会话
        return await self.create_conversation(name=name, model=model, source=source, user_id=user_id, username=username)

    async def _migrate_conversation(self, conv: Conversation) -> Conversation:
        """迁移会话到新账号"""
        async with self._lock:
            account = await account_manager.get_next_account()
            conv_id = conv.id

            async with async_session_factory() as session:
                # 重新查询会话
                result = await session.execute(
                    select(Conversation)
                    .options(selectinload(Conversation.messages))
                    .where(Conversation.id == conv_id)
                )
                conv = result.scalar_one_or_none()
                if not conv:
                    raise ValueError(f"会话 {conv_id} 不存在")

                conv.account_index = account.index
                conv.team_id = account.team_id
                conv.session_name = ""
                conv.touch()
                await session.commit()

                # 重新查询确保 messages 关系仍然可用
                result = await session.execute(
                    select(Conversation)
                    .options(selectinload(Conversation.messages))
                    .where(Conversation.id == conv_id)
                )
                conv = result.scalar_one()

            logger.info(f"会话 {conv_id} 迁移到账号 {account.index}")
            return conv

    async def update_binding_session(self, conv_id: str, session_name: str):
        """更新会话的 Gemini Session 名称"""
        async with async_session_factory() as session:
            result = await session.execute(
                select(Conversation).where(Conversation.id == conv_id)
            )
            conv = result.scalar_one_or_none()
            if conv:
                conv.session_name = session_name
                await session.commit()

    async def update_binding_account(self, conv_id: str, account_index: int):
        """更新会话绑定的账号"""
        async with async_session_factory() as session:
            result = await session.execute(
                select(Conversation).where(Conversation.id == conv_id)
            )
            conv = result.scalar_one_or_none()
            if conv:
                old_index = conv.account_index
                conv.account_index = account_index
                conv.session_name = ""
                await session.commit()
                logger.info(f"会话 {conv_id} 账号切换: {old_index} -> {account_index}")

    async def add_message(
        self,
        conv_id: str,
        role: str,
        content: str,
        images: List[str] = None
    ):
        """添加消息到会话"""
        async with async_session_factory() as session:
            result = await session.execute(
                select(Conversation).where(Conversation.id == conv_id)
            )
            conv = result.scalar_one_or_none()

            if not conv:
                logger.warning(f"会话 {conv_id} 不存在，无法添加消息")
                return

            # 确保图片目录存在
            image_dir = Path(conv.image_dir)
            if not image_dir.exists():
                image_dir.mkdir(parents=True, exist_ok=True)

            # 创建消息
            message = ConversationMessage(
                conversation_id=conv_id,
                role=role,
                content=content,
                images=images or []
            )
            session.add(message)

            # 更新会话时间
            conv.updated_at = time.time()
            conv.last_active_at = time.time()

            await session.commit()
            logger.info(f"消息已保存: conv_id={conv_id}, role={role}")

    async def add_image(self, conv_id: str, filename: str):
        """记录会话生成的图片"""
        async with async_session_factory() as session:
            result = await session.execute(
                select(Conversation).where(Conversation.id == conv_id)
            )
            conv = result.scalar_one_or_none()
            if conv:
                conv.image_count += 1
                await session.commit()

    def get_image_dir(self, conv_id: str) -> Optional[Path]:
        """获取会话的图片目录"""
        return IMAGES_DIR / conv_id

    async def delete_conversation(self, conv_id: str) -> bool:
        """删除会话"""
        async with async_session_factory() as session:
            result = await session.execute(
                select(Conversation).where(Conversation.id == conv_id)
            )
            conv = result.scalar_one_or_none()

            if not conv:
                return False

            # 删除图片目录
            image_dir = Path(conv.image_dir) if conv.image_dir else None
            if image_dir and image_dir.exists():
                import shutil
                shutil.rmtree(image_dir)

            # 删除数据库记录（级联删除消息）
            await session.execute(
                delete(Conversation).where(Conversation.id == conv_id)
            )
            await session.commit()

            logger.info(f"删除会话 {conv_id}")
            return True

    async def list_conversations(self, include_api: bool = False, user_id: Optional[int] = None) -> List[dict]:
        """
        获取会话列表

        Args:
            include_api: 是否包含 API 来源的会话
            user_id: 用户ID（如果提供，只返回该用户的会话）
        """
        async with async_session_factory() as session:
            query = select(Conversation).options(selectinload(Conversation.messages))

            # 过滤来源
            if not include_api:
                query = query.where(Conversation.source != "api")

            # 过滤用户（严格隔离，只返回该用户的会话）
            if user_id is not None:
                query = query.where(Conversation.user_id == user_id)

            # 按更新时间倒序
            query = query.order_by(Conversation.updated_at.desc())

            result = await session.execute(query)
            conversations = result.scalars().all()

            return [conv.to_summary_dict() for conv in conversations]

    async def cleanup_expired(self, max_age_seconds: int = 86400):
        """清理过期会话"""
        cutoff_time = time.time() - max_age_seconds

        async with async_session_factory() as session:
            result = await session.execute(
                select(Conversation).where(Conversation.last_active_at < cutoff_time)
            )
            expired_convs = result.scalars().all()

            for conv in expired_convs:
                # 删除图片目录
                if conv.image_dir:
                    image_dir = Path(conv.image_dir)
                    if image_dir.exists():
                        import shutil
                        shutil.rmtree(image_dir)

            # 删除过期会话
            await session.execute(
                delete(Conversation).where(Conversation.last_active_at < cutoff_time)
            )
            await session.commit()

            if expired_convs:
                logger.info(f"清理了 {len(expired_convs)} 个过期会话")

    async def get_status(self) -> dict:
        """获取会话管理器状态"""
        conversations = await self.list_conversations()
        return {
            "total_conversations": len(conversations),
            "conversations": conversations
        }


# 全局会话管理器实例
conversation_manager = ConversationManager()
