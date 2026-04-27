"""Jotter application entry point."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gio

from jotter import APP_ID

logger = logging.getLogger(__name__)

_DB_PATH = Path(GLib.get_user_data_dir()) / "jotter" / "cache.db"
# Fallback only — not needed when a GNOME Online Account is configured
_CLIENT_SECRETS = Path(GLib.get_user_config_dir()) / "jotter" / "client_secrets.json"


class JotterApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self._db = None
        self._sync_engine = None
        self._goa_account = None  # holds the live GOA object so it isn't GC'd
        self.connect("activate", self._on_activate)
        self.connect("shutdown", self._on_shutdown)

    def _on_activate(self, app: "JotterApp") -> None:
        from .audit_log import AuditLog
        from .backup import start_auto_backup
        from .models import Database
        from .window import MainWindow

        self._db = Database(_DB_PATH)
        audit_log = AuditLog(_DB_PATH.parent / "audit.log")
        start_auto_backup(_DB_PATH)
        # Purge notes that have been in trash for more than 30 days
        self._db.purge_old_deleted_notes(30)
        sync_engine, source = self._build_sync_engine(audit_log)
        self._sync_engine = sync_engine

        self._window = MainWindow(app, self._db, sync_engine, auth_source=source,
                                  audit_log=audit_log)
        self._window.present()

        if sync_engine:
            sync_engine.start()
            sync_engine.request_sync()

    # ------------------------------------------------------------------
    # Credential resolution: GOA → client_secrets.json → offline
    # ------------------------------------------------------------------

    def _build_sync_engine(self, audit_log=None):
        """
        Return (ImapSyncEngine | None, source_label).
        source_label is one of: 'goa', 'client_secrets', 'none'.
        """
        from .imap_backend import ImapSyncEngine

        def _noop_event_cb(event):
            pass  # replaced by MainWindow.__init__

        # 1. GNOME Online Accounts (preferred — no client_secrets.json needed)
        goa_creds_cb = self._make_goa_creds_cb()
        if goa_creds_cb is not None:
            logger.info("Using GNOME Online Accounts for Gmail sync")
            engine = ImapSyncEngine(self._db, goa_creds_cb, _noop_event_cb, audit_log=audit_log)
            return engine, "goa"

        # 2. Fallback: client_secrets.json + SecretService token cache
        if _CLIENT_SECRETS.exists():
            logger.info("Using client_secrets.json OAuth2 flow for Gmail sync")
            engine = ImapSyncEngine(self._db, self._make_client_secrets_creds_cb(),
                                    _noop_event_cb, audit_log=audit_log)
            return engine, "client_secrets"

        # 3. Offline — no credentials at all
        logger.info(
            "No Google credentials available. "
            "Add a Google account in GNOME Settings → Online Accounts to enable sync."
        )
        return None, "none"

    def _make_goa_creds_cb(self):
        """
        Return a credentials callback that fetches a fresh GOA token each call,
        or None if no suitable GOA Google account exists.
        """
        from .auth import list_goa_google_accounts, get_goa_credentials

        accounts = list_goa_google_accounts()
        if not accounts:
            return None

        # Use the first Google account with mail enabled.
        # Keep a reference so the GObject isn't freed.
        self._goa_account = accounts[0]
        goa_obj = self._goa_account

        def get_creds():
            return get_goa_credentials(goa_obj)

        return get_creds

    def _make_client_secrets_creds_cb(self):
        def get_creds():
            from .auth import get_imap_credentials_from_client_secrets
            return get_imap_credentials_from_client_secrets(str(_CLIENT_SECRETS))

        return get_creds

    def _on_shutdown(self, app) -> None:
        if self._sync_engine:
            self._sync_engine.stop()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    app = JotterApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
