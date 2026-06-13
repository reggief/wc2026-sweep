"""
Thin wrapper around the Whapi.Cloud REST API.

Sends messages to a WhatsApp group and parses inbound webhook payloads.
"""

from __future__ import annotations

import os

import httpx

WHAPI_BASE = "https://gate.whapi.cloud"


def _token() -> str:
    token = os.environ.get("WHAPI_TOKEN", "")
    if not token:
        raise RuntimeError("WHAPI_TOKEN environment variable is not set")
    return token


def _group_id() -> str:
    gid = os.environ.get("WHAPI_GROUP_ID", "")
    if not gid:
        raise RuntimeError("WHAPI_GROUP_ID environment variable is not set")
    return gid


def send_message(text: str, group_id: str | None = None) -> None:
    """Send a text message to the configured WhatsApp group."""
    target = group_id or _group_id()
    r = httpx.post(
        f"{WHAPI_BASE}/messages/text",
        headers={"Authorization": f"Bearer {_token()}"},
        json={"to": target, "body": text},
        timeout=15,
    )
    r.raise_for_status()


def bot_phone_number() -> str:
    """Return the bot's own phone number from the Whapi account info endpoint."""
    r = httpx.get(
        f"{WHAPI_BASE}/settings",
        headers={"Authorization": f"Bearer {_token()}"},
        timeout=10,
    )
    r.raise_for_status()
    return r.json().get("phone", "")


def parse_webhook(payload: dict) -> list[dict]:
    """
    Extract inbound messages from a Whapi webhook payload.

    Returns a list of dicts, each with keys:
      id, from, group_id, sender_phone, sender_name, text, is_from_me
    """
    messages = []
    for msg in payload.get("messages", []):
        if msg.get("type") != "text":
            continue
        chat_id: str = msg.get("chat_id", "")
        # Whapi provides the sender's WhatsApp display name in push_name or notify
        sender_name: str = msg.get("push_name") or msg.get("notify") or ""
        messages.append(
            {
                "id": msg.get("id", ""),
                "from": msg.get("from", ""),
                "group_id": chat_id,
                "sender_phone": msg.get("from", "").split("@")[0],
                "sender_name": sender_name,
                "text": msg.get("text", {}).get("body", ""),
                "is_from_me": bool(msg.get("from_me", False)),
            }
        )
    return messages
