"""
账号替换服务

当检测到生图失败时自动：
1. 删除失败账号（从 credient.txt 和 config.json）
2. 生成新随机邮箱
3. 注册新账号并添加凭证

统一使用 credential_service 的并发注册功能（VerificationCodeHub）
"""
import asyncio
import logging
import random
import string
from pathlib import Path
from typing import Optional, Tuple
from datetime import datetime

from app.config import config_manager

logger = logging.getLogger(__name__)

# 邮箱域名
EMAIL_DOMAIN = "@jasonaa.top"

# 凭证文件路径
CREDIENT_FILE = Path(__file__).parent.parent.parent / "credient.txt"


def generate_random_email(length: int = 8) -> str:
    """
    生成随机邮箱地址

    Args:
        length: 前缀长度（8-12个随机字母）

    Returns:
        随机邮箱地址
    """
    # 使用小写字母生成随机前缀
    prefix = ''.join(random.choices(string.ascii_lowercase, k=length))
    return f"{prefix}{EMAIL_DOMAIN}"


def generate_unique_email(existing_emails: set, max_attempts: int = 100) -> str:
    """
    生成唯一的随机邮箱

    Args:
        existing_emails: 已存在的邮箱集合
        max_attempts: 最大尝试次数

    Returns:
        不重复的随机邮箱
    """
    for _ in range(max_attempts):
        # 随机长度 6-12
        length = random.randint(6, 12)
        email = generate_random_email(length)
        if email.lower() not in existing_emails:
            return email

    # 如果多次尝试都重复，使用时间戳确保唯一
    timestamp = int(datetime.now().timestamp())
    return f"a{timestamp}{EMAIL_DOMAIN}"


