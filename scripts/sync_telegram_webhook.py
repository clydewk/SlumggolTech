from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

TUNNEL_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.IGNORECASE)


def main() -> None:
    base_url = os.environ["TELEGRAM_BASE_URL"].rstrip("/")
    bot_token = os.environ["TELEGRAM_BOT_TOKEN"]
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
    log_path = Path(os.environ.get("CLOUDFLARED_LOG_PATH", "/shared/cloudflared.log"))
    poll_interval_seconds = float(os.environ.get("WEBHOOK_SYNC_POLL_SECONDS", "2"))
    current_webhook_url: str | None = None

    while True:
        tunnel_url = latest_tunnel_url(log_path)
        if tunnel_url is not None:
            webhook_url = f"{tunnel_url.rstrip('/')}/webhooks/telegram"
            if webhook_url != current_webhook_url:
                did_register = register_webhook(
                    base_url=base_url,
                    bot_token=bot_token,
                    webhook_url=webhook_url,
                    secret=secret,
                )
                if did_register:
                    print(f"Webhook synced to {webhook_url}", flush=True)
                    current_webhook_url = webhook_url

        time.sleep(poll_interval_seconds)


def latest_tunnel_url(log_path: Path) -> str | None:
    if not log_path.exists():
        return None
    matches = TUNNEL_URL_RE.findall(log_path.read_text())
    if not matches:
        return None
    return matches[-1]


def register_webhook(
    *,
    base_url: str,
    bot_token: str,
    webhook_url: str,
    secret: str,
) -> bool:
    payload: dict[str, object] = {
        "url": webhook_url,
        "allowed_updates": ["message"],
        "drop_pending_updates": False,
    }
    if secret:
        payload["secret_token"] = secret

    request = urllib.request.Request(
        f"{base_url}/bot{bot_token}/setWebhook",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        print(
            f"Webhook sync failed for {webhook_url}: HTTP {exc.code} {error_body}",
            flush=True,
        )
        return False
    except urllib.error.URLError as exc:
        print(f"Webhook sync failed for {webhook_url}: {exc}", flush=True)
        return False

    response_json = json.loads(response_body)
    if response_json.get("ok") is not True:
        print(
            f"Webhook sync failed for {webhook_url}: {response_body}",
            flush=True,
        )
        return False

    print(f"Telegram setWebhook response: {response_body}", flush=True)
    return True


if __name__ == "__main__":
    main()
