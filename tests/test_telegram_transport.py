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
                    "chat": {"id": -100123, "type": "supergroup", "title": "SG Rumours"},
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
    assert message.group_display_name == "SG Rumours"
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
                    "chat": {"id": -200456, "type": "group", "title": "Estate Watch"},
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
    assert message.text_simhash is not None


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
        message_id = await transport.send_group_message("-100123", "Correction")

    assert message_id == 1


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
        message_id = await transport.send_group_message(
            "-100123",
            "Correction",
            reply_to_message_id=77,
        )

    assert message_id == 2


@pytest.mark.asyncio
async def test_send_group_message_supports_reply_markup() -> None:
    settings = AppSettings(telegram_bot_token="test-token")

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        assert payload == {
            "chat_id": "-100123",
            "text": "Correction",
            "disable_web_page_preview": True,
            "reply_markup": {"inline_keyboard": [[{"text": "Translate", "callback_data": "x"}]]},
        }
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 3}})

    async with httpx.AsyncClient(
        base_url=settings.telegram_base_url,
        transport=httpx.MockTransport(handler),
    ) as client:
        transport = TelegramTransport(settings, client=client)
        message_id = await transport.send_group_message(
            "-100123",
            "Correction",
            reply_markup={"inline_keyboard": [[{"text": "Translate", "callback_data": "x"}]]},
        )

    assert message_id == 3


@pytest.mark.asyncio
async def test_fetch_updates_calls_telegram_get_updates() -> None:
    settings = AppSettings(telegram_bot_token="test-token")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/bottest-token/getUpdates"
        payload = json.loads(request.content.decode("utf-8"))
        assert payload == {
            "timeout": 20,
            "limit": 10,
            "allowed_updates": ["message", "callback_query"],
            "offset": 101,
        }
        assert request.extensions["timeout"]["read"] == 25.0
        return httpx.Response(
            200,
            json={"ok": True, "result": [{"update_id": 101, "message": {"message_id": 1}}]},
        )

    async with httpx.AsyncClient(
        base_url=settings.telegram_base_url,
        transport=httpx.MockTransport(handler),
    ) as client:
        transport = TelegramTransport(settings, client=client)
        updates = await transport.fetch_updates(offset=101, timeout_seconds=20, limit=10)

    assert updates == [{"update_id": 101, "message": {"message_id": 1}}]


@pytest.mark.asyncio
async def test_delete_webhook_calls_telegram_delete_webhook() -> None:
    settings = AppSettings(telegram_bot_token="test-token")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/bottest-token/deleteWebhook"
        payload = json.loads(request.content.decode("utf-8"))
        assert payload == {"drop_pending_updates": False}
        return httpx.Response(200, json={"ok": True, "result": True})

    async with httpx.AsyncClient(
        base_url=settings.telegram_base_url,
        transport=httpx.MockTransport(handler),
    ) as client:
        transport = TelegramTransport(settings, client=client)
        await transport.delete_webhook(drop_pending_updates=False)


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


@pytest.mark.asyncio
async def test_normalize_webhook_parses_reply_bot_mention_as_factcheck_command() -> None:
    settings = AppSettings(
        telegram_bot_token="test-token",
        telegram_bot_username="isrealanot_bot",
    )
    async with httpx.AsyncClient(base_url=settings.telegram_base_url) as client:
        transport = TelegramTransport(settings, client=client)
        messages = await transport.normalize_webhook(
            {
                "update_id": 4,
                "message": {
                    "message_id": 46,
                    "date": 1_710_000_789,
                    "chat": {"id": -100123, "type": "supergroup"},
                    "from": {"id": 99},
                    "text": "@isrealanot_bot",
                    "reply_to_message": {
                        "text": "MOH confirmed that drinking salt water cures dengue"
                    },
                },
            }
        )

    assert len(messages) == 1
    message = messages[0]
    assert message.command_name == "factcheck"
    assert message.command_arg_text == ""
    assert message.text == ""
    assert message.quoted_text == "MOH confirmed that drinking salt water cures dengue"
    assert message.command_target_text() == "MOH confirmed that drinking salt water cures dengue"


