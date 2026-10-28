"""Telegram alerts: a message from the site's bot to each person who connected.

Connecting needs no public webhook (the site can stay private): the person
opens the bot with a one-time code and presses Start, then the site reads the
bot's recent messages (getUpdates) to find that code and keep the chat id.
Only the chat id and a display name are kept. Nothing here can place trades.
"""
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

API = "https://api.telegram.org/bot%s/%s"
TOKEN = re.compile(r"[0-9]{5,16}:[A-Za-z0-9_-]{30,64}")
CODE = re.compile(r"[A-Za-z0-9_-]{16,64}")


class TelegramError(ValueError):
    """A Telegram call failed; the message is safe to show (no secrets)."""


def valid_token(token):
    return bool(TOKEN.fullmatch(token or ""))


def _call(token, method, payload=None):
    if not valid_token(token):
        raise TelegramError("The Telegram bot isn't set up.")
    data = json.dumps(payload or {}).encode("utf-8")
    request = Request(API % (token, method), data=data, method="POST",
                      headers={"Content-Type": "application/json", "User-Agent": "Wealtharr/2 (private tracker)"})
    try:
        with urlopen(request, timeout=10) as response:  # nosec B310 - fixed https endpoint
            body = json.loads(response.read(1_000_000) or b"{}")
    except HTTPError as exc:
        raise TelegramError("Telegram refused the request (HTTP %s)." % exc.code) from None
    except (URLError, OSError, ValueError):
        raise TelegramError("Telegram couldn't be reached.") from None
    if not body.get("ok"):
        raise TelegramError("Telegram refused the request.")
    return body.get("result")


def bot_username(token):
    """The bot's @username, which also proves the token works."""
    me = _call(token, "getMe") or {}
    name = str(me.get("username") or "")
    if not re.fullmatch(r"[A-Za-z0-9_]{3,64}", name):
        raise TelegramError("Telegram didn't return the bot's name.")
    return name


def start_link(username, code):
    return "https://t.me/%s?start=%s" % (quote(username, safe=""), quote(code, safe=""))


def find_start(token, code):
    """(chat_id, name) of the private chat that sent "/start <code>", or None."""
    if not CODE.fullmatch(code or ""):
        return None
    updates = _call(token, "getUpdates", {"offset": -100, "limit": 100, "allowed_updates": ["message"]}) or []
    for update in reversed(updates):
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        if chat.get("type") == "private" and (message.get("text") or "").strip() == "/start " + code:
            chat_id = str(chat.get("id", ""))
            if re.fullmatch(r"-?[0-9]{1,20}", chat_id):
                sender = message.get("from") or {}
                name = sender.get("username") or sender.get("first_name") or ""
                return chat_id, str(name)[:100]
    return None


def send(token, chat_id, text):
    if not re.fullmatch(r"-?[0-9]{1,20}", str(chat_id or "")):
        raise TelegramError("Telegram isn't connected for this account.")
    _call(token, "sendMessage", {"chat_id": str(chat_id), "text": text[:4000],
                                 "disable_web_page_preview": True})
