"""
账号池维护服务

持久运行，保持账号池始终有目标数量的活跃账号
- 定期健康检查
- 刷新失败直接删除
- 自动补充新账号
"""
import asyncio
import sys
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Set

# 添加 backend 目录到路径，以便导入统一配置
BACKEND_DIR = Path(__file__).parent.parent.parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# 导入统一配置
try:
    import config as unified_config
except ImportError:
    unified_config = None

from app.config import config_manager
from app.services.account_manager import account_manager
from app.services.credential_service import credential_service
from app.services.account_replacement_service import account_replacement_service, generate_unique_email

logger = logging.getLogger(__name__)


class AccountPoolService:
    """
    账号池维护服务

    目标：保持账号池始终有 TARGET_ACCOUNT_COUNT 个可用账号
    策略：激进删除 + 快速补充
    """

    # 从统一配置读取，如果没有则使用默认值
    @property
    def TARGET_ACCOUNT_COUNT(self):
        if unified_config and hasattr(unified_config, 'ACCOUNT_POOL_TARGET_COUNT'):
            return unified_config.ACCOUNT_POOL_TARGET_COUNT
        return 25

    @property
    def HEALTH_CHECK_INTERVAL(self):
        if unified_config and hasattr(unified_config, 'ACCOUNT_POOL_HEALTH_CHECK_INTERVAL'):
            return unified_config.ACCOUNT_POOL_HEALTH_CHECK_INTERVAL
        return 300

    @property
    def MAX_REFRESH_FAILURES(self):
        if unified_config and hasattr(unified_config, 'ACCOUNT_POOL_MAX_REFRESH_FAILURES'):
            return unified_config.ACCOUNT_POOL_MAX_REFRESH_FAILURES
        return 2

    @property
    def MAX_CONSECUTIVE_ERRORS(self):
        if unified_config and hasattr(unified_config, 'ACCOUNT_POOL_MAX_CONSECUTIVE_ERRORS'):
            return unified_config.ACCOUNT_POOL_MAX_CONSECUTIVE_ERRORS
        return 3

    @property
    def CREDENTIAL_EXPIRE_HOURS(self):
        if unified_config and hasattr(unified_config, 'ACCOUNT_POOL_CREDENTIAL_EXPIRE_HOURS'):
            return unified_config.ACCOUNT_POOL_CREDENTIAL_EXPIRE_HOURS
        return 12

    def __init__(self):
        self._running = False
        self._task = None

        # 记录刷新失败次数
        self._refresh_failures: dict = {}  # account_note -> failure_count

        # 记录连续错误次数
        self._consecutive_errors: dict = {}  # account_note -> error_count

        # 正在补充账号的锁
        self._replenish_lock = asyncio.Lock()

    async def start(self):
        """启动服务"""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._main_loop())
        print(f"\n[AccountPool] 服务已启动，目标账号数: {self.TARGET_ACCOUNT_COUNT}")
        logger.info(f"AccountPool service started, target: {self.TARGET_ACCOUNT_COUNT}")

    async def stop(self):
        """停止服务"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        print("[AccountPool] 服务已停止")
        logger.info("AccountPool service stopped")

    async def _main_loop(self):
        """主循环"""
        # 启动后等待一段时间，让其他服务先初始化
        await asyncio.sleep(30)

        while self._running:
            try:
                print(f"\n[AccountPool] === 开始健康检查 ({datetime.now().strftime('%H:%M:%S')}) ===")

                # 1. 执行健康检查，清理无效账号
                await self._health_check()

                # 2. 检查并补充账号
                await self._replenish_accounts()

                # 3. 打印状态
                self._print_status()

            except Exception as e:
                logger.error(f"AccountPool main loop error: {e}")
                import traceback
                traceback.print_exc()

            # 等待下次检查
            await asyncio.sleep(self.HEALTH_CHECK_INTERVAL)

    async def _health_check(self):
        """
        健康检查

        检查所有账号的健康状态，删除不健康的账号
        """
        accounts = list(config_manager.config.accounts)
        deleted_count = 0

        for i, account in enumerate(accounts):
            # 跳过已被标记为不可用的
            if not account.available:
                continue

            account_note = account.note or f"账号{i}"
            should_delete = False
            delete_reason = ""

            # 检查1: 凭证是否过期
            if account.refresh_time:
                try:
                    # 处理时区：统一转换为本地时间比较
                    refresh_time_str = account.refresh_time.replace('Z', '+00:00')
                    refresh_dt = datetime.fromisoformat(refresh_time_str)
                    # 如果有时区信息，转换为本地时间
                    if refresh_dt.tzinfo is not None:
                        refresh_dt = refresh_dt.replace(tzinfo=None)
                    age_hours = (datetime.now() - refresh_dt).total_seconds() / 3600

                    if age_hours > self.CREDENTIAL_EXPIRE_HOURS:
                        # 尝试刷新
                        print(f"[AccountPool] {account_note} 凭证已过期 {age_hours:.1f}h，尝试刷新...")
                        success = await self._try_refresh(i, account_note)

                        if not success:
                            should_delete = True
                            delete_reason = f"凭证过期且刷新失败"
                except Exception as e:
                    logger.warning(f"解析 refresh_time 失败: {e}")

            # 检查2: 刷新失败次数
            failures = self._refresh_failures.get(account_note, 0)
            if failures >= self.MAX_REFRESH_FAILURES:
                should_delete = True
                delete_reason = f"刷新失败次数达到 {failures} 次"

            # 检查3: 连续错误次数
            errors = self._consecutive_errors.get(account_note, 0)
            if errors >= self.MAX_CONSECUTIVE_ERRORS:
                should_delete = True
                delete_reason = f"连续错误次数达到 {errors} 次"

            # 检查4: 必要的凭证字段是否存在
            if not account.secure_c_ses or not account.team_id:
                should_delete = True
                delete_reason = "缺少必要凭证"

            # 执行删除
            if should_delete:
                print(f"[AccountPool] 删除账号 {account_note}: {delete_reason}")
                logger.warning(f"Deleting account {account_note}: {delete_reason}")

                try:
                    # 查找当前索引（因为删除会改变索引）
                    current_index = self._find_account_index(account_note)
                    if current_index is not None:
                        await account_replacement_service.delete_account(current_index)
                        deleted_count += 1

                        # 清理记录
                        self._refresh_failures.pop(account_note, None)
                        self._consecutive_errors.pop(account_note, None)
                except Exception as e:
                    logger.error(f"删除账号 {account_note} 失败: {e}")

        if deleted_count > 0:
            print(f"[AccountPool] 健康检查完成，删除了 {deleted_count} 个账号")

    async def _try_refresh(self, account_index: int, account_note: str) -> bool:
        """
        尝试刷新账号凭证

        Returns:
            True 如果刷新成功
        """
        try:
            success, error = await credential_service.refresh_credential(account_index)

            if success:
                print(f"[AccountPool] {account_note} 刷新成功")
                self._refresh_failures[account_note] = 0
                return True
            else:
                print(f"[AccountPool] {account_note} 刷新失败: {error}")
                self._refresh_failures[account_note] = self._refresh_failures.get(account_note, 0) + 1
                return False

        except Exception as e:
            logger.error(f"刷新账号 {account_note} 出错: {e}")
            self._refresh_failures[account_note] = self._refresh_failures.get(account_note, 0) + 1
            return False

    async def _replenish_accounts(self):
        """
        补充账号

        当可用账号数量低于目标时，自动注册新账号
        """
        async with self._replenish_lock:
            current_count = self._get_available_count()
            needed = self.TARGET_ACCOUNT_COUNT - current_count

            if needed <= 0:
                return

            print(f"[AccountPool] 当前可用账号: {current_count}，需要补充: {needed}")
            logger.info(f"Need to add {needed} accounts (current: {current_count})")

            # 并发注册（2G内存服务器最多同时注册2个）
            batch_size = min(needed, 2)

            for i in range(0, needed, batch_size):
                tasks = []
                for j in range(min(batch_size, needed - i)):
                    tasks.append(self._add_one_account())

                results = await asyncio.gather(*tasks, return_exceptions=True)

                success_count = sum(1 for r in results if r is True)
                print(f"[AccountPool] 批次 {i//batch_size + 1}: 成功注册 {success_count}/{len(tasks)} 个账号")

                # 每批次之间稍作等待
                if i + batch_size < needed:
                    await asyncio.sleep(5)

    async def _add_one_account(self) -> bool:
        """添加一个新账号"""
        try:
            success, msg, email = await account_replacement_service.add_new_random_account()
            if success:
                print(f"[AccountPool] 新账号注册成功: {email}")
                return True
            else:
                print(f"[AccountPool] 新账号注册失败: {msg}")
                return False
        except Exception as e:
            logger.error(f"添加新账号出错: {e}")
            return False

    def _find_account_index(self, account_note: str) -> int:
        """通过 note 查找账号索引"""
        for i, acc in enumerate(config_manager.config.accounts):
            if acc.note and acc.note.lower() == account_note.lower():
                return i
        return None

    def _get_available_count(self) -> int:
        """获取可用账号数量"""
        return sum(1 for acc in config_manager.config.accounts if acc.available)

    def _print_status(self):
        """打印状态"""
        total = len(config_manager.config.accounts)
        available = self._get_available_count()

        print(f"\n[AccountPool] 状态: {available}/{total} 可用，目标: {self.TARGET_ACCOUNT_COUNT}")

        if self._refresh_failures:
            print(f"[AccountPool] 刷新失败记录: {dict(self._refresh_failures)}")
        if self._consecutive_errors:
            print(f"[AccountPool] 连续错误记录: {dict(self._consecutive_errors)}")

    def record_error(self, account_note: str):
        """
        记录账号错误（供外部调用）

        当账号在使用中出现错误时调用此方法
        """
        self._consecutive_errors[account_note] = self._consecutive_errors.get(account_note, 0) + 1
        count = self._consecutive_errors[account_note]
        logger.warning(f"Account {account_note} error recorded, count: {count}")

        if count >= self.MAX_CONSECUTIVE_ERRORS:
            print(f"[AccountPool] {account_note} 连续错误达到 {count} 次，将在下次检查时删除")

    def clear_error(self, account_note: str):
        """
        清除账号错误记录（供外部调用）

        当账号成功使用后调用此方法
        """
        if account_note in self._consecutive_errors:
            del self._consecutive_errors[account_note]

    def record_refresh_failure(self, account_note: str):
        """记录刷新失败"""
        self._refresh_failures[account_note] = self._refresh_failures.get(account_note, 0) + 1


# 全局实例
account_pool_service = AccountPoolService()
