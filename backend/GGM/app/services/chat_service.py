"""
聊天服务
- 会话创建
- 消息发送
- 流式响应
"""
import asyncio
import json
import ssl
import uuid
import hashlib
import logging
from typing import Optional, List, AsyncGenerator

import httpx
import anyio

from app.config import config_manager
from app.models.account import Account
from app.models.chat import ChatResult, ChatImage, estimate_tokens
from app.models.conversation import Conversation
from app.services.account_manager import (
    account_manager,
    AccountAuthError,
    AccountRateLimitError,
    AccountRequestError,
    CooldownReason
)

# 图片生成失败错误
class ImageGenerationError(Exception):
    """图片生成失败错误"""
    pass
from app.services.conversation_manager import conversation_manager
from app.services.jwt_service import jwt_service, get_http_client, reset_http_client
from app.services.image_service import image_service

logger = logging.getLogger(__name__)

# API endpoints
BASE_URL = "https://biz-discoveryengine.googleapis.com/v1alpha/locations/global"
CREATE_SESSION_URL = f"{BASE_URL}/widgetCreateSession"
STREAM_ASSIST_URL = f"{BASE_URL}/widgetStreamAssist"


def get_headers(jwt: str) -> dict:
    """获取请求头"""
    return {
        "accept": "*/*",
        "accept-encoding": "gzip, deflate, br",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
        "authorization": f"Bearer {jwt}",
        "content-type": "application/json",
        "origin": "https://business.gemini.google",
        "referer": "https://business.gemini.google/",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "x-server-timeout": "1800",
    }