@pytest.mark.asyncio
async def test_normalize_webhook_ignores_reply_mentions_for_other_users() -> None:
    settings = AppSettings(
        telegram_bot_token="test-token",
        telegram_bot_username="isrealanot_bot",
    )
    async with httpx.AsyncClient(base_url=settings.telegram_base_url) as client:
        transport = TelegramTransport(settings, client=client)
        messages = await transport.normalize_webhook(
            {
                "update_id": 5,
                "message": {
                    "message_id": 47,
                    "date": 1_710_000_790,
                    "chat": {"id": -100123, "type": "supergroup"},
                    "from": {"id": 99},
                    "text": "@anotherbot",
                    "reply_to_message": {
                        "text": "MOH confirmed that drinking salt water cures dengue"
                    },
                },
            }
        )

    assert len(messages) == 1
    message = messages[0]
    assert message.command_name is None
    assert message.command_arg_text == ""
    assert message.text == "@anotherbot"


@pytest.mark.asyncio
async def test_normalize_webhook_ignores_bot_mention_without_reply() -> None:
    settings = AppSettings(
        telegram_bot_token="test-token",
        telegram_bot_username="isrealanot_bot",
    )
    async with httpx.AsyncClient(base_url=settings.telegram_base_url) as client:
        transport = TelegramTransport(settings, client=client)
        messages = await transport.normalize_webhook(
            {
                "update_id": 6,
                "message": {
                    "message_id": 48,
                    "date": 1_710_000_791,
                    "chat": {"id": -100123, "type": "supergroup"},
                    "from": {"id": 99},
                    "text": "@isrealanot_bot",
                },
            }
        )

    assert len(messages) == 1
    message = messages[0]
    assert message.command_name is None
    assert message.command_arg_text == ""
    assert message.text == "@isrealanot_bot"


