"""Discord alerts: a DM from the site's bot, and/or the person's own channel webhook.

Discord only lets a bot DM someone who shares a server with it, so connecting
asks the person (Discord's own consent screen, scopes ``identify`` and
``guilds.join``) to be added to the site owner's private server where the bot
lives. Only their Discord user id and name are kept; the one-off access token
is used once and dropped. Nothing here can place trades.
"""
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.urls import reverse

API = "https://discord.com/api/v10"
AUTHORIZE = "https://discord.com/oauth2/authorize"
SCOPES = "identify guilds.join"
# Bot permission needed to add people to the server: Create Instant Invite.
BOT_PERMISSIONS = "1"
USER_AGENT = "Wealtharr/2 (private tracker)"


class DiscordError(ValueError):
    """A Discord call failed; the message is safe to show (no secrets)."""


def configured(cfg):
    """Whether the site owner has filled in everything DMs need."""
    from .services import decrypt_secret
    return bool(cfg.discord_client_id and cfg.discord_guild_id and
                decrypt_secret(cfg.discord_client_secret_ciphertext) and
                decrypt_secret(cfg.discord_bot_token_ciphertext))


def redirect_uri(request):
    path = reverse("discord_callback")
    return (settings.SITE_URL + path) if settings.SITE_URL else request.build_absolute_uri(path)


def authorize_url(cfg, redirect, state):
    return AUTHORIZE + "?" + urlencode({
        "client_id": cfg.discord_client_id, "response_type": "code", "redirect_uri": redirect,
        "scope": SCOPES, "state": state, "prompt": "consent",
    })


def bot_invite_url(cfg):
    """Link for the site owner to add the bot to their own server."""
    if not cfg.discord_client_id:
        return ""
    return AUTHORIZE + "?" + urlencode({
        "client_id": cfg.discord_client_id, "scope": "bot", "permissions": BOT_PERMISSIONS,
        "guild_id": cfg.discord_guild_id or "", "disable_guild_select": "true" if cfg.discord_guild_id else "false",
    })


def _call(method, path, *, token=None, bot=None, payload=None, form=None, auth=None):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif form is not None:
        data = urlencode(form).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Authorization"] = "Bearer " + token
    elif bot:
        headers["Authorization"] = "Bot " + bot
    elif auth:
        headers["Authorization"] = auth
    request = Request(API + path, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=10) as response:
            body = response.read() or b"{}"
            return response.status, json.loads(body)
    except HTTPError as exc:
        raise DiscordError("Discord refused the request (HTTP %s)." % exc.code) from None
    except (URLError, OSError, ValueError):
        raise DiscordError("Discord couldn't be reached.") from None


def connect(cfg, code, redirect):
    """Finish the consent screen: returns (user_id, username) after adding them to the server."""
    import base64
    from .services import decrypt_secret
    basic = base64.b64encode(("%s:%s" % (cfg.discord_client_id, decrypt_secret(
        cfg.discord_client_secret_ciphertext))).encode("utf-8")).decode("ascii")
    _, grant = _call("POST", "/oauth2/token", auth="Basic " + basic, form={
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect})
    access = grant.get("access_token")
    if not access:
        raise DiscordError("Discord didn't confirm the connection.")
    _, user = _call("GET", "/users/@me", token=access)
    user_id, name = str(user.get("id", "")), user.get("global_name") or user.get("username") or ""
    if not user_id.isdigit():
        raise DiscordError("Discord didn't return your account.")
    # 201 added, 204 already a member: either way the bot can now DM them.
    _call("PUT", "/guilds/%s/members/%s" % (cfg.discord_guild_id, user_id),
          bot=decrypt_secret(cfg.discord_bot_token_ciphertext), payload={"access_token": access})
    return user_id, name[:100]


def send_dm(cfg, user_id, text):
    """Open (or reuse) the DM channel with this person and post the message."""
    from .services import decrypt_secret
    bot = decrypt_secret(cfg.discord_bot_token_ciphertext)
    if not bot or not str(user_id).isdigit():
        raise DiscordError("Discord DMs aren't set up for this account.")
    _, channel = _call("POST", "/users/@me/channels", bot=bot, payload={"recipient_id": str(user_id)})
    channel_id = str(channel.get("id", ""))
    if not channel_id.isdigit():
        raise DiscordError("Discord didn't open a DM.")
    _call("POST", "/channels/%s/messages" % channel_id, bot=bot,
          payload={"content": text[:1900], "allowed_mentions": {"parse": []}})


WEBHOOK = re.compile(r"https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/[0-9]+/[A-Za-z0-9_-]+")


def valid_webhook(url):
    return bool(WEBHOOK.fullmatch(url or ""))


def post_webhook(url, text):
    """Post into a channel on the person's own server through their webhook."""
    if not valid_webhook(url):
        raise DiscordError("Your Discord webhook isn't set up.")
    request = Request(url, data=json.dumps({"content": text[:1900], "allowed_mentions": {"parse": []}})
                      .encode("utf-8"), headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
                      method="POST")
    try:
        with urlopen(request, timeout=10) as response:
            if response.status not in (200, 204):
                raise DiscordError("Discord refused the webhook post.")
    except HTTPError as exc:
        raise DiscordError("Discord refused the webhook post (HTTP %s)." % exc.code) from None
    except (URLError, OSError):
        raise DiscordError("Discord couldn't be reached.") from None
