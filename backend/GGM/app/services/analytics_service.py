"""
统计分析服务
- 收集用户使用数据
- 生成统计报告
- 支持时间范围查询
"""
import time
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from collections import defaultdict

from sqlalchemy import select, func, and_, case

from app.database import async_session_factory
from app.db_models.usage_record import UsageRecord
from app.db_models.user_quota import UserQuota
from app.db_models.conversation import Conversation, ConversationMessage
from app.db_models.api_token import ApiToken

logger = logging.getLogger(__name__)


class AnalyticsService:
    """统计分析服务"""

    async def record_usage(
        self,
        user_id: Optional[int] = None,
        username: str = "",
        model: str = "",
        source: str = "web",
        conversation_id: Optional[str] = None,
        api_token: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        success: bool = True,
        error_type: Optional[str] = None
    ):
        """
        记录一次 API 使用

        Args:
            user_id: 用户ID
            username: 用户名
            model: 模型名称
            source: 来源 (web, cli, api)
            conversation_id: 会话ID
            api_token: API Token（仅保存前缀）
            prompt_tokens: 输入 token 数
            completion_tokens: 输出 token 数
            success: 是否成功
            error_type: 错误类型
        """
        token_prefix = api_token[:8] if api_token and len(api_token) > 8 else api_token

        async with async_session_factory() as session:
            record = UsageRecord(
                user_id=user_id,
                username=username,
                model=model,
                source=source,
                conversation_id=conversation_id,
                api_token_prefix=token_prefix,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                success=success,
                error_type=error_type
            )
            session.add(record)
            await session.commit()

    async def get_overview(self) -> Dict[str, Any]:
        """
        获取总览统计数据
        """
        async with async_session_factory() as session:
            # 总用户数
            user_count_result = await session.execute(
                select(func.count(UserQuota.user_id))
            )
            total_users = user_count_result.scalar() or 0

            # 活跃用户数（有使用记录的）
            active_users_result = await session.execute(
                select(func.count(func.distinct(UsageRecord.user_id))).where(
                    UsageRecord.user_id.isnot(None)
                )
            )
            active_users = active_users_result.scalar() or 0

            # 总请求数
            total_requests_result = await session.execute(
                select(func.count(UsageRecord.id))
            )
            total_requests = total_requests_result.scalar() or 0

            # 总 token 消耗
            total_tokens_result = await session.execute(
                select(func.sum(UsageRecord.total_tokens))
            )
            total_tokens = total_tokens_result.scalar() or 0

            # 总会话数
            total_conversations_result = await session.execute(
                select(func.count(Conversation.id))
            )
            total_conversations = total_conversations_result.scalar() or 0

            # 成功率
            success_count_result = await session.execute(
                select(func.count(UsageRecord.id)).where(UsageRecord.success == True)
            )
            success_count = success_count_result.scalar() or 0
            success_rate = (success_count / total_requests * 100) if total_requests > 0 else 100

            # 今日统计
            today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

            today_requests_result = await session.execute(
                select(func.count(UsageRecord.id)).where(
                    UsageRecord.timestamp >= today_start
                )
            )
            today_requests = today_requests_result.scalar() or 0

            today_users_result = await session.execute(
                select(func.count(func.distinct(UsageRecord.user_id))).where(
                    and_(
                        UsageRecord.timestamp >= today_start,
                        UsageRecord.user_id.isnot(None)
                    )
                )
            )
            today_active_users = today_users_result.scalar() or 0

            today_tokens_result = await session.execute(
                select(func.sum(UsageRecord.total_tokens)).where(
                    UsageRecord.timestamp >= today_start
                )
            )
            today_tokens = today_tokens_result.scalar() or 0

        return {
            "total_users": total_users,
            "active_users": active_users,
            "total_requests": total_requests,
            "total_tokens": total_tokens,
            "total_conversations": total_conversations,
            "success_rate": round(success_rate, 2),
            "today": {
                "requests": today_requests,
                "active_users": today_active_users,
                "tokens": today_tokens
            }
        }

    async def get_usage_trend(self, days: int = 7) -> List[Dict[str, Any]]:
        """
        获取使用趋势（按天统计）

        Args:
            days: 统计天数

        Returns:
            每天的统计数据列表
        """
        results = []
        now = datetime.now()

        async with async_session_factory() as session:
            for i in range(days - 1, -1, -1):
                day = now - timedelta(days=i)
                day_start = day.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
                day_end = day.replace(hour=23, minute=59, second=59, microsecond=999999).timestamp()

                # 请求数
                requests_result = await session.execute(
                    select(func.count(UsageRecord.id)).where(
                        and_(
                            UsageRecord.timestamp >= day_start,
                            UsageRecord.timestamp <= day_end
                        )
                    )
                )
                requests = requests_result.scalar() or 0

                # 活跃用户数
                users_result = await session.execute(
                    select(func.count(func.distinct(UsageRecord.user_id))).where(
                        and_(
                            UsageRecord.timestamp >= day_start,
                            UsageRecord.timestamp <= day_end,
                            UsageRecord.user_id.isnot(None)
                        )
                    )
                )
                users = users_result.scalar() or 0

                # Token 消耗
                tokens_result = await session.execute(
                    select(func.sum(UsageRecord.total_tokens)).where(
                        and_(
                            UsageRecord.timestamp >= day_start,
                            UsageRecord.timestamp <= day_end
                        )
                    )
                )
                tokens = tokens_result.scalar() or 0

                # 新会话数
                conversations_result = await session.execute(
                    select(func.count(Conversation.id)).where(
                        and_(
                            Conversation.created_at >= day_start,
                            Conversation.created_at <= day_end
                        )
                    )
                )
                conversations = conversations_result.scalar() or 0

                results.append({
                    "date": day.strftime("%Y-%m-%d"),
                    "requests": requests,
                    "active_users": users,
                    "tokens": tokens,
                    "new_conversations": conversations
                })

        return results

    async def get_hourly_distribution(self, days: int = 7) -> List[Dict[str, Any]]:
        """
        获取每小时请求分布（最近N天的平均值）

        Args:
            days: 统计天数

        Returns:
            24小时的平均请求数
        """
        start_time = (datetime.now() - timedelta(days=days)).timestamp()

        async with async_session_factory() as session:
            # 获取所有记录的时间戳
            result = await session.execute(
                select(UsageRecord.timestamp).where(
                    UsageRecord.timestamp >= start_time
                )
            )
            timestamps = [r[0] for r in result.all()]

        # 按小时统计
        hourly_counts = defaultdict(int)
        for ts in timestamps:
            hour = datetime.fromtimestamp(ts).hour
            hourly_counts[hour] += 1

        # 计算平均值
        return [
            {
                "hour": h,
                "requests": round(hourly_counts.get(h, 0) / days, 1)
            }
            for h in range(24)
        ]

    async def get_model_distribution(self, days: int = 30) -> List[Dict[str, Any]]:
        """
        获取模型使用分布

        Args:
            days: 统计天数
        """
        start_time = (datetime.now() - timedelta(days=days)).timestamp()

        async with async_session_factory() as session:
            result = await session.execute(
                select(
                    UsageRecord.model,
                    func.count(UsageRecord.id).label('count'),
                    func.sum(UsageRecord.total_tokens).label('tokens')
                ).where(
                    UsageRecord.timestamp >= start_time
                ).group_by(UsageRecord.model).order_by(func.count(UsageRecord.id).desc())
            )

            return [
                {
                    "model": row[0] or "unknown",
                    "requests": row[1],
                    "tokens": row[2] or 0
                }
                for row in result.all()
            ]

    async def get_source_distribution(self, days: int = 30) -> List[Dict[str, Any]]:
        """
        获取来源分布

        Args:
            days: 统计天数
        """
        start_time = (datetime.now() - timedelta(days=days)).timestamp()

        async with async_session_factory() as session:
            result = await session.execute(
                select(
                    UsageRecord.source,
                    func.count(UsageRecord.id).label('count')
                ).where(
                    UsageRecord.timestamp >= start_time
                ).group_by(UsageRecord.source)
            )

            return [
                {"source": row[0] or "unknown", "requests": row[1]}
                for row in result.all()
            ]

    async def get_top_users(self, limit: int = 10, days: int = 30) -> List[Dict[str, Any]]:
        """
        获取使用量最高的用户

        Args:
            limit: 返回数量
            days: 统计天数
        """
        start_time = (datetime.now() - timedelta(days=days)).timestamp()

        async with async_session_factory() as session:
            result = await session.execute(
                select(
                    UsageRecord.user_id,
                    UsageRecord.username,
                    func.count(UsageRecord.id).label('requests'),
                    func.sum(UsageRecord.total_tokens).label('tokens')
                ).where(
                    and_(
                        UsageRecord.timestamp >= start_time,
                        UsageRecord.user_id.isnot(None)
                    )
                ).group_by(
                    UsageRecord.user_id, UsageRecord.username
                ).order_by(
                    func.count(UsageRecord.id).desc()
                ).limit(limit)
            )

            return [
                {
                    "user_id": row[0],
                    "username": row[1] or f"User-{row[0]}",
                    "requests": row[2],
                    "tokens": row[3] or 0
                }
                for row in result.all()
            ]

    async def get_user_detail(self, user_id: int, days: int = 30) -> Dict[str, Any]:
        """
        获取单个用户的详细统计

        Args:
            user_id: 用户ID
            days: 统计天数
        """
        start_time = (datetime.now() - timedelta(days=days)).timestamp()

        async with async_session_factory() as session:
            # 基础统计
            stats_result = await session.execute(
                select(
                    func.count(UsageRecord.id),
                    func.sum(UsageRecord.total_tokens),
                    func.avg(UsageRecord.total_tokens)
                ).where(
                    and_(
                        UsageRecord.user_id == user_id,
                        UsageRecord.timestamp >= start_time
                    )
                )
            )
            stats = stats_result.one()

            # 用户配额
            quota_result = await session.execute(
                select(UserQuota).where(UserQuota.user_id == user_id)
            )
            quota = quota_result.scalar_one_or_none()

            # 会话数
            conversations_result = await session.execute(
                select(func.count(Conversation.id)).where(
                    Conversation.user_id == user_id
                )
            )
            conversations = conversations_result.scalar() or 0

            # 每日使用趋势
            daily_trend = []
            now = datetime.now()
            for i in range(6, -1, -1):
                day = now - timedelta(days=i)
                day_start = day.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
                day_end = day.replace(hour=23, minute=59, second=59, microsecond=999999).timestamp()

                day_result = await session.execute(
                    select(func.count(UsageRecord.id)).where(
                        and_(
                            UsageRecord.user_id == user_id,
                            UsageRecord.timestamp >= day_start,
                            UsageRecord.timestamp <= day_end
                        )
                    )
                )
                daily_trend.append({
                    "date": day.strftime("%m-%d"),
                    "requests": day_result.scalar() or 0
                })

        return {
            "user_id": user_id,
            "total_requests": stats[0] or 0,
            "total_tokens": stats[1] or 0,
            "avg_tokens_per_request": round(stats[2] or 0, 1),
            "conversations": conversations,
            "quota": quota.to_dict() if quota else None,
            "daily_trend": daily_trend
        }

    async def get_error_stats(self, days: int = 7) -> Dict[str, Any]:
        """
        获取错误统计

        Args:
            days: 统计天数
        """
        start_time = (datetime.now() - timedelta(days=days)).timestamp()

        async with async_session_factory() as session:
            # 总请求数和失败数
            total_result = await session.execute(
                select(
                    func.count(UsageRecord.id),
                    func.sum(case((UsageRecord.success == False, 1), else_=0))
                ).where(UsageRecord.timestamp >= start_time)
            )
            total_stats = total_result.one()

            # 错误类型分布
            error_types_result = await session.execute(
                select(
                    UsageRecord.error_type,
                    func.count(UsageRecord.id)
                ).where(
                    and_(
                        UsageRecord.timestamp >= start_time,
                        UsageRecord.success == False,
                        UsageRecord.error_type.isnot(None)
                    )
                ).group_by(UsageRecord.error_type)
            )

        total_requests = total_stats[0] or 0
        failed_requests = total_stats[1] or 0

        return {
            "total_requests": total_requests,
            "failed_requests": failed_requests,
            "success_rate": round((total_requests - failed_requests) / total_requests * 100, 2) if total_requests > 0 else 100,
            "error_types": [
                {"type": row[0], "count": row[1]}
                for row in error_types_result.all()
            ]
        }

    async def get_recent_activity(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        获取最近的活动记录

        Args:
            limit: 返回数量
        """
        async with async_session_factory() as session:
            result = await session.execute(
                select(UsageRecord).order_by(
                    UsageRecord.timestamp.desc()
                ).limit(limit)
            )

            return [record.to_dict() for record in result.scalars().all()]


# 全局实例
analytics_service = AnalyticsService()
