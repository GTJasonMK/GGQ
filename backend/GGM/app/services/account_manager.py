"""
账号管理服务
- 多账号轮训
- 冷却机制
- 故障转移
- 凭证自动刷新
"""
import asyncio
import time
import logging
from typing import Optional, List, Tuple
from datetime import datetime, timezone, timedelta

from app.config import config_manager, AccountConfig
from app.models.account import Account, AccountState, CooldownReason

logger = logging.getLogger(__name__)


def seconds_until_pt_midnight() -> int:
    """计算距离下一个太平洋时间午夜的秒数（Google配额重置时间）"""
    try:
        from zoneinfo import ZoneInfo
        pt_tz = ZoneInfo("America/Los_Angeles")
        now_pt = datetime.now(pt_tz)
    except ImportError:
        # 兼容旧版Python
        now_utc = datetime.now(timezone.utc)
        now_pt = now_utc - timedelta(hours=8)

    tomorrow = (now_pt + timedelta(days=1)).date()
    midnight_pt = datetime.combine(tomorrow, datetime.min.time())
    if hasattr(now_pt, 'tzinfo') and now_pt.tzinfo:
        midnight_pt = midnight_pt.replace(tzinfo=now_pt.tzinfo)

    delta = (midnight_pt - now_pt).total_seconds()
    return max(0, int(delta))


# 健康度评分权重配置
class HealthScoreConfig:
    """健康度评分配置"""
    BASE_SCORE = 100                    # 基础分
    JWT_CACHE_BONUS = 20                # JWT缓存加成
    SESSION_CACHE_BONUS = 10            # Session缓存加成
    FRESH_CREDENTIAL_BONUS = 15         # 凭据新鲜度加成（1小时内刷新）
    SUCCESS_RATE_WEIGHT = 50            # 成功率权重（乘以失败率作为惩罚）
    CONSECUTIVE_ERROR_PENALTY = 15      # 每次连续错误的惩罚
    CONSECUTIVE_SUCCESS_BONUS = 2       # 每次连续成功的加成（最多+20）
    CONCURRENT_PENALTY = 10             # 每个并发请求的惩罚
    RESPONSE_TIME_PENALTY = 0.01        # 响应时间惩罚（每毫秒）
    MAX_CONSECUTIVE_SUCCESS_BONUS = 20  # 连续成功加成上限
    RECENT_ERROR_PENALTY = 25           # 最近5分钟内有错误的惩罚
    RECENT_ERROR_WINDOW = 300           # 最近错误窗口（秒）