class ChatService:
    """
    聊天服务

    功能：
    1. 创建 Gemini Session
    2. 发送消息
    3. 解析响应（包括图片）
    """

    def __init__(self):
        self._lock = asyncio.Lock()

    def _build_full_message(
        self,
        message: str,
        system_prompt: str = "",
        history_messages: List[dict] = None
    ) -> str:
        """
        构建完整的消息，包含系统提示词和历史消息

        对于新会话或需要上下文的场景，将系统提示词和历史消息
        作为前缀添加到用户消息中。

        Args:
            message: 当前用户消息
            system_prompt: 系统提示词
            history_messages: 历史消息列表

        Returns:
            完整的消息文本
        """
        if history_messages is None:
            history_messages = []

        parts = []

        # 添加系统提示词
        if system_prompt:
            parts.append(f"[System Instructions]\n{system_prompt}\n[End of System Instructions]\n")

        # 添加历史消息
        if history_messages:
            parts.append("[Conversation History]")
            for msg in history_messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                role_label = "User" if role == "user" else "Assistant"
                parts.append(f"{role_label}: {content}")
            parts.append("[End of Conversation History]\n")

        # 添加当前用户消息
        if parts:
            # 如果有前缀内容，明确标记当前消息
            parts.append(f"[Current Message]\n{message}")
            return "\n".join(parts)
        else:
            # 没有前缀，直接返回消息
            return message

    async def create_gemini_session(
        self,
        account: Account,
        jwt: str
    ) -> str:
        """
        创建 Gemini Session

        Returns:
            session_name: Gemini 会话名称
        """
        client = await get_http_client()

        session_id = uuid.uuid4().hex[:12]
        body = {
            "configId": account.team_id,
            "additionalParams": {"token": "-"},
            "createSessionRequest": {
                "session": {"name": session_id, "displayName": session_id}
            }
        }

        # 发送请求（带 SSL 错误重试）
        max_retries = 2
        for retry in range(max_retries + 1):
            try:
                if retry > 0:
                    logger.warning(f"重试创建会话 ({retry}/{max_retries})...")
                    await reset_http_client()
                    client = await get_http_client()

                response = await client.post(
                    CREATE_SESSION_URL,
                    headers=get_headers(jwt),
                    json=body
                )
                break

            except ssl.SSLError as e:
                logger.warning(f"SSL 错误: {e}")
                if retry < max_retries:
                    await asyncio.sleep(1)
                    continue
                raise AccountRequestError(f"SSL 错误（已重试 {max_retries} 次）: {e}")

            except anyio.ClosedResourceError as e:
                logger.warning(f"连接被关闭: {e}")
                if retry < max_retries:
                    await asyncio.sleep(1)
                    continue
                raise AccountRequestError(f"连接被关闭（已重试 {max_retries} 次）: {e}")

            except httpx.RequestError as e:
                error_str = str(e).lower()
                if "ssl" in error_str or "decryption" in error_str or "bad record" in error_str or "closed" in error_str:
                    logger.warning(f"SSL/连接相关错误: {e}")
                    if retry < max_retries:
                        await asyncio.sleep(1)
                        continue
                raise AccountRequestError(f"创建会话请求失败: {e}")

        if response.status_code == 401:
            # 认证失败，使凭证缓存失效
            account_manager.invalidate_credential_cache(account.index)
            raise AccountAuthError("创建会话认证失败")
        elif response.status_code == 403:
            # 权限被拒绝，通常是凭证过期
            logger.warning(f"创建会话权限被拒绝 (403)，凭证可能已过期")
            account_manager.invalidate_credential_cache(account.index)
            raise AccountAuthError("创建会话权限被拒绝，凭证已过期")
        elif response.status_code == 429:
            raise AccountRateLimitError("创建会话触发限额")
        elif response.status_code != 200:
            logger.error(f"创建会话失败: status={response.status_code}, body={response.text[:500]}")
            raise AccountRequestError(f"创建会话失败: {response.status_code}")

        data = response.json()
        session_name = data.get("session", {}).get("name")

        if not session_name:
            logger.error(f"响应中没有 session name: {data}")
            raise AccountRequestError(f"响应中没有 session name")

        logger.info(f"创建 Gemini Session 成功: {session_name}")
        return session_name

    async def ensure_gemini_session(
        self,
        conversation: Conversation,
        account: Account,
        jwt: str
    ) -> str:
        """
        确保会话有有效的 Gemini Session

        如果没有，创建一个并更新绑定
        """
        if conversation.session_name:
            return conversation.session_name

        # 创建新的 Gemini Session
        session_name = await self.create_gemini_session(account, jwt)

        # 更新绑定
        await conversation_manager.update_binding_session(conversation.id, session_name)

        # 同时更新账号状态
        account_manager.update_account_state(
            account.index,
            session_name=session_name
        )

        return session_name

    async def chat(
        self,
        conversation: Conversation,
        message: str,
        file_ids: List[str] = None,
        model: str = None,
        system_prompt: str = "",
        history_messages: List[dict] = None
    ) -> ChatResult:
        """
        发送聊天消息

        Args:
            conversation: 会话
            message: 用户消息
            file_ids: 文件ID列表（图片等）
            model: 模型名称（用于确定生成模式）
            system_prompt: 系统提示词
            history_messages: 历史消息列表 [{"role": "user/assistant", "content": "..."}]

        Returns:
            ChatResult: 聊天结果
        """
        import time as time_module

        if history_messages is None:
            history_messages = []

        if not conversation.team_id:
            raise ValueError("会话没有绑定信息")

        # 获取绑定的账号
        account = await account_manager.get_account_for_conversation(
            conversation.account_index
        )

        # 记录请求开始
        request_start_time = time_module.time()
        account.state.record_request_start()
        request_success = False

        try:
            # 确保 JWT 有效
            jwt = await jwt_service.ensure_jwt(account)

            # 确保 Gemini Session 有效
            session_name = await self.ensure_gemini_session(conversation, account, jwt)

            # 发送消息（如果失败会尝试重建session）
            try:
                result = await self._send_message(
                    jwt=jwt,
                    session_name=session_name,
                    team_id=account.team_id,
                    message=message,
                    file_ids=file_ids or [],
                    conversation=conversation,
                    model=model,
                    system_prompt=system_prompt,
                    history_messages=history_messages
                )

                # 检测图片生成失败，触发账号替换
                if result.image_generation_failed:
                    logger.warning(f"检测到图片生成失败，触发账号替换: {account.note}")
                    asyncio.create_task(self._handle_image_generation_failure(account))
                else:
                    # 成功时清除错误记录
                    try:
                        from app.services.account_pool_service import account_pool_service
                        account_pool_service.clear_error(account.note)
                    except:
                        pass

                request_success = True
                return result
            except AccountRequestError as e:
                error_msg = str(e)
                logger.info(f"捕获到 AccountRequestError: {error_msg}")

                # 检查是否是 FILE_NOT_FOUND 错误
                if "FILE_NOT_FOUND" in error_msg:
                    # 文件不存在，清空文件列表重试
                    logger.warning(f"检测到文件不存在错误，清空文件列表重试")
                    result = await self._send_message(
                        jwt=jwt,
                        session_name=session_name,
                        team_id=account.team_id,
                        message=message,
                        file_ids=[],  # 不带文件ID重试
                        conversation=conversation,
                        model=model,
                        system_prompt=system_prompt,
                        history_messages=history_messages
                    )
                    request_success = True
                    return result
                # 如果是403或404错误，可能是session过期或不属于当前用户，尝试重建
                elif "403" in error_msg or "404" in error_msg:
                    logger.warning(f"Session无效或不属于当前用户，尝试重建: {session_name}")
                    # 清除旧session
                    await conversation_manager.update_binding_session(conversation.id, "")
                    # 重新创建session
                    session_name = await self.create_gemini_session(account, jwt)
                    await conversation_manager.update_binding_session(conversation.id, session_name)
                    account_manager.update_account_state(account.index, session_name=session_name)
                    # 重试发送消息（不带文件，因为文件可能也属于旧session）
                    result = await self._send_message(
                        jwt=jwt,
                        session_name=session_name,
                        team_id=account.team_id,
                        message=message,
                        file_ids=[],  # 新session不带旧文件
                        conversation=conversation,
                        model=model,
                        system_prompt=system_prompt,
                        history_messages=history_messages
                    )
                    request_success = True
                    return result
                raise

        except AccountAuthError as e:
            # 认证失败，标记凭证无效并触发后台刷新
            account_manager.invalidate_credential_cache(account.index)

            # 尝试切换到凭据最新的账号（不阻塞等待刷新）
            try:
                from app.services.credential_service import credential_service
                # 标记无效并异步刷新
                credential_service.mark_invalid(account.index)
                asyncio.create_task(credential_service.queue_refresh(account.index))
                logger.info(f"账号 {account.index} 凭据无效，已加入后台刷新队列")
            except ImportError:
                pass

            # 尝试切换到凭据最新的账号
            freshest_account = account_manager.get_freshest_available_account(exclude_index=account.index)
            if freshest_account:
                logger.info(
                    f"切换到凭据最新的账号: {freshest_account.index} ({freshest_account.note}), "
                    f"refresh_time={freshest_account.refresh_time or '未知'}"
                )
                try:
                    # 更新会话绑定到新账号
                    await conversation_manager.update_binding_account(conversation.id, freshest_account.index)

                    # 使用新账号重试
                    jwt = await jwt_service.ensure_jwt(freshest_account)
                    session_name = await self.ensure_gemini_session(conversation, freshest_account, jwt)
                    result = await self._send_message(
                        jwt=jwt,
                        session_name=session_name,
                        team_id=freshest_account.team_id,
                        message=message,
                        file_ids=[],  # 新账号不带旧文件
                        conversation=conversation,
                        model=model,
                        system_prompt=system_prompt,
                        history_messages=history_messages
                    )
                    return result
                except AccountAuthError:
                    # 新账号也失败，标记冷却并抛出
                    account_manager.mark_account_cooldown(
                        freshest_account.index,
                        CooldownReason.AUTH_ERROR
                    )
                    # 记录新账号的错误
                    try:
                        from app.services.account_pool_service import account_pool_service
                        account_pool_service.record_error(freshest_account.note)
                    except:
                        pass
                    raise

            # 没有可用的备选账号，标记原账号冷却并抛出
            account_manager.mark_account_cooldown(
                account.index,
                CooldownReason.AUTH_ERROR
            )
            # 记录错误到账号池服务
            try:
                from app.services.account_pool_service import account_pool_service
                account_pool_service.record_error(account.note)
            except:
                pass
            raise
        except AccountRateLimitError as e:
            account_manager.mark_account_cooldown(
                account.index,
                CooldownReason.RATE_LIMIT
            )
            raise
        except AccountRequestError as e:
            account_manager.mark_account_cooldown(
                account.index,
                CooldownReason.GENERIC_ERROR
            )
            # 记录错误到账号池服务
            try:
                from app.services.account_pool_service import account_pool_service
                account_pool_service.record_error(account.note)
            except:
                pass
            raise
        finally:
            # 记录请求结束（无论成功或失败）
            response_time_ms = (time_module.time() - request_start_time) * 1000
            account.state.record_request_end(request_success, response_time_ms)
            logger.debug(
                f"请求结束: account={account.index}, success={request_success}, "
                f"time={response_time_ms:.1f}ms, concurrent={account.state.concurrent_requests}"
            )

    async def _send_message(
        self,
        jwt: str,
        session_name: str,
        team_id: str,
        message: str,
        file_ids: List[str],
        conversation: Conversation,
        model: str = None,
        system_prompt: str = "",
        history_messages: List[dict] = None
    ) -> ChatResult:
        """发送消息并解析响应"""
        client = await get_http_client()

        if history_messages is None:
            history_messages = []

        # 构建完整的消息（包含系统提示词和历史消息）
        full_message = self._build_full_message(message, system_prompt, history_messages)

        logger.info(f"发送聊天消息: session={session_name}, team_id={team_id}, model={model}, "
                   f"message_len={len(message)}, full_message_len={len(full_message)}, "
                   f"has_system_prompt={bool(system_prompt)}, history_count={len(history_messages)}")

        # 计算输入 token 数量（使用完整消息）
        prompt_tokens = estimate_tokens(full_message)

        # 根据模型类型确定工具规格
        # 图像生成模型列表（支持更多变体）
        image_gen_models = ["nano-banana", "gemini-3-pro-image", "imagen", "image-gen", "imagegeneration"]
        is_image_model = model and any(m in model.lower() for m in image_gen_models)
        logger.info(f"[图片生成检测] model='{model}', is_image_model={is_image_model}")

        # 构建工具规格
        tools_spec = {
            "webGroundingSpec": {},
            "toolRegistry": "default_tool_registry",
        }

        # 只有特定图片模型才启用图片生成
        # 注意：Discovery Engine API 的 imageGenerationSpec 是空对象，不需要额外字段
        if is_image_model:
            tools_spec["imageGenerationSpec"] = {}
            logger.info(f"图片生成模型 {model}，启用 imageGenerationSpec")

        # 构建请求体
        stream_assist_request = {
            "session": session_name,
            "query": {"parts": [{"text": full_message}]},
            "filter": "",
            "fileIds": file_ids,
            "answerGenerationMode": "NORMAL",
            "toolsSpec": tools_spec,
            "languageCode": "zh-CN",
            "userMetadata": {"timeZone": "Etc/GMT-8"},
            "assistSkippingMode": "REQUEST_ASSIST"
        }
        # 注意：Discovery Engine API 不支持 modelSpec 参数，模型由账号配置决定

        body = {
            "configId": team_id,
            "additionalParams": {"token": "-"},
            "streamAssistRequest": stream_assist_request
        }

        # 发送请求（带 SSL 错误重试）
        max_retries = 2
        last_error = None

        for retry in range(max_retries + 1):
            try:
                if retry > 0:
                    # SSL 错误后重置客户端
                    logger.warning(f"重试请求 ({retry}/{max_retries})...")
                    await reset_http_client()
                    client = await get_http_client()

                response = await client.post(
                    STREAM_ASSIST_URL,
                    headers=get_headers(jwt),
                    json=body,
                    timeout=120.0
                )
                break  # 请求成功，退出重试循环

            except ssl.SSLError as e:
                last_error = e
                logger.warning(f"SSL 错误: {e}")
                if retry < max_retries:
                    await asyncio.sleep(1)
                    continue
                raise AccountRequestError(f"SSL 错误（已重试 {max_retries} 次）: {e}")

            except anyio.ClosedResourceError as e:
                last_error = e
                logger.warning(f"连接被关闭: {e}")
                if retry < max_retries:
                    await asyncio.sleep(1)
                    continue
                raise AccountRequestError(f"连接被关闭（已重试 {max_retries} 次）: {e}")

            except httpx.RequestError as e:
                # 检查是否是 SSL 相关错误
                error_str = str(e).lower()
                if "ssl" in error_str or "decryption" in error_str or "bad record" in error_str or "closed" in error_str:
                    last_error = e
                    logger.warning(f"SSL/连接相关错误: {e}")
                    if retry < max_retries:
                        await asyncio.sleep(1)
                        continue
                raise AccountRequestError(f"聊天请求失败: {e}")

        if response.status_code == 401:
            raise AccountAuthError("聊天认证失败")
        elif response.status_code == 429:
            raise AccountRateLimitError("聊天触发限额")
        elif response.status_code != 200:
            error_body = response.text[:500]
            logger.error(f"聊天请求失败: status={response.status_code}, body={error_body}")
            # 检查是否是 FILE_NOT_FOUND 错误
            if "FILE_NOT_FOUND" in error_body:
                raise AccountRequestError(f"FILE_NOT_FOUND:{response.status_code}")
            raise AccountRequestError(f"聊天请求失败: {response.status_code}")

        logger.info(f"收到响应，开始解析... 响应长度: {len(response.text)}")

        # 解析响应
        result = await self._parse_response(
            response_text=response.text,
            jwt=jwt,
            session_name=session_name,
            team_id=team_id,
            conversation=conversation,
            prompt_tokens=prompt_tokens,
            is_image_model=is_image_model,
            model=model
        )

        return result

    async def _parse_response(
        self,
        response_text: str,
        jwt: str,
        session_name: str,
        team_id: str,
        conversation: Conversation,
        prompt_tokens: int = 0,
        is_image_model: bool = False,
        model: str = ""
    ) -> ChatResult:
        """解析响应，提取文本和图片"""
        result = ChatResult()
        texts = []
        file_infos = []  # 需要下载的文件
        seen_image_hashes = set()  # 用于图片去重

        # 打印原始响应用于调试（前1000字符）
        logger.info(f"原始响应前1000字符: {response_text[:1000]}")

        try:
            data_list = json.loads(response_text)
            logger.info(f"解析到 {len(data_list)} 个响应块")
        except json.JSONDecodeError as e:
            logger.error(f"JSON解析失败: {e}")
            result.text = response_text
            result.prompt_tokens = prompt_tokens
            result.completion_tokens = estimate_tokens(response_text)
            return result

        current_session = session_name

        for data in data_list:
            sar = data.get("streamAssistResponse")
            if not sar:
                continue

            # 获取 session 信息
            session_info = sar.get("sessionInfo", {})
            if session_info.get("session"):
                current_session = session_info["session"]

            # 解析生成的图片（顶层）
            top_gen_imgs = sar.get("generatedImages", [])
            if top_gen_imgs:
                logger.info(f"发现顶层 generatedImages: {len(top_gen_imgs)} 个")
            for gen_img in top_gen_imgs:
                img = self._parse_generated_image(gen_img)
                if img and img.base64_data:
                    # 使用 hash 去重
                    img_hash = hashlib.md5(img.base64_data.encode()).hexdigest()
                    if img_hash not in seen_image_hashes:
                        seen_image_hashes.add(img_hash)
                        result.images.append(img)
                        logger.info(f"解析到内联图片: mime={img.mime_type}, hash={img_hash[:8]}")

            # 解析回复
            answer = sar.get("answer") or {}
            answer_gen_imgs = answer.get("generatedImages", [])
            if answer_gen_imgs:
                logger.info(f"发现 answer.generatedImages: {len(answer_gen_imgs)} 个")
            for gen_img in answer_gen_imgs:
                img = self._parse_generated_image(gen_img)
                if img and img.base64_data:
                    img_hash = hashlib.md5(img.base64_data.encode()).hexdigest()
                    if img_hash not in seen_image_hashes:
                        seen_image_hashes.add(img_hash)
                        result.images.append(img)
                        logger.info(f"解析到内联图片: mime={img.mime_type}, hash={img_hash[:8]}")

            for reply in answer.get("replies", []):
                reply_gen_imgs = reply.get("generatedImages", [])
                if reply_gen_imgs:
                    logger.info(f"发现 reply.generatedImages: {len(reply_gen_imgs)} 个")
                for gen_img in reply_gen_imgs:
                    img = self._parse_generated_image(gen_img)
                    if img and img.base64_data:
                        img_hash = hashlib.md5(img.base64_data.encode()).hexdigest()
                        if img_hash not in seen_image_hashes:
                            seen_image_hashes.add(img_hash)
                            result.images.append(img)
                            logger.info(f"解析到内联图片: mime={img.mime_type}, hash={img_hash[:8]}")

                gc = reply.get("groundedContent", {})
                content = gc.get("content", {})
                text = content.get("text", "")
                thought = content.get("thought", False)

                # 检查文件引用
                file_info = content.get("file")
                if file_info and file_info.get("fileId"):
                    logger.info(f"发现文件引用: fileId={file_info.get('fileId')}, mimeType={file_info.get('mimeType')}")
                    file_infos.append({
                        "fileId": file_info["fileId"],
                        "mimeType": file_info.get("mimeType", "image/png"),
                        "fileName": file_info.get("name")
                    })

                if text and not thought:
                    texts.append(text)

        result.text = "".join(texts)
        logger.info(f"解析完成: 文本长度={len(result.text)}, 内联图片={len(result.images)}, 待下载文件={len(file_infos)}")

        # 检查内联图片是否有效（有base64数据）
        valid_images = [img for img in result.images if img.base64_data]
        logger.info(f"有效内联图片数: {len(valid_images)} / {len(result.images)}")

        # 如果已经有有效的内联图片，跳过文件下载（避免重复）
        if valid_images and file_infos:
            logger.info(f"已有 {len(valid_images)} 个有效内联图片，跳过 {len(file_infos)} 个文件下载")
            file_infos = []
        elif result.images and not valid_images and file_infos:
            # 如果有无效的内联图片，清空它们，使用文件下载代替
            logger.warning(f"检测到 {len(result.images)} 个无效内联图片（无base64数据），清空并使用文件下载")
            result.images = []

        # 下载文件引用的图片
        # 使用参数 team_id 而不是 conversation.team_id，保持一致性
        logger.info(f"[图片下载检查] file_infos={len(file_infos)}, team_id={bool(team_id)}, conversation.team_id={bool(conversation.team_id)}")
        if file_infos and team_id:
            for finfo in file_infos:
                try:
                    logger.info(f"开始下载图片: fileId={finfo['fileId']}")
                    image = await image_service.download_and_save(
                        jwt=jwt,
                        session_name=current_session,
                        file_id=finfo["fileId"],
                        mime_type=finfo["mimeType"],
                        conversation_id=conversation.id,
                        team_id=team_id
                    )
                    if image:
                        result.images.append(image)
                        await conversation_manager.add_image(conversation.id, image.file_name)
                        logger.info(f"图片下载成功: {image.file_name}")
                except Exception as e:
                    logger.error(f"下载图片失败: {e}")

        # 计算 token 数量
        result.prompt_tokens = prompt_tokens
        result.completion_tokens = estimate_tokens(result.text)

        logger.info(f"Token 统计: prompt={result.prompt_tokens}, completion={result.completion_tokens}")

        # 检测图片生成失败
        # 如果是图片生成模型但没有生成图片，标记为失败
        if is_image_model and len(result.images) == 0:
            # 检查响应文本中是否有失败指示词或模型在"解释如何生成"而不是实际生成
            image_gen_failure_indicators = [
                "无法生成图片",
                "图片生成失败",
                "无法创建图像",
                "无法生成图像",
                "无法完成图片生成",
                "I can't generate images",
                "I cannot generate images",
                "unable to generate",
                "failed to generate",
                "image generation failed",
                "cannot create images",
                "I'm not able to generate images",
                # 检测模型在提供prompt建议而不是生成图片
                "Prompt",
                "prompt",
                "建议",
                "选项",
                "option",
            ]

            response_lower = result.text.lower() if result.text else ""
            is_failure = any(ind.lower() in response_lower for ind in image_gen_failure_indicators)

            # 如果有明确的失败提示，或者响应为空/太短，或者模型在提供建议，认为是失败
            if is_failure or len(result.text) < 20:
                error_msg = f"图片生成模型 {model} 未返回图片"
                if result.text and len(result.text) > 50:
                    # 模型可能在提供prompt建议而不是生成图片
                    error_msg = f"图片生成模型 {model} 返回了文字而非图片，可能需要检查模型配置"
                logger.warning(f"{error_msg}, 响应: {result.text[:200] if result.text else '(empty)'}...")
                result.image_generation_failed = True
                result.image_generation_error = error_msg

        return result

    def _parse_generated_image(self, gen_img: dict) -> Optional[ChatImage]:
        """解析生成的图片"""
        import base64

        image_data = gen_img.get("image")
        if not image_data:
            return None

        b64_data = image_data.get("bytesBase64Encoded")
        if not b64_data:
            return None

        mime_type = image_data.get("mimeType", "image/png")

        return ChatImage(
            base64_data=b64_data,
            mime_type=mime_type
        )

    async def _handle_image_generation_failure(self, account: Account):
        """
        处理图片生成失败，触发账号替换

        在后台异步执行账号替换，不阻塞当前请求

        Args:
            account: 失败的账号
        """
        try:
            from app.services.account_replacement_service import account_replacement_service

            # 执行账号替换（使用 credential_service 的并发注册功能）
            success, message = await account_replacement_service.replace_failed_account(
                failed_account_index=account.index
            )

            if success:
                logger.info(f"图片生成失败账号替换成功: {message}")
            else:
                logger.warning(f"图片生成失败账号替换失败: {message}")

        except Exception as e:
            logger.error(f"处理图片生成失败时出错: {e}")
            import traceback
            traceback.print_exc()

    async def chat_stream(
        self,
        conversation: Conversation,
        message: str,
        file_ids: List[str] = None
    ) -> AsyncGenerator[str, None]:
        """
        流式聊天（返回 SSE 格式）

        目前 Gemini Business API 不支持真正的流式，
        这里模拟流式输出
        """
        result = await self.chat(conversation, message, file_ids)

        # 模拟流式输出
        chunk_size = 20
        text = result.text

        for i in range(0, len(text), chunk_size):
            chunk = text[i:i + chunk_size]
            yield chunk
            await asyncio.sleep(0.05)


# 全局聊天服务实例
chat_service = ChatService()
