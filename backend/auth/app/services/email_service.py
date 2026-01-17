"""
邮件发送服务
使用 SMTP 发送邮件（支持 QQ 邮箱等）
"""
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

from ..config import (
    SMTP_HOST,
    SMTP_PORT,
    SMTP_USER,
    SMTP_PASSWORD,
    SMTP_FROM_NAME,
    EMAIL_VERIFICATION_ENABLED
)


class EmailService:
    """邮件发送服务"""

    @staticmethod
    def is_configured() -> bool:
        """检查邮件服务是否已配置"""
        return bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD)

    @staticmethod
    def is_enabled() -> bool:
        """检查邮件验证是否启用"""
        return EMAIL_VERIFICATION_ENABLED and EmailService.is_configured()

    @staticmethod
    def send_email(
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None
    ) -> bool:
        """
        发送邮件

        Args:
            to_email: 收件人邮箱
            subject: 邮件主题
            html_content: HTML 格式内容
            text_content: 纯文本内容（可选）

        Returns:
            是否发送成功
        """
        if not EmailService.is_configured():
            print("邮件服务未配置")
            return False

        print(f"准备发送邮件到: {to_email}")
        print(f"SMTP 服务器: {SMTP_HOST}:{SMTP_PORT}")

        try:
            # 创建邮件
            message = MIMEMultipart("alternative")
            message["Subject"] = subject
            message["From"] = f"{SMTP_FROM_NAME} <{SMTP_USER}>"
            message["To"] = to_email

            # 添加纯文本版本
            if text_content:
                part1 = MIMEText(text_content, "plain", "utf-8")
                message.attach(part1)

            # 添加 HTML 版本
            part2 = MIMEText(html_content, "html", "utf-8")
            message.attach(part2)

            # 根据端口选择连接方式
            if SMTP_PORT == 465:
                # SSL 方式
                print("使用 SSL 连接...")
                context = ssl.create_default_context()
                server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=30)
                try:
                    print("正在登录...")
                    server.login(SMTP_USER, SMTP_PASSWORD)
                    print("正在发送...")
                    server.sendmail(SMTP_USER, to_email, message.as_string())
                    print(f"邮件发送成功: {to_email}")
                finally:
                    try:
                        server.quit()
                    except Exception:
                        pass  # 忽略关闭连接时的错误
            else:
                # STARTTLS 方式（端口 587）
                print("使用 STARTTLS 连接...")
                server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30)
                try:
                    server.starttls()
                    print("正在登录...")
                    server.login(SMTP_USER, SMTP_PASSWORD)
                    print("正在发送...")
                    server.sendmail(SMTP_USER, to_email, message.as_string())
                    print(f"邮件发送成功: {to_email}")
                finally:
                    try:
                        server.quit()
                    except Exception:
                        pass  # 忽略关闭连接时的错误

            return True

        except smtplib.SMTPAuthenticationError as e:
            print(f"SMTP 认证失败: {e}")
            print("请检查邮箱账号和授权码是否正确")
            return False
        except smtplib.SMTPConnectError as e:
            print(f"SMTP 连接失败: {e}")
            return False
        except smtplib.SMTPException as e:
            print(f"SMTP 错误: {e}")
            return False
        except ssl.SSLError as e:
            print(f"SSL 错误: {e}")
            return False
        except Exception as e:
            print(f"邮件发送失败: {type(e).__name__}: {e}")
            return False

    @staticmethod
    def send_verification_code(to_email: str, code: str, expire_minutes: int = 10) -> bool:
        """
        发送验证码邮件

        Args:
            to_email: 收件人邮箱
            code: 验证码
            expire_minutes: 验证码有效期（分钟）

        Returns:
            是否发送成功
        """
        subject = "邮箱验证码"

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    line-height: 1.6;
                    color: #333;
                }}
                .container {{
                    max-width: 600px;
                    margin: 0 auto;
                    padding: 20px;
                }}
                .code {{
                    font-size: 32px;
                    font-weight: bold;
                    color: #007bff;
                    letter-spacing: 8px;
                    text-align: center;
                    padding: 20px;
                    background-color: #f8f9fa;
                    border-radius: 8px;
                    margin: 20px 0;
                }}
                .warning {{
                    color: #666;
                    font-size: 14px;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <h2>邮箱验证码</h2>
                <p>您好，您正在进行邮箱验证操作，验证码如下：</p>
                <div class="code">{code}</div>
                <p class="warning">
                    验证码有效期为 {expire_minutes} 分钟，请勿将验证码告知他人。<br>
                    如非本人操作，请忽略此邮件。
                </p>
            </div>
        </body>
        </html>
        """

        text_content = f"""
邮箱验证码

您好，您正在进行邮箱验证操作，验证码如下：

{code}

验证码有效期为 {expire_minutes} 分钟，请勿将验证码告知他人。
如非本人操作，请忽略此邮件。
        """

        return EmailService.send_email(to_email, subject, html_content, text_content)


# 创建单例实例
email_service = EmailService()