@pytest.mark.asyncio
async def test_normalize_webhook_resolves_bot_username_via_get_me_for_reply_mentions() -> None:
    settings = AppSettings(telegram_bot_token="test-token")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/bottest-token/getMe":
            return httpx.Response(
                200,
                json={"ok": True, "result": {"id": 42, "username": "isrealanot_bot"}},
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(
        base_url=settings.telegram_base_url,
        transport=httpx.MockTransport(handler),
    ) as client:
        transport = TelegramTransport(settings, client=client)
        messages = await transport.normalize_webhook(
            {
                "update_id": 7,
                "message": {
                    "message_id": 49,
                    "date": 1_710_000_792,
                    "chat": {"id": -100123, "type": "supergroup"},
                    "from": {"id": 99},
                    "text": "@isrealanot_bot",
                    "reply_to_message": {"text": "Suspicious claim"},
                },
            }
        )

    assert len(messages) == 1
    message = messages[0]
    assert message.command_name == "factcheck"
    assert message.command_target_text() == "Suspicious claim"


@pytest.mark.asyncio
async def test_normalize_webhook_parses_reply_to_bot_message_as_followup() -> None:
    settings = AppSettings(
        telegram_bot_token="test-token",
        telegram_bot_username="isrealanot_bot",
    )
    async with httpx.AsyncClient(base_url=settings.telegram_base_url) as client:
        transport = TelegramTransport(settings, client=client)
        messages = await transport.normalize_webhook(
            {
                "update_id": 8,
                "message": {
                    "message_id": 50,
                    "date": 1_710_000_793,
                    "chat": {"id": -100123, "type": "supergroup"},
                    "from": {"id": 100},
                    "text": "What about these other reasons?",
                    "reply_to_message": {
                        "text": "Verdict: false (95% confidence)",
                        "from": {
                            "id": 42,
                            "is_bot": True,
                            "username": "isrealanot_bot",
                        },
                    },
                },
            }
        )

    assert len(messages) == 1
    message = messages[0]
    assert message.command_name == "followup"
    assert message.text == "What about these other reasons?"
    assert message.quoted_text == "Verdict: false (95% confidence)"


@pytest.mark.asyncio
async def test_normalize_webhook_ignores_reply_to_other_bot_for_followup() -> None:
    settings = AppSettings(
        telegram_bot_token="test-token",
        telegram_bot_username="isrealanot_bot",
    )
    async with httpx.AsyncClient(base_url=settings.telegram_base_url) as client:
        transport = TelegramTransport(settings, client=client)
        messages = await transport.normalize_webhook(
            {
                "update_id": 9,
                "message": {
                    "message_id": 51,
                    "date": 1_710_000_794,
                    "chat": {"id": -100123, "type": "supergroup"},
                    "from": {"id": 100},
                    "text": "What about these other reasons?",
                    "reply_to_message": {
                        "text": "Verdict: false (95% confidence)",
                        "from": {
                            "id": 333,
                            "is_bot": True,
                            "username": "anotherbot",
                        },
                    },
                },
            }
        )

    assert len(messages) == 1
    message = messages[0]
    assert message.command_name is None
    assert message.text == "What about these other reasons?"


@pytest.mark.asyncio
async def test_answer_callback_query_calls_telegram_answer_callback_query() -> None:
    settings = AppSettings(telegram_bot_token="test-token")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/bottest-token/answerCallbackQuery"
        payload = json.loads(request.content.decode("utf-8"))
        assert payload == {
            "callback_query_id": "abc123",
            "text": "Choose language",
        }
        return httpx.Response(200, json={"ok": True, "result": True})

    async with httpx.AsyncClient(
        base_url=settings.telegram_base_url,
        transport=httpx.MockTransport(handler),
    ) as client:
        transport = TelegramTransport(settings, client=client)
        await transport.answer_callback_query("abc123", text="Choose language")


@pytest.mark.asyncio
async def test_edit_message_reply_markup_calls_telegram_edit_message_reply_markup() -> None:
    settings = AppSettings(telegram_bot_token="test-token")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/bottest-token/editMessageReplyMarkup"
        payload = json.loads(request.content.decode("utf-8"))
        assert payload == {
            "chat_id": "-100123",
            "message_id": 50,
            "reply_markup": {"inline_keyboard": [[{"text": "English", "callback_data": "x"}]]},
        }
        return httpx.Response(200, json={"ok": True, "result": True})

    async with httpx.AsyncClient(
        base_url=settings.telegram_base_url,
        transport=httpx.MockTransport(handler),
    ) as client:
        transport = TelegramTransport(settings, client=client)
        await transport.edit_message_reply_markup(
            "-100123",
            50,
            reply_markup={"inline_keyboard": [[{"text": "English", "callback_data": "x"}]]},
        )


@pytest.mark.asyncio
async def test_normalize_webhook_parses_translate_menu_callback_query() -> None:
    settings = AppSettings(telegram_bot_token="test-token")
    async with httpx.AsyncClient(base_url=settings.telegram_base_url) as client:
        transport = TelegramTransport(settings, client=client)
        messages = await transport.normalize_webhook(
            {
                "update_id": 10,
                "callback_query": {
                    "id": "cb-1",
                    "from": {"id": 777},
                    "data": "translate:menu",
                    "message": {
                        "message_id": 99,
                        "chat": {"id": -100123, "type": "supergroup"},
                        "text": "Verdict: false (95% confidence)",
                    },
                },
            }
        )

    assert len(messages) == 1
    message = messages[0]
    assert message.command_name == "translate_menu"
    assert message.command_arg_text == ""
    assert message.callback_query_id == "cb-1"
    assert message.transport_message_id == 99
    assert message.text == "Verdict: false (95% confidence)"


@pytest.mark.asyncio
async def test_normalize_webhook_parses_translate_language_callback_query() -> None:
    settings = AppSettings(telegram_bot_token="test-token")
    async with httpx.AsyncClient(base_url=settings.telegram_base_url) as client:
        transport = TelegramTransport(settings, client=client)
        messages = await transport.normalize_webhook(
            {
                "update_id": 11,
                "callback_query": {
                    "id": "cb-2",
                    "from": {"id": 777},
                    "data": "translate:lang:zh",
                    "message": {
                        "message_id": 100,
                        "chat": {"id": -100123, "type": "supergroup"},
                        "text": "Verdict: false (95% confidence)",
                    },
                },
            }
        )

    assert len(messages) == 1
    message = messages[0]
    assert message.command_name == "translate_lang"
    assert message.command_arg_text == "zh"
    assert message.callback_query_id == "cb-2"
    assert message.transport_message_id == 100