class AccountManager:
    """
    账号管理器

    功能：
    1. 智能账号选择（基于健康度评分）
    2. 冷却机制（错误时临时禁用账号）
    3. 故障转移（自动切换到可用账号）
    4. 凭证自动检测和刷新
    5. 并发负载均衡
    """

    def __init__(self):
        self._accounts: List[Account] = []
        self._lock = asyncio.Lock()
        self._credential_check_lock = asyncio.Lock()
        self._last_credential_check: dict = {}  # account_index -> timestamp
        self._credential_check_interval = 300  # 每 5 分钟最多检查一次凭证

    def load_accounts(self):
        """从配置加载账号"""
        config = config_manager.config
        self._accounts = []

        for i, acc_config in enumerate(config.accounts):
            account = Account(
                index=i,
                team_id=acc_config.team_id,
                csesidx=acc_config.csesidx,
                secure_c_ses=acc_config.secure_c_ses,
                host_c_oses=acc_config.host_c_oses,
                user_agent=acc_config.user_agent,
                note=acc_config.note,
                available=acc_config.available,
                refresh_time=acc_config.refresh_time,
                state=AccountState()
            )
            self._accounts.append(account)

        logger.info(f"已加载 {len(self._accounts)} 个账号")

    @property
    def accounts(self) -> List[Account]:
        """获取所有账号"""
        return self._accounts

    def get_account(self, index: int) -> Optional[Account]:
        """获取指定索引的账号"""
        if 0 <= index < len(self._accounts):
            return self._accounts[index]
        return None

    def get_account_by_team_id(self, team_id: str) -> Optional[Account]:
        """通过 team_id 获取账号（更可靠的方式）"""
        for acc in self._accounts:
            if acc.team_id == team_id:
                return acc
        return None

    def reload_account(self, index: int):
        """
        重新从配置加载指定账号的信息

        用于凭据刷新后同步更新运行时的账号状态
        """
        if not (0 <= index < len(self._accounts)):
            return

        acc_config = config_manager.get_account(index)
        if not acc_config:
            return

        account = self._accounts[index]

        # 更新凭据字段（保留运行时状态）
        account.team_id = acc_config.team_id
        account.csesidx = acc_config.csesidx
        account.secure_c_ses = acc_config.secure_c_ses
        account.host_c_oses = acc_config.host_c_oses
        account.available = acc_config.available
        account.refresh_time = acc_config.refresh_time

        # 清除缓存的 JWT（因为凭据已更新）
        account.state.jwt = None
        account.state.jwt_expires_at = 0
        account.state.session_name = None

        # 清除冷却状态（凭据已刷新）
        account.state.cooldown_until = None
        account.state.cooldown_reason = None

        logger.info(f"账号 {index} ({account.note}) 凭据已同步更新, refresh_time={account.refresh_time}")

    def get_available_accounts(self, skip_invalid: bool = True) -> List[Account]:
        """
        获取所有可用账号

        Args:
            skip_invalid: 是否跳过已知凭证无效的账号
        """
        available = [acc for acc in self._accounts if acc.is_usable()]

        if skip_invalid:
            # 过滤掉已知凭证无效的账号
            try:
                from app.services.credential_service import credential_service
                available = [
                    acc for acc in available
                    if not credential_service.is_known_invalid(acc.index)
                ]
            except ImportError:
                pass

        return available

    def get_account_count(self) -> Tuple[int, int]:
        """获取账号数量统计 (total, available)"""
        total = len(self._accounts)
        available = len(self.get_available_accounts())
        return total, available

    def calculate_health_score(self, account: Account) -> float:
        """
        计算账号健康度分数

        评分因素:
        - 基础分: 100
        - JWT缓存: +20 (如果有效)
        - Session缓存: +10 (如果存在)
        - 凭据新鲜度: +15 (1小时内刷新)
        - 成功率: -50 * 失败率
        - 连续错误: -15 * 次数
        - 连续成功: +2 * 次数 (最多+20)
        - 并发请求: -10 * 当前数量
        - 最近错误: -25 (5分钟内有错误)
        - 响应时间: -0.01 * 平均毫秒数

        Returns:
            健康度分数 (越高越好)
        """
        cfg = HealthScoreConfig
        state = account.state
        score = cfg.BASE_SCORE

        # JWT缓存加成
        if state.is_jwt_valid():
            score += cfg.JWT_CACHE_BONUS

        # Session缓存加成
        if state.session_name:
            score += cfg.SESSION_CACHE_BONUS

        # 凭据新鲜度加成
        refresh_dt = account.get_refresh_datetime()
        if refresh_dt:
            # 统一时区处理：移除时区信息后比较
            if refresh_dt.tzinfo is not None:
                refresh_dt = refresh_dt.replace(tzinfo=None)
            age_hours = (datetime.now() - refresh_dt).total_seconds() / 3600
            if age_hours < 1:
                score += cfg.FRESH_CREDENTIAL_BONUS

        # 成功率惩罚
        failure_rate = 1 - state.get_success_rate()
        score -= cfg.SUCCESS_RATE_WEIGHT * failure_rate

        # 连续错误惩罚
        score -= cfg.CONSECUTIVE_ERROR_PENALTY * state.consecutive_errors

        # 连续成功加成（有上限）
        success_bonus = min(
            cfg.CONSECUTIVE_SUCCESS_BONUS * state.consecutive_successes,
            cfg.MAX_CONSECUTIVE_SUCCESS_BONUS
        )
        score += success_bonus

        # 并发请求惩罚
        score -= cfg.CONCURRENT_PENALTY * state.concurrent_requests

        # 最近错误惩罚
        if state.last_error_at:
            time_since_error = time.time() - state.last_error_at
            if time_since_error < cfg.RECENT_ERROR_WINDOW:
                score -= cfg.RECENT_ERROR_PENALTY

        # 响应时间惩罚（越慢越低分）
        avg_response = state.get_avg_response_time()
        if avg_response > 0:
            score -= cfg.RESPONSE_TIME_PENALTY * avg_response

        return score

    def get_accounts_with_scores(self, accounts: List[Account] = None) -> List[Tuple[Account, float]]:
        """
        获取账号及其健康度分数列表

        Args:
            accounts: 要评分的账号列表，None则使用所有可用账号

        Returns:
            [(账号, 分数)] 列表，按分数降序排列
        """
        if accounts is None:
            accounts = self.get_available_accounts(skip_invalid=True)

        scored = [(acc, self.calculate_health_score(acc)) for acc in accounts]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    async def get_next_account(self) -> Account:
        """
        智能选择下一个可用账号

        基于健康度评分选择最优账号，考虑：
        - JWT/Session缓存状态
        - 成功率和连续错误
        - 并发负载
        - 响应时间

        Raises:
            NoAvailableAccountError: 没有可用账号
        """
        async with self._lock:
            # 获取有效的可用账号（跳过已知无效的）
            available = self.get_available_accounts(skip_invalid=True)

            if not available:
                # 如果没有有效账号，但有正在刷新的账号，提示等待
                try:
                    from app.services.credential_service import credential_service
                    status = credential_service.get_status()
                    if status.get("refreshing_accounts") or status.get("queue_size", 0) > 0:
                        raise NoAvailableAccountError(
                            f"所有账号凭证无效，正在后台刷新中，请稍后重试"
                        )
                except ImportError:
                    pass

                # 查找最近将解除冷却的账号
                next_cooldown = self._get_next_cooldown_info()
                if next_cooldown:
                    remaining = next_cooldown["remaining"]
                    raise NoAvailableAccountError(
                        f"没有可用账号，最近的账号将在 {remaining} 秒后解除冷却"
                    )
                raise NoAvailableAccountError("没有可用账号")

            # 基于健康度评分选择账号
            scored_accounts = self.get_accounts_with_scores(available)
            best_account, best_score = scored_accounts[0]

            # 记录选择日志
            logger.info(
                f"智能选择账号: index={best_account.index}, note={best_account.note}, "
                f"score={best_score:.1f}, concurrent={best_account.state.concurrent_requests}, "
                f"success_rate={best_account.state.get_success_rate()*100:.1f}%, "
                f"has_jwt={best_account.state.is_jwt_valid()}, "
                f"available_count={len(available)}"
            )

            # 如果有多个账号分数接近，记录次优选择供参考
            if len(scored_accounts) > 1:
                second_account, second_score = scored_accounts[1]
                if best_score - second_score < 10:  # 分数差距小于10
                    logger.debug(
                        f"次优账号: index={second_account.index}, score={second_score:.1f}"
                    )

            return best_account

    async def get_account_for_conversation(self, preferred_index: Optional[int] = None) -> Account:
        """
        为会话获取账号

        优先使用指定账号（粘性会话），如果不可用或凭证无效则轮训获取新账号

        Args:
            preferred_index: 首选账号索引

        Returns:
            可用的账号
        """
        # 如果有首选账号且可用，使用它
        if preferred_index is not None:
            account = self.get_account(preferred_index)
            if account and account.is_usable():
                # 检查凭证是否已知无效
                try:
                    from app.services.credential_service import credential_service
                    if credential_service.is_known_invalid(preferred_index):
                        logger.info(f"首选账号 {preferred_index} 凭证无效，切换到下一个账号")
                        # 触发后台刷新
                        asyncio.create_task(credential_service.queue_refresh(preferred_index))
                    else:
                        return account
                except ImportError:
                    return account
            else:
                logger.info(f"首选账号 {preferred_index} 不可用，切换到下一个账号")

        # 否则轮训获取
        return await self.get_next_account()

    def get_freshest_available_account(self, exclude_index: Optional[int] = None) -> Optional[Account]:
        """
        获取凭据刷新时间最近的可用账号

        用于当某个账号凭据失效时，快速切换到最可能有效的账号

        Args:
            exclude_index: 要排除的账号索引（通常是刚失效的那个）

        Returns:
            凭据最新的可用账号，如果没有可用账号返回 None
        """
        try:
            from app.services.credential_service import credential_service
            skip_invalid = True
        except ImportError:
            skip_invalid = False
            credential_service = None

        available = self.get_available_accounts(skip_invalid=skip_invalid)

        # 排除指定账号
        if exclude_index is not None:
            available = [acc for acc in available if acc.index != exclude_index]

        if not available:
            return None

        # 按 refresh_time 降序排序（最新的在前）
        def get_refresh_time(acc: Account) -> datetime:
            dt = acc.get_refresh_datetime()
            if dt is None:
                # 没有刷新时间的账号排在最后
                return datetime.min
            return dt

        available.sort(key=get_refresh_time, reverse=True)

        # 返回凭据最新的账号
        freshest = available[0]
        logger.info(
            f"选择凭据最新的账号: index={freshest.index}, note={freshest.note}, "
            f"refresh_time={freshest.refresh_time or '未知'}"
        )
        return freshest

    def mark_account_cooldown(
        self,
        index: int,
        reason: CooldownReason,
        custom_seconds: Optional[int] = None
    ):
        """
        标记账号进入冷却期

        Args:
            index: 账号索引
            reason: 冷却原因
            custom_seconds: 自定义冷却时间
        """
        account = self.get_account(index)
        if not account:
            return

        config = config_manager.config.cooldown

        # 根据原因确定冷却时间
        if custom_seconds is not None:
            cooldown_seconds = custom_seconds
        elif reason == CooldownReason.AUTH_ERROR:
            cooldown_seconds = config.auth_error_seconds
            # 认证错误，标记凭证无效并触发后台刷新
            try:
                from app.services.credential_service import credential_service
                credential_service.mark_invalid(index)
                # 异步加入刷新队列
                asyncio.create_task(credential_service.queue_refresh(index))
                logger.info(f"账号 {index} 认证失败，已加入后台刷新队列")
            except ImportError:
                pass
        elif reason == CooldownReason.RATE_LIMIT:
            # 限额错误：等待到太平洋时间午夜
            pt_wait = seconds_until_pt_midnight()
            cooldown_seconds = max(config.rate_limit_seconds, pt_wait)
        else:
            cooldown_seconds = config.generic_error_seconds

        # 更新状态
        account.state.cooldown_until = time.time() + cooldown_seconds
        account.state.cooldown_reason = reason
        account.state.jwt = None
        account.state.jwt_expires_at = 0
        account.state.session_name = None
        account.state.failed_requests += 1

        logger.warning(
            f"账号 {index} 进入冷却期 {cooldown_seconds}秒，原因: {reason.value}"
        )

    def clear_account_cooldown(self, index: int):
        """清除账号冷却状态"""
        account = self.get_account(index)
        if account:
            account.state.cooldown_until = None
            account.state.cooldown_reason = None
            logger.info(f"账号 {index} 冷却已清除")

    def update_account_state(
        self,
        index: int,
        jwt: Optional[str] = None,
        jwt_expires_at: Optional[float] = None,
        session_name: Optional[str] = None
    ):
        """更新账号状态"""
        account = self.get_account(index)
        if not account:
            return

        if jwt is not None:
            account.state.jwt = jwt
        if jwt_expires_at is not None:
            account.state.jwt_expires_at = jwt_expires_at
        if session_name is not None:
            account.state.session_name = session_name

        account.state.last_used_at = time.time()
        account.state.total_requests += 1

    def _get_next_cooldown_info(self) -> Optional[dict]:
        """获取最近将解除冷却的账号信息"""
        now = time.time()
        candidates = []

        for acc in self._accounts:
            if acc.available and acc.state.cooldown_until:
                if acc.state.cooldown_until > now:
                    candidates.append({
                        "index": acc.index,
                        "until": acc.state.cooldown_until,
                        "remaining": int(acc.state.cooldown_until - now)
                    })

        if not candidates:
            return None

        return min(candidates, key=lambda x: x["until"])

    async def verify_and_refresh_credential(self, account_index: int) -> Tuple[bool, str]:
        """
        验证账号凭证，如果无效则尝试刷新

        Args:
            account_index: 账号索引

        Returns:
            (is_valid, error_message)
        """
        async with self._credential_check_lock:
            # 检查是否最近已经验证过
            now = time.time()
            last_check = self._last_credential_check.get(account_index, 0)
            if now - last_check < self._credential_check_interval:
                return True, ""  # 假设最近检查过的凭证仍然有效

            self._last_credential_check[account_index] = now

        try:
            from app.services.credential_service import credential_service
            return await credential_service.check_and_refresh(account_index)
        except Exception as e:
            logger.error(f"验证账号 {account_index} 凭证时出错: {e}")
            return False, str(e)

    async def ensure_credential_valid(self, account: Account) -> bool:
        """
        确保账号凭证有效

        在获取 JWT 之前调用此方法，确保凭证可用

        Args:
            account: 账号对象

        Returns:
            凭证是否有效
        """
        is_valid, error = await self.verify_and_refresh_credential(account.index)

        if not is_valid:
            logger.warning(f"账号 {account.index} ({account.note}) 凭证无效: {error}")
            # 将账号设置为冷却状态
            self.mark_account_cooldown(account.index, CooldownReason.AUTH_ERROR)
            return False

        return True

    def invalidate_credential_cache(self, account_index: int):
        """
        使凭证检查缓存失效

        当检测到认证错误时调用，强制下次使用时重新验证凭证
        """
        self._last_credential_check.pop(account_index, None)

    def get_status(self) -> dict:
        """获取账号管理器状态"""
        total, available = self.get_account_count()
        return {
            "total_accounts": total,
            "available_accounts": available,
            "accounts": [acc.to_display_dict() for acc in self._accounts]
        }

    def get_health_summary(self) -> dict:
        """
        获取账号池健康摘要

        Returns:
            包含整体健康状况的字典
        """
        available = self.get_available_accounts(skip_invalid=True)
        if not available:
            return {
                "healthy": False,
                "available_count": 0,
                "avg_score": 0,
                "avg_success_rate": 0,
                "total_concurrent": 0,
                "accounts": []
            }

        scored = self.get_accounts_with_scores(available)
        scores = [s for _, s in scored]
        success_rates = [acc.state.get_success_rate() for acc in available]
        total_concurrent = sum(acc.state.concurrent_requests for acc in available)

        return {
            "healthy": len(available) >= 3 and sum(success_rates) / len(success_rates) > 0.8,
            "available_count": len(available),
            "avg_score": sum(scores) / len(scores),
            "min_score": min(scores),
            "max_score": max(scores),
            "avg_success_rate": sum(success_rates) / len(success_rates),
            "total_concurrent": total_concurrent,
            "accounts": [
                {
                    "index": acc.index,
                    "note": acc.note,
                    "score": score,
                    "success_rate": acc.state.get_success_rate(),
                    "concurrent": acc.state.concurrent_requests,
                    "consecutive_errors": acc.state.consecutive_errors
                }
                for acc, score in scored
            ]
        }

    def decay_statistics(self, decay_factor: float = 0.9):
        """
        衰减统计数据

        用于降低历史数据的影响，让系统更快适应账号状态变化

        Args:
            decay_factor: 衰减因子 (0-1)，越小衰减越快
        """
        for account in self._accounts:
            state = account.state
            # 衰减请求计数（保留整数）
            if state.total_requests > 0:
                state.total_requests = max(1, int(state.total_requests * decay_factor))
                state.failed_requests = int(state.failed_requests * decay_factor)
            # 衰减响应时间统计
            if state.response_count > 0:
                state.total_response_time *= decay_factor
                state.response_count = max(1, int(state.response_count * decay_factor))
            # 衰减连续成功计数
            state.consecutive_successes = int(state.consecutive_successes * decay_factor)

        logger.info(f"统计数据已衰减，衰减因子: {decay_factor}")

    def reset_account_statistics(self, account_index: int):
        """
        重置指定账号的统计数据

        Args:
            account_index: 账号索引
        """
        account = self.get_account(account_index)
        if not account:
            return

        state = account.state
        state.total_requests = 0
        state.failed_requests = 0
        state.consecutive_errors = 0
        state.consecutive_successes = 0
        state.total_response_time = 0
        state.response_count = 0
        state.last_error_at = None

        logger.info(f"账号 {account_index} ({account.note}) 统计数据已重置")

    def reset_all_statistics(self):
        """重置所有账号的统计数据"""
        for account in self._accounts:
            self.reset_account_statistics(account.index)


class NoAvailableAccountError(Exception):
    """没有可用账号异常"""
    pass


class AccountAuthError(Exception):
    """账号认证错误"""
    pass


class AccountRateLimitError(Exception):
    """账号限额错误"""
    pass


class AccountRequestError(Exception):
    """账号请求错误"""
    pass


# 全局账号管理器实例
account_manager = AccountManager()
