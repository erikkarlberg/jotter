"""Google OAuth2 authentication + GNOME Secret Service token storage.

Preferred path: GNOME Online Accounts (GOA) — no client_secrets.json needed.
Fallback path:  InstalledAppFlow with client_secrets.json + SecretService cache.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ImapCredentials:
    """Minimal credentials bundle for IMAP XOAUTH2 authentication."""
    email: str
    access_token: str


# ---------------------------------------------------------------------------
# GNOME Online Accounts (primary path)
# ---------------------------------------------------------------------------

def list_goa_google_accounts() -> list:
    """
    Return GOA account objects that have a Google provider with mail enabled.
    Returns an empty list if GOA is unavailable or no matching accounts exist.
    """
    try:
        import gi
        gi.require_version("Goa", "1.0")
        from gi.repository import Goa
        client = Goa.Client.new_sync(None)
        result = []
        for obj in client.get_accounts():
            acct = obj.get_account()
            if (acct.props.provider_type == "google"
                    and not acct.props.mail_disabled
                    and obj.get_oauth2_based() is not None
                    and obj.get_mail() is not None):
                result.append(obj)
        return result
    except Exception as exc:
        logger.debug("GOA unavailable: %s", exc)
        return []


def get_goa_credentials(goa_object) -> Optional[ImapCredentials]:
    """
    Ask GOA for a fresh access token for *goa_object*.
    Safe to call from a background thread.
    """
    try:
        email = goa_object.get_mail().props.email_address
        access_token, _expires_in = goa_object.get_oauth2_based().call_get_access_token_sync(None)
        return ImapCredentials(email=email, access_token=access_token)
    except Exception as exc:
        logger.warning("Failed to get GOA access token: %s", exc)
        return None

# OAuth2 scopes — full IMAP access (same as Apple Notes uses)
SCOPES = ["https://mail.google.com/"]

# GNOME Secret Service collection / item labels
_SECRET_COLLECTION = "default"
_SECRET_LABEL = "Jotter Google OAuth Token"
_SECRET_ATTRS = {"application": "io.github.erikkarlberg.jotter", "service": "google-oauth2"}


def _get_keyring():
    """Return a secretstorage Collection, or None if unavailable."""
    try:
        import secretstorage
        bus = secretstorage.dbus_init()
        collection = secretstorage.get_default_collection(bus)
        if collection.is_locked():
            collection.unlock()
        return collection
    except Exception as exc:
        logger.warning("Secret Service unavailable: %s", exc)
        return None


def _load_token_json() -> Optional[dict]:
    collection = _get_keyring()
    if collection is None:
        return None
    try:
        item = next(collection.search_items(_SECRET_ATTRS), None)
        if item is None:
            return None
        return json.loads(item.get_secret().decode())
    except Exception as exc:
        logger.warning("Failed to load token from Secret Service: %s", exc)
        return None


def _save_token_json(token_dict: dict) -> None:
    collection = _get_keyring()
    if collection is None:
        logger.warning("Cannot save token: Secret Service unavailable")
        return
    try:
        # Delete any existing item first
        for item in collection.search_items(_SECRET_ATTRS):
            item.delete()
        collection.create_item(
            _SECRET_LABEL,
            _SECRET_ATTRS,
            json.dumps(token_dict).encode(),
            replace=True,
        )
    except Exception as exc:
        logger.warning("Failed to save token to Secret Service: %s", exc)


def _delete_token() -> None:
    collection = _get_keyring()
    if collection is None:
        return
    try:
        for item in collection.search_items(_SECRET_ATTRS):
            item.delete()
    except Exception as exc:
        logger.warning("Failed to delete token: %s", exc)


def _make_credentials(token_dict: dict):
    """Reconstruct a google.oauth2.credentials.Credentials from a dict."""
    from google.oauth2.credentials import Credentials

    return Credentials(
        token=token_dict.get("token"),
        refresh_token=token_dict.get("refresh_token"),
        token_uri=token_dict.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=token_dict.get("client_id"),
        client_secret=token_dict.get("client_secret"),
        scopes=token_dict.get("scopes"),
    )


def _creds_to_dict(creds) -> dict:
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes) if creds.scopes else SCOPES,
    }


# Public API
# ---------------------------------------------------------------------------

def get_credentials(client_secrets_path: str):
    """
    Return valid Google credentials, refreshing or re-running OAuth flow as needed.

    *client_secrets_path* is the path to a ``client_secrets.json`` file
    downloaded from Google Cloud Console (Desktop application type).

    Raises ``RuntimeError`` if no client secrets file is found.
    """
    import os
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow

    # 1. Try to load stored credentials
    token_dict = _load_token_json()
    creds = None
    if token_dict:
        try:
            creds = _make_credentials(token_dict)
        except Exception as exc:
            logger.warning("Could not reconstruct credentials: %s", exc)
            creds = None

    # 2. Refresh if expired
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token_json(_creds_to_dict(creds))
            return creds
        except Exception as exc:
            logger.warning("Token refresh failed: %s", exc)
            creds = None

    if creds and creds.valid:
        return creds

    # 3. Run OAuth flow
    if not os.path.isfile(client_secrets_path):
        raise RuntimeError(
            f"Google client secrets file not found: {client_secrets_path}\n"
            "Download it from https://console.cloud.google.com/ → "
            "APIs & Services → Credentials → OAuth 2.0 Client IDs → Desktop."
        )

    flow = InstalledAppFlow.from_client_secrets_file(client_secrets_path, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    _save_token_json(_creds_to_dict(creds))
    return creds


def get_email(creds) -> Optional[str]:
    """Return the authenticated user's e-mail address via Google userinfo API."""
    try:
        import urllib.request, urllib.error
        req = urllib.request.Request(
            "https://www.googleapis.com/oauth2/v1/userinfo?alt=json",
            headers={"Authorization": f"Bearer {creds.token}"},
        )
        with urllib.request.urlopen(req) as resp:
            info = json.loads(resp.read())
            return info.get("email")
    except Exception as exc:
        logger.warning("Could not fetch user email: %s", exc)
        return None


def get_xoauth2_string(email: str, access_token: str) -> str:
    """
    Return the base64-encoded SASL XOAUTH2 string for IMAP authentication.

    Format: ``user=<email>\\x01auth=Bearer <token>\\x01\\x01``
    """
    raw = f"user={email}\x01auth=Bearer {access_token}\x01\x01"
    return base64.b64encode(raw.encode()).decode()


def get_imap_credentials_from_client_secrets(client_secrets_path: str) -> Optional[ImapCredentials]:
    """
    Run the OAuth2 InstalledAppFlow (or load cached token) and return ImapCredentials.
    Falls back to None on any failure.
    """
    try:
        creds = get_credentials(client_secrets_path)
        email = get_email(creds)
        if not email:
            return None
        return ImapCredentials(email=email, access_token=creds.token)
    except Exception as exc:
        logger.warning("client_secrets OAuth2 failed: %s", exc)
        return None


def revoke_credentials() -> None:
    """Delete stored credentials (sign out)."""
    _delete_token()
