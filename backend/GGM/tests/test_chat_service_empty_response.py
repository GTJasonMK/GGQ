import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.models.chat import ChatImage, ChatResult
from app.services.chat_service import ChatService


def test_empty_result_when_text_blank_and_no_images():
    service = ChatService()
    result = ChatResult(text="  ", images=[])
    assert service._is_empty_result(result)


def test_empty_result_when_text_empty_and_no_images():
    service = ChatService()
    result = ChatResult(text="", images=[])
    assert service._is_empty_result(result)


def test_not_empty_when_text_present():
    service = ChatService()
    result = ChatResult(text="ok", images=[])
    assert not service._is_empty_result(result)


def test_not_empty_when_images_present():
    service = ChatService()
    result = ChatResult(text="", images=[ChatImage(base64_data="abc")])
    assert not service._is_empty_result(result)