class AccountReplacementService:
    """
    账号替换服务

    功能：
    1. 删除失败账号
    2. 生成新随机邮箱
    3. 使用 credential_service 的并发注册功能注册新账号
    """

    def __init__(self):
        self._lock = asyncio.Lock()

    def _load_emails_from_credient(self) -> list:
        """加载 credient.txt 中的所有邮箱"""
        if not CREDIENT_FILE.exists():
            return []

        try:
            with open(CREDIENT_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
            return [
                line.strip() for line in lines
                if line.strip() and not line.startswith("#") and "@" in line
            ]
        except Exception as e:
            logger.error(f"读取 credient.txt 失败: {e}")
            return []

    def _save_emails_to_credient(self, emails: list):
        """保存邮箱列表到 credient.txt"""
        try:
            with open(CREDIENT_FILE, "w", encoding="utf-8") as f:
                for email in emails:
                    f.write(f"{email}\n")
            logger.info(f"已保存 {len(emails)} 个邮箱到 credient.txt")
        except Exception as e:
            logger.error(f"保存 credient.txt 失败: {e}")

    def _find_account_index_by_email(self, email: str) -> Optional[int]:
        """通过邮箱查找账号索引"""
        email_prefix = email.split("@")[0].lower()

        for i, acc in enumerate(config_manager.config.accounts):
            note_lower = acc.note.lower() if acc.note else ""
            # 精确匹配邮箱前缀
            if email_prefix == note_lower:
                return i

        return None

    def _find_account_index_by_team_id(self, team_id: str) -> Optional[int]:
        """通过 team_id 查找账号索引"""
        for i, acc in enumerate(config_manager.config.accounts):
            if acc.team_id == team_id:
                return i
        return None

    async def delete_account(self, account_index: int) -> Tuple[bool, str]:
        """
        删除账号（从 config.json 和 credient.txt）

        Args:
            account_index: 账号索引

        Returns:
            (success, message)
        """
        async with self._lock:
            if account_index < 0 or account_index >= len(config_manager.config.accounts):
                return False, f"账号索引 {account_index} 不存在"

            account = config_manager.config.accounts[account_index]
            account_note = account.note or ""

            logger.info(f"开始删除账号: index={account_index}, note={account_note}")

            # 1. 从 credient.txt 删除对应邮箱
            emails = self._load_emails_from_credient()

            # 查找匹配的邮箱
            email_to_delete = None
            for email in emails:
                email_prefix = email.split("@")[0].lower()
                if email_prefix == account_note.lower():
                    email_to_delete = email
                    break

            if email_to_delete:
                emails.remove(email_to_delete)
                self._save_emails_to_credient(emails)
                logger.info(f"已从 credient.txt 删除邮箱: {email_to_delete}")
            else:
                logger.warning(f"未在 credient.txt 中找到账号 {account_note} 对应的邮箱")

            # 2. 从 config.json 删除账号
            del config_manager.config.accounts[account_index]
            config_manager.save()
            logger.info(f"已从 config.json 删除账号: {account_note}")

            # 3. 重新加载账号管理器
            try:
                from app.services.account_manager import account_manager
                account_manager.load_accounts()
            except Exception as e:
                logger.warning(f"重新加载账号管理器失败: {e}")

            return True, f"已删除账号: {account_note}"

    async def delete_account_by_team_id(self, team_id: str) -> Tuple[bool, str]:
        """通过 team_id 删除账号"""
        account_index = self._find_account_index_by_team_id(team_id)
        if account_index is None:
            return False, f"未找到 team_id={team_id[:20]}... 的账号"
        return await self.delete_account(account_index)

    async def add_new_random_account(self) -> Tuple[bool, str, Optional[str]]:
        """
        添加一个新的随机账号

        使用 credential_service 的并发注册功能（统一使用 VerificationCodeHub）
        支持多个替换任务并发执行

        Returns:
            (success, message, new_email)
        """
        from app.services.credential_service import credential_service

        # 只锁住文件操作，生成邮箱和写入文件
        async with self._lock:
            # 1. 获取现有邮箱列表
            existing_emails = set(email.lower() for email in self._load_emails_from_credient())

            # 2. 生成新的唯一邮箱
            new_email = generate_unique_email(existing_emails)
            logger.info(f"生成新邮箱: {new_email}")

            # 3. 添加到 credient.txt
            emails = self._load_emails_from_credient()
            emails.append(new_email)
            self._save_emails_to_credient(emails)

        # 注册过程放在锁外面，允许并发
        print(f"\n[账号替换] 开始注册新账号: {new_email}")
        logger.info(f"开始注册新账号: {new_email}")

        try:
            # 确保共享资源已初始化
            if not await credential_service._ensure_shared_resources():
                # 注册失败，从 credient.txt 移除
                async with self._lock:
                    emails = self._load_emails_from_credient()
                    if new_email in emails:
                        emails.remove(new_email)
                        self._save_emails_to_credient(emails)
                return False, "无法初始化共享资源", None

            # 调用并发注册方法（可以多个同时执行）
            success, error = await credential_service._do_concurrent_register(new_email)

            if success:
                print(f"[账号替换] 新账号注册成功: {new_email}")
                logger.info(f"新账号注册成功: {new_email}")
                return True, f"新账号注册成功: {new_email}", new_email
            else:
                # 注册失败，从 credient.txt 移除
                async with self._lock:
                    emails = self._load_emails_from_credient()
                    if new_email in emails:
                        emails.remove(new_email)
                        self._save_emails_to_credient(emails)
                print(f"[账号替换] 新账号注册失败: {error}")
                logger.warning(f"新账号注册失败: {error}")
                return False, f"新账号注册失败: {error}", None

        except Exception as e:
            # 注册出错，从 credient.txt 移除
            async with self._lock:
                emails = self._load_emails_from_credient()
                if new_email in emails:
                    emails.remove(new_email)
                    self._save_emails_to_credient(emails)
            print(f"[账号替换] 注册新账号出错: {e}")
            logger.error(f"注册新账号出错: {e}")
            import traceback
            traceback.print_exc()
            return False, f"注册新账号出错: {e}", None

    async def replace_failed_account(
        self,
        failed_account_index: int = None,
        failed_team_id: str = None
    ) -> Tuple[bool, str]:
        """
        替换失败的账号

        删除失败账号，生成并注册新账号

        Args:
            failed_account_index: 失败账号的索引
            failed_team_id: 失败账号的 team_id（二选一）

        Returns:
            (success, message)
        """
        # 确定要删除的账号
        if failed_account_index is not None:
            account_index = failed_account_index
        elif failed_team_id:
            account_index = self._find_account_index_by_team_id(failed_team_id)
            if account_index is None:
                return False, f"未找到 team_id 对应的账号"
        else:
            return False, "必须指定 failed_account_index 或 failed_team_id"

        # 获取账号信息用于日志
        if account_index < len(config_manager.config.accounts):
            account = config_manager.config.accounts[account_index]
            account_note = account.note or f"账号{account_index}"
        else:
            account_note = f"账号{account_index}"

        print(f"\n{'='*60}")
        print(f"[账号替换] 开始替换失败账号: {account_note}")
        print(f"{'='*60}")

        # 1. 删除失败账号
        delete_success, delete_msg = await self.delete_account(account_index)
        if not delete_success:
            print(f"[账号替换] 删除失败: {delete_msg}")
            return False, f"删除账号失败: {delete_msg}"

        print(f"[账号替换] 已删除失败账号: {account_note}")

        # 2. 添加新随机账号
        add_success, add_msg, new_email = await self.add_new_random_account()

        if add_success:
            print(f"[账号替换] 替换完成: {account_note} -> {new_email}")
            print(f"{'='*60}\n")
            return True, f"账号替换成功: {account_note} -> {new_email}"
        else:
            print(f"[账号替换] 添加新账号失败: {add_msg}")
            print(f"{'='*60}\n")
            return False, f"删除成功但添加新账号失败: {add_msg}"

    async def handle_image_generation_failure(
        self,
        account: 'Account',
        error_message: str = ""
    ) -> Tuple[bool, str]:
        """
        处理图片生成失败

        当检测到图片生成失败时调用此方法，自动替换账号

        Args:
            account: 失败的账号对象
            error_message: 错误信息

        Returns:
            (success, message)
        """
        logger.warning(f"检测到图片生成失败: account={account.index} ({account.note}), error={error_message}")
        print(f"\n[图片生成失败] 账号 {account.note}: {error_message}")
        print(f"[图片生成失败] 开始自动替换账号...")

        return await self.replace_failed_account(
            failed_account_index=account.index
        )

    async def shutdown(self):
        """关闭服务（清理资源）"""
        pass


# 全局账号替换服务实例
account_replacement_service = AccountReplacementService()
