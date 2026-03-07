from __future__ import annotations

import json

import httpx
import pytest

from slumggol_bot.config import AppSettings
from slumggol_bot.schemas import ContentKind
from slumggol_bot.transport.telegram import TelegramTransport


@pytest.mark.asyncio
async def test_normalize_webhook_ignores_non_group_updates() -> None:
    settings = AppSettings(telegram_bot_token="test-token")
    async with httpx.AsyncClient(base_url=settings.telegram_base_url) as client:
        transport = TelegramTransport(settings, client=client)
        messages = await transport.normalize_webhook(
            {
                "update_id": 1,
                "message": {
                    "message_id": 12,
                    "date": 1_710_000_000,
                    "chat": {"id": 1001, "type": "private"},
                    "from": {"id": 42},
                    "text": "hello",
                },
            }
        )

    assert messages == []


@pytest.mark.asyncio
async def test_normalize_webhook_parses_group_text_message() -> None:
    settings = AppSettings(telegram_bot_token="test-token")
    async with httpx.AsyncClient(base_url=settings.telegram_base_url) as client:
        transport = TelegramTransport(settings, client=client)
        messages = await transport.normalize_webhook(
            {
                "update_id": 1,
                "message": {
                    "message_id": 12,
                    "date": 1_710_000_000,
                    "chat": {"id": -100123, "type": "supergroup"},
                    "from": {"id": 42},
                    "text": "Claim text",
                    "reply_to_message": {"text": "Earlier claim"},
                    "forward_origin": {"type": "user"},
                },
            }
        )

    assert len(messages) == 1
    message = messages[0]
    assert message.group_id == "-100123"
    assert message.message_id == "-100123:12"
    assert message.transport_message_id == 12
    assert message.sender_id == "42"
    assert message.content_kind == ContentKind.TEXT
    assert message.text == "Claim text"
    assert message.quoted_text == "Earlier claim"
    assert message.forwarded is True
    assert message.text_sha256 is not None
    assert message.text_simhash is not None


@pytest.mark.asyncio
async def test_normalize_webhook_resolves_photo_url() -> None:
    settings = AppSettings(telegram_bot_token="test-token")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/bottest-token/getFile":
            assert request.url.params["file_id"] == "photo-large"
            return httpx.Response(
                200,
                json={"ok": True, "result": {"file_path": "photos/file_1.jpg"}},
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(
        base_url=settings.telegram_base_url,
        transport=httpx.MockTransport(handler),
    ) as client:
        transport = TelegramTransport(settings, client=client)
        messages = await transport.normalize_webhook(
            {
                "update_id": 2,
                "message": {
                    "message_id": 98,
                    "date": 1_710_000_123,
                    "chat": {"id": -200456, "type": "group"},
                    "from": {"id": 77},
                    "caption": "Look at this",
                    "photo": [
                        {"file_id": "photo-small"},
                        {"file_id": "photo-large"},
                    ],
                },
            }
        )

    assert len(messages) == 1
    message = messages[0]
    assert message.content_kind == ContentKind.IMAGE
    assert message.text == ""
    assert message.caption == "Look at this"
    assert message.media_mimetype == "image/jpeg"
    assert message.media_url == "https://api.telegram.org/file/bottest-token/photos/file_1.jpg"
    assert message.text_sha256 is not None


@pytest.mark.asyncio
async def test_send_group_message_uses_telegram_send_message() -> None:
    settings = AppSettings(telegram_bot_token="test-token")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/bottest-token/sendMessage"
        payload = json.loads(request.content.decode("utf-8"))
        assert payload == {
            "chat_id": "-100123",
            "text": "Correction",
            "disable_web_page_preview": True,
        }
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    async with httpx.AsyncClient(
        base_url=settings.telegram_base_url,
        transport=httpx.MockTransport(handler),
    ) as client:
        transport = TelegramTransport(settings, client=client)
        await transport.send_group_message("-100123", "Correction")


@pytest.mark.asyncio
async def test_send_group_message_supports_reply_to_message() -> None:
    settings = AppSettings(telegram_bot_token="test-token")

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        assert payload == {
            "chat_id": "-100123",
            "text": "Correction",
            "disable_web_page_preview": True,
            "reply_to_message_id": 77,
        }
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 2}})

    async with httpx.AsyncClient(
        base_url=settings.telegram_base_url,
        transport=httpx.MockTransport(handler),
    ) as client:
        transport = TelegramTransport(settings, client=client)
        await transport.send_group_message(
            "-100123",
            "Correction",
            reply_to_message_id=77,
        )


@pytest.mark.asyncio
async def test_normalize_webhook_parses_factcheck_command() -> None:
    settings = AppSettings(telegram_bot_token="test-token")
    async with httpx.AsyncClient(base_url=settings.telegram_base_url) as client:
        transport = TelegramTransport(settings, client=client)
        messages = await transport.normalize_webhook(
            {
                "update_id": 3,
                "message": {
                    "message_id": 45,
                    "date": 1_710_000_456,
                    "chat": {"id": -100123, "type": "supergroup"},
                    "from": {"id": 42},
                    "text": "/factcheck MOH confirmed that drinking salt water cures dengue",
                },
            }
        )

    assert len(messages) == 1
    message = messages[0]
    assert message.command_name == "factcheck"
    assert message.command_arg_text == "MOH confirmed that drinking salt water cures dengue"
    assert message.text == "MOH confirmed that drinking salt water cures dengue"
    assert message.text_simhash is not None
