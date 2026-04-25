"""Main application window — three-column layout."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable, Optional

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, GObject, Gtk

from .models import Database, Folder, Note
from .editor import EditorWidget
from .imap_backend import ImapSyncEngine, SyncEvent, SyncEventType

logger = logging.getLogger(__name__)

_DB_PATH_DEFAULT = GLib.get_user_data_dir() + "/jotter/cache.db"


class AllNotesRow(Gtk.ListBoxRow):
    """Top pseudo-folder row that shows notes from every folder."""

    def __init__(self):
        super().__init__()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(8)
        box.set_margin_bottom(8)

        icon = Gtk.Image.new_from_icon_name("view-list-symbolic")
        icon.set_pixel_size(16)

        label = Gtk.Label(label="All Notes")
        label.set_halign(Gtk.Align.START)
        label.set_hexpand(True)

        box.append(icon)
        box.append(label)
        self.set_child(box)


class FolderRow(Gtk.ListBoxRow):
    """Sidebar row for a single folder."""

    def __init__(
        self,
        folder: Folder,
        on_note_dropped: Optional[Callable] = None,
        on_delete: Optional[Callable] = None,
        depth: int = 0,
    ):
        super().__init__()
        self.folder = folder
        self._on_note_dropped_cb = on_note_dropped
        self._on_delete_cb = on_delete
        self._hovering = False

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        # Indent sub-folders: 12px base + 16px per depth level
        box.set_margin_start(12 + depth * 16)
        box.set_margin_end(4)
        box.set_margin_top(8)
        box.set_margin_bottom(8)

        icon_name = "folder-symbolic" if depth == 0 else "folder-open-symbolic"
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(16)

        # Show only the leaf component of the folder name
        leaf_name = folder.name.rsplit("/", 1)[-1] if "/" in folder.name else folder.name
        label = Gtk.Label(label=leaf_name)
        label.set_halign(Gtk.Align.START)
        label.set_hexpand(True)
        label.set_ellipsize(3)  # PANGO_ELLIPSIZE_END

        self._menu_btn = self._build_menu_btn()

        box.append(icon)
        box.append(label)
        box.append(self._menu_btn)
        self.set_child(box)

        if on_note_dropped:
            self._setup_drop_target()

        motion = Gtk.EventControllerMotion()
        motion.connect("enter", self._on_hover_enter)
        motion.connect("leave", self._on_hover_leave)
        self.add_controller(motion)

    def _build_menu_btn(self) -> Gtk.MenuButton:
        btn = Gtk.MenuButton()
        btn.set_icon_name("view-more-symbolic")
        btn.add_css_class("flat")
        btn.add_css_class("circular")
        btn.set_valign(Gtk.Align.CENTER)
        btn.set_visible(False)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inner.set_margin_start(4)
        inner.set_margin_end(4)
        inner.set_margin_top(4)
        inner.set_margin_bottom(4)

        del_btn = Gtk.Button(label="Delete folder")
        del_btn.add_css_class("flat")
        del_btn.set_halign(Gtk.Align.FILL)
        del_btn.connect("clicked", self._on_delete_clicked)
        inner.append(del_btn)

        popover = Gtk.Popover()
        popover.set_child(inner)
        popover.connect("closed", self._on_popover_closed)
        btn.set_popover(popover)

        return btn

    def _on_hover_enter(self, _ctrl, _x, _y) -> None:
        self._hovering = True
        self._menu_btn.set_visible(True)

    def _on_hover_leave(self, _ctrl) -> None:
        self._hovering = False
        if not self._menu_btn.get_popover().get_visible():
            self._menu_btn.set_visible(False)

    def _on_popover_closed(self, _popover) -> None:
        if not self._hovering:
            self._menu_btn.set_visible(False)

    def _on_delete_clicked(self, _btn) -> None:
        self._menu_btn.get_popover().popdown()
        if self._on_delete_cb:
            self._on_delete_cb(self.folder)

    def _setup_drop_target(self) -> None:
        target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        target.connect("drop", self._on_drop)
        target.connect("enter", self._on_drag_enter)
        target.connect("leave", self._on_drag_leave)
        self.add_controller(target)

    def _on_drag_enter(self, target, x, y) -> Gdk.DragAction:
        self.add_css_class("drop-highlight")
        return Gdk.DragAction.MOVE

    def _on_drag_leave(self, target) -> None:
        self.remove_css_class("drop-highlight")

    def _on_drop(self, target, value, x, y) -> bool:
        self.remove_css_class("drop-highlight")
        try:
            note_id = int(value)
        except (ValueError, TypeError):
            return False
        if self._on_note_dropped_cb:
            self._on_note_dropped_cb(note_id, self.folder)
        return True


class TrashRow(Gtk.ListBoxRow):
    """Sidebar pseudo-folder row for Recently Deleted notes."""

    def __init__(self):
        super().__init__()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(8)
        box.set_margin_bottom(8)

        icon = Gtk.Image.new_from_icon_name("user-trash-symbolic")
        icon.set_pixel_size(16)

        label = Gtk.Label(label="Recently Deleted")
        label.set_halign(Gtk.Align.START)
        label.set_hexpand(True)

        box.append(icon)
        box.append(label)
        self.set_child(box)
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Recently Deleted folder"])


class NoteRow(Gtk.ListBoxRow):
    """A single row in the note list."""

    def __init__(self, note: Note, folder_name: str = "", on_delete=None,
                 on_restore=None, on_purge=None):
        super().__init__()
        self.note = note
        self._folder_name = folder_name
        self._on_delete_cb = on_delete
        self._on_restore_cb = on_restore
        self._on_purge_cb = on_purge
        self._hovering = False

        # Outer row: content (hexpand) + ⋮ button
        self._outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._content.set_hexpand(True)
        self._menu_btn = self._build_menu_btn()
        self._outer.append(self._content)
        self._outer.append(self._menu_btn)
        self.set_child(self._outer)

        self._build()
        self._setup_drag()

        motion = Gtk.EventControllerMotion()
        motion.connect("enter", self._on_hover_enter)
        motion.connect("leave", self._on_hover_leave)
        self.add_controller(motion)

    def _build(self) -> None:
        while child := self._content.get_first_child():
            self._content.remove(child)

        self._content.set_margin_start(16)
        self._content.set_margin_end(0)
        self._content.set_margin_top(12)
        self._content.set_margin_bottom(12)

        subject = self.note.subject or "(no title)"
        title = Gtk.Label(label=subject)
        title.set_halign(Gtk.Align.START)
        title.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        title.add_css_class("heading")

        preview_text = self.note.preview or ""
        preview = Gtk.Label(label=preview_text)
        preview.set_halign(Gtk.Align.START)
        preview.set_ellipsize(3)
        preview.add_css_class("caption")
        preview.add_css_class("dim-label")

        self._content.append(title)
        self._content.append(preview)

        if self._folder_name:
            tag_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            tag_box.set_margin_top(2)
            icon = Gtk.Image.new_from_icon_name("folder-symbolic")
            icon.set_pixel_size(12)
            icon.add_css_class("dim-label")
            tag_label = Gtk.Label(label=self._folder_name)
            tag_label.add_css_class("caption")
            tag_label.add_css_class("dim-label")
            tag_box.append(icon)
            tag_box.append(tag_label)
            self._content.append(tag_box)

    def _build_menu_btn(self) -> Gtk.MenuButton:
        btn = Gtk.MenuButton()
        btn.set_icon_name("view-more-symbolic")
        btn.add_css_class("flat")
        btn.add_css_class("circular")
        btn.set_valign(Gtk.Align.CENTER)
        btn.set_margin_end(4)
        btn.set_visible(False)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inner.set_margin_start(4)
        inner.set_margin_end(4)
        inner.set_margin_top(4)
        inner.set_margin_bottom(4)

        if self._on_restore_cb:
            restore_btn = Gtk.Button(label="Restore")
            restore_btn.add_css_class("flat")
            restore_btn.set_halign(Gtk.Align.FILL)
            restore_btn.connect("clicked", self._on_restore_clicked)
            inner.append(restore_btn)

        if self._on_purge_cb:
            purge_btn = Gtk.Button(label="Delete Permanently")
            purge_btn.add_css_class("flat")
            purge_btn.add_css_class("destructive-action")
            purge_btn.set_halign(Gtk.Align.FILL)
            purge_btn.connect("clicked", self._on_purge_clicked)
            inner.append(purge_btn)

        if not self._on_restore_cb and not self._on_purge_cb:
            del_btn = Gtk.Button(label="Delete note")
            del_btn.add_css_class("flat")
            del_btn.set_halign(Gtk.Align.FILL)
            del_btn.connect("clicked", self._on_delete_clicked)
            inner.append(del_btn)

        popover = Gtk.Popover()
        popover.set_child(inner)
        popover.connect("closed", self._on_popover_closed)
        btn.set_popover(popover)
        return btn

    def _on_hover_enter(self, _ctrl, _x, _y) -> None:
        self._hovering = True
        self._menu_btn.set_visible(True)

    def _on_hover_leave(self, _ctrl) -> None:
        self._hovering = False
        if not self._menu_btn.get_popover().get_visible():
            self._menu_btn.set_visible(False)

    def _on_popover_closed(self, _popover) -> None:
        if not self._hovering:
            self._menu_btn.set_visible(False)

    def _on_delete_clicked(self, _btn) -> None:
        self._menu_btn.get_popover().popdown()
        if self._on_delete_cb:
            self._on_delete_cb(self.note)

    def _on_restore_clicked(self, _btn) -> None:
        self._menu_btn.get_popover().popdown()
        if self._on_restore_cb:
            self._on_restore_cb(self.note)

    def _on_purge_clicked(self, _btn) -> None:
        self._menu_btn.get_popover().popdown()
        if self._on_purge_cb:
            self._on_purge_cb(self.note)

    def _setup_drag(self) -> None:
        source = Gtk.DragSource.new()
        source.set_actions(Gdk.DragAction.MOVE)
        source.connect("prepare", self._on_drag_prepare)
        self.add_controller(source)

    def _on_drag_prepare(self, src, x, y) -> Gdk.ContentProvider:
        val = GObject.Value()
        val.init(GObject.TYPE_STRING)
        val.set_string(str(self.note.id))
        return Gdk.ContentProvider.new_for_value(val)


class MainWindow(Adw.ApplicationWindow):
    """AdwApplicationWindow hosting the three-column layout."""

    def __init__(
        self,
        app: Adw.Application,
        db: Database,
        sync_engine: Optional[ImapSyncEngine] = None,
        auth_source: str = "none",
        audit_log=None,
    ):
        super().__init__(application=app)
        self._db = db
        self._sync_engine = sync_engine
        self._auth_source = auth_source
        self._audit_log = audit_log
        self._current_folder: Optional[Folder] = None
        self._current_note: Optional[Note] = None
        self._all_notes_mode: bool = False
        self._last_action: str = "init"
        self._sort_by: str = "modified"
        self._distraction_free: bool = False
        self._trash_mode: bool = False

        # Save icon auto-hide timeout
        self._save_icon_timeout_id: Optional[int] = None

        self.set_title("Jotter")
        self.set_default_size(1100, 700)

        self._build_ui()
        self._load_folders()
        self._restore_window_state()

        if sync_engine:
            sync_engine._event_cb = self._on_sync_event

        GLib.idle_add(self._maybe_show_welcome)

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # CSS for drag-and-drop folder highlight
        css = Gtk.CssProvider()
        css.load_from_string("""
.drop-highlight {
    background-color: alpha(@accent_color, 0.15);
    border-radius: 8px;
    box-shadow: inset 0 0 0 2px alpha(@accent_color, 0.8);
}
""")
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        # Toast overlay (wraps everything for notifications)
        self._toast_overlay = Adw.ToastOverlay()
        self.set_content(self._toast_overlay)

        # Outer split: folders | right pane
        self._outer_split = Adw.OverlaySplitView()
        self._outer_split.set_min_sidebar_width(180)
        self._outer_split.set_max_sidebar_width(260)
        self._outer_split.set_sidebar_width_fraction(0.22)
        self._toast_overlay.set_child(self._outer_split)

        # ---- LEFT: folder list ----
        self._folder_list = Gtk.ListBox()
        self._folder_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._folder_list.add_css_class("navigation-sidebar")
        self._folder_selected_id = self._folder_list.connect("row-selected", self._on_folder_selected)

        folder_scroll = Gtk.ScrolledWindow()
        folder_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        folder_scroll.set_vexpand(True)
        folder_scroll.set_child(self._folder_list)

        left_toolbar = Adw.ToolbarView()
        left_header = Adw.HeaderBar()
        # Title only — no subtitle
        left_header.set_title_widget(Adw.WindowTitle(title="Jotter", subtitle=""))

        new_folder_btn = Gtk.Button.new_from_icon_name("folder-new-symbolic")
        new_folder_btn.set_tooltip_text("New folder")
        new_folder_btn.connect("clicked", self._on_new_folder)
        left_header.pack_end(new_folder_btn)

        if self._auth_source == "none":
            add_account_btn = Gtk.Button.new_from_icon_name("user-available-symbolic")
            add_account_btn.set_tooltip_text("Add Google Account in GNOME Settings")
            add_account_btn.connect("clicked", lambda _: self._open_online_accounts_settings())
            left_header.pack_start(add_account_btn)

        left_toolbar.add_top_bar(left_header)
        left_toolbar.set_content(folder_scroll)
        self._outer_split.set_sidebar(left_toolbar)

        # ---- RIGHT: inner split view (note list + editor) ----
        self._inner_split = Adw.OverlaySplitView()
        self._inner_split.set_sidebar_position(Gtk.PackType.START)
        self._inner_split.set_max_sidebar_width(320)
        self._inner_split.set_sidebar_width_fraction(0.33)

        # --- MIDDLE: note list ---
        self._note_list = Gtk.ListBox()
        self._note_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._note_list.add_css_class("boxed-list-separate")
        self._note_list.set_margin_start(8)
        self._note_list.set_margin_end(8)
        self._note_list.set_margin_top(8)
        self._note_list.set_margin_bottom(8)
        self._note_selected_id = self._note_list.connect("row-selected", self._on_note_selected)

        note_scroll = Gtk.ScrolledWindow()
        note_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        note_scroll.set_vexpand(True)
        note_scroll.set_child(self._note_list)

        self._empty_state = Adw.StatusPage()
        self._empty_state.set_icon_name("accessories-text-editor-symbolic")
        self._empty_state.set_title("No Notes")
        self._empty_state.add_css_class("compact")
        self._empty_state.set_vexpand(True)

        self._note_stack = Gtk.Stack()
        self._note_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._note_stack.set_transition_duration(150)
        self._note_stack.set_vexpand(True)
        self._note_stack.add_named(note_scroll, "list")
        self._note_stack.add_named(self._empty_state, "empty")

        mid_toolbar = Adw.ToolbarView()
        self._mid_header = Adw.HeaderBar()
        self._folder_title = Adw.WindowTitle(title="Notes", subtitle="")
        self._mid_header.set_title_widget(self._folder_title)

        self._sidebar_btn = Gtk.ToggleButton()
        self._sidebar_btn.set_icon_name("sidebar-show-symbolic")
        self._sidebar_btn.set_tooltip_text("Show folders")
        self._sidebar_btn.set_visible(False)
        self._outer_split.bind_property(
            "show-sidebar", self._sidebar_btn, "active",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE,
        )
        self._mid_header.pack_start(self._sidebar_btn)

        new_note_btn = Gtk.Button.new_from_icon_name("document-new-symbolic")
        new_note_btn.set_tooltip_text("New note (Ctrl+N)")
        new_note_btn.connect("clicked", self._on_new_note)
        self._mid_header.pack_end(new_note_btn)

        search_btn = Gtk.ToggleButton()
        search_btn.set_icon_name("system-search-symbolic")
        search_btn.set_tooltip_text("Search (Ctrl+F)")
        self._mid_header.pack_end(search_btn)

        sort_btn = Gtk.MenuButton()
        sort_btn.set_icon_name("view-sort-ascending-symbolic")
        sort_btn.set_tooltip_text("Sort notes")
        sort_btn.set_menu_model(self._build_sort_menu())
        self._mid_header.pack_end(sort_btn)

        mid_toolbar.add_top_bar(self._mid_header)

        self._search_bar = Gtk.SearchBar()
        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_hexpand(True)
        self._search_entry.connect("search-changed", self._on_search_changed)
        self._search_bar.set_child(self._search_entry)
        self._search_bar.connect_entry(self._search_entry)
        search_btn.bind_property(
            "active", self._search_bar, "search-mode-enabled",
            GObject.BindingFlags.BIDIRECTIONAL,
        )

        mid_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        mid_content.append(self._search_bar)
        mid_content.append(self._note_stack)

        mid_toolbar.set_content(mid_content)
        self._inner_split.set_sidebar(mid_toolbar)

        # --- EDITOR ---
        self._editor = EditorWidget()
        self._editor.connect("note-changed", self._on_note_changed)
        self._editor.set_editable(False)
        self._editor.set_vexpand(True)

        # Empty state shown when no note is selected
        self._editor_empty = Adw.StatusPage()
        self._editor_empty.set_icon_name("accessories-text-editor-symbolic")
        self._editor_empty.set_title("No Note Selected")
        self._editor_empty.set_description("Select a note from the list or create a new one")
        _create_btn = Gtk.Button(label="New Note")
        _create_btn.set_halign(Gtk.Align.CENTER)
        _create_btn.add_css_class("pill")
        _create_btn.add_css_class("suggested-action")
        _create_btn.connect("clicked", self._on_new_note)
        self._editor_empty.set_child(_create_btn)
        self._editor_empty.set_vexpand(True)

        self._editor_stack = Gtk.Stack()
        self._editor_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._editor_stack.set_transition_duration(150)
        self._editor_stack.set_vexpand(True)
        self._editor_stack.add_named(self._editor, "editor")
        self._editor_stack.add_named(self._editor_empty, "empty")
        self._editor_stack.set_visible_child_name("empty")

        edit_toolbar = Adw.ToolbarView()
        self._edit_header = Adw.HeaderBar()

        # Title widget: save icon + sync status box
        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title_box.set_halign(Gtk.Align.CENTER)

        self._save_icon = Gtk.Image.new_from_icon_name("clock-symbolic")
        self._save_icon.set_pixel_size(16)
        self._save_icon.add_css_class("dim-label")
        self._save_icon.set_visible(False)

        self._sync_spinner = Gtk.Spinner()
        self._sync_spinner.set_size_request(16, 16)
        self._sync_spinner.set_visible(False)

        self._sync_status_icon = Gtk.Image()
        self._sync_status_icon.set_pixel_size(16)
        self._sync_status_icon.set_visible(False)

        title_box.append(self._save_icon)
        title_box.append(self._sync_spinner)
        title_box.append(self._sync_status_icon)
        self._edit_header.set_title_widget(title_box)

        # ⋮ menu (Sync Now + Export/Import)
        menu_model = self._build_note_menu()
        menu_btn = Gtk.MenuButton()
        menu_btn.set_icon_name("view-more-symbolic")
        menu_btn.set_menu_model(menu_model)
        self._edit_header.pack_end(menu_btn)

        # Sync error indicator — hidden until an error occurs
        self._error_btn = Gtk.Button()
        self._error_btn.set_icon_name("dialog-warning-symbolic")
        self._error_btn.set_tooltip_text("Sync errors — click for details")
        self._error_btn.add_css_class("flat")
        self._error_btn.add_css_class("error")
        self._error_btn.set_visible(False)
        self._error_btn.connect("clicked", self._on_error_btn_clicked)
        self._edit_header.pack_end(self._error_btn)

        edit_toolbar.add_top_bar(self._edit_header)
        edit_toolbar.set_content(self._editor_stack)

        self._inner_split.set_content(edit_toolbar)
        self._outer_split.set_content(self._inner_split)

        # ---- Breakpoint: collapse folder sidebar at 860sp ----
        bp = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 860sp")
        )
        bp.add_setter(self._outer_split, "collapsed", True)
        bp.add_setter(self._outer_split, "show-sidebar", False)
        bp.add_setter(self._sidebar_btn, "visible", True)
        self.add_breakpoint(bp)

        # ---- Keyboard shortcuts ----
        ctrl = Gtk.ShortcutController()
        ctrl.set_scope(Gtk.ShortcutScope.MANAGED)
        ctrl.add_shortcut(Gtk.Shortcut.new(
            Gtk.ShortcutTrigger.parse_string("<Control>n"),
            Gtk.CallbackAction.new(lambda *_: self._on_new_note(None) or True),
        ))
        ctrl.add_shortcut(Gtk.Shortcut.new(
            Gtk.ShortcutTrigger.parse_string("<Control>f"),
            Gtk.CallbackAction.new(lambda *_: self._toggle_search() or True),
        ))
        ctrl.add_shortcut(Gtk.Shortcut.new(
            Gtk.ShortcutTrigger.parse_string("F11"),
            Gtk.CallbackAction.new(lambda *_: self._toggle_distraction_free() or True),
        ))
        ctrl.add_shortcut(Gtk.Shortcut.new(
            Gtk.ShortcutTrigger.parse_string("<Control>p"),
            Gtk.CallbackAction.new(lambda *_: self._show_jump_to() or True),
        ))
        self.add_controller(ctrl)

    def _build_note_menu(self):
        from gi.repository import Gio
        menu = Gio.Menu()

        export_section = Gio.Menu()
        export_section.append("Export as Markdown…", "win.export-md")
        export_section.append("Export as Plain Text…", "win.export-txt")
        export_section.append("Export as HTML…", "win.export-html")
        export_section.append("Export as PDF…", "win.export-pdf")
        menu.append_section("Export", export_section)

        import_section = Gio.Menu()
        import_section.append("Import Markdown…", "win.import-md")
        import_section.append("Import Text File…", "win.import-txt")
        import_section.append("Export Folder as Zip…", "win.export-folder-zip")
        import_section.append("Export All Notes…", "win.export-all")
        menu.append_section("Import / Batch", import_section)

        sync_section = Gio.Menu()
        sync_section.append("Sync Now", "win.sync-now")
        menu.append_section(None, sync_section)

        for name, cb in [
            ("sync-now", self._on_sync_now),
            ("export-md", self._on_export_md),
            ("export-txt", self._on_export_txt),
            ("export-html", self._on_export_html),
            ("export-pdf", self._on_export_pdf),
            ("import-md", self._on_import_md),
            ("import-txt", self._on_import_txt),
            ("export-folder-zip", self._on_export_folder_zip),
            ("export-all", self._on_export_all),
        ]:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", cb)
            self.add_action(action)

        return menu

    def _build_sort_menu(self):
        from gi.repository import Gio
        menu = Gio.Menu()
        menu.append("Date modified", "win.sort::modified")
        menu.append("Date created", "win.sort::created")
        menu.append("Title A–Z", "win.sort::title_asc")
        menu.append("Title Z–A", "win.sort::title_desc")

        sort_action = Gio.SimpleAction.new_stateful(
            "sort", GLib.VariantType.new("s"), GLib.Variant.new_string("modified")
        )
        sort_action.connect("activate", self._on_sort_changed)
        self.add_action(sort_action)
        return menu

    def _on_sort_changed(self, action, param) -> None:
        self._sort_by = param.get_string()
        action.set_state(param)
        self._db.set_meta("sort_by", self._sort_by)
        self._reload_current_notes()

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_folders(self) -> None:
        current_folder_id = self._current_folder.id if self._current_folder else None

        self._last_action = "load_folders:block"
        self._folder_list.handler_block(self._folder_selected_id)

        while row := self._folder_list.get_first_child():
            self._folder_list.remove(row)

        all_notes_row = AllNotesRow()
        self._folder_list.append(all_notes_row)

        folders = self._db.get_folders()
        if not folders:
            folder = self._db.ensure_folder("Notes", "Notes")
            folders = [folder]

        target_folder_row = None
        for folder in folders:
            depth = folder.name.count("/")
            row = FolderRow(folder, on_note_dropped=self._move_note_to_folder,
                            on_delete=self._on_delete_folder_requested, depth=depth)
            self._folder_list.append(row)
            if folder.id == current_folder_id:
                target_folder_row = row
                self._current_folder = folder

        trash_row = TrashRow()
        self._folder_list.append(trash_row)

        if self._trash_mode:
            row_to_select = trash_row
            let_signal_fire = False
        else:
            let_signal_fire = (target_folder_row is None and not self._all_notes_mode)
            row_to_select = target_folder_row or all_notes_row
        logger.debug("load_folders: current_folder_id=%s let_signal_fire=%s all_notes=%s trash=%s",
                     current_folder_id, let_signal_fire, self._all_notes_mode, self._trash_mode)

        if let_signal_fire:
            self._last_action = "load_folders:unblock+select(intentional)"
            self._folder_list.handler_unblock(self._folder_selected_id)
            self._folder_list.select_row(row_to_select)
        else:
            self._last_action = "load_folders:select+unblock(suppressed)"
            self._folder_list.select_row(row_to_select)
            self._folder_list.handler_unblock(self._folder_selected_id)
        self._last_action = "load_folders:done"

    def _load_notes(self, folder: Folder, search: str = "") -> None:
        logger.debug("_load_notes: folder=%s search=%r", folder.name, search)
        self._discard_empty_note()
        while row := self._note_list.get_first_child():
            self._note_list.remove(row)
        for note in self._db.get_notes(folder.id, search, sort_by=self._sort_by):
            self._note_list.append(NoteRow(note, on_delete=self._on_delete_note_requested))
        self._update_empty_state(search)
        self._select_first_note()

    def _load_notes_all(self, search: str = "") -> None:
        logger.debug("_load_notes_all: search=%r", search)
        self._discard_empty_note()
        while row := self._note_list.get_first_child():
            self._note_list.remove(row)
        folder_names = {f.id: f.name for f in self._db.get_folders()}
        for note in self._db.get_all_notes(search, sort_by=self._sort_by):
            self._note_list.append(NoteRow(note, folder_name=folder_names.get(note.folder_id, ""),
                                           on_delete=self._on_delete_note_requested))
        self._update_empty_state(search)
        self._select_first_note()

    def _reload_current_notes(self) -> None:
        search = self._search_entry.get_text().strip()
        if self._trash_mode:
            self._load_trash_notes()
        elif self._all_notes_mode:
            self._load_notes_all(search)
        elif self._current_folder is not None:
            self._load_notes(self._current_folder, search)

    def _load_trash_notes(self) -> None:
        logger.debug("_load_trash_notes")
        self._discard_empty_note()
        while row := self._note_list.get_first_child():
            self._note_list.remove(row)
        for note in self._db.get_deleted_notes():
            self._note_list.append(NoteRow(
                note,
                on_restore=self._on_trash_restore_note,
                on_purge=self._on_trash_purge_note,
            ))
        self._update_empty_state()
        # Show editor in read-only mode if a note is selected, otherwise show empty state
        self._editor.clear()
        self._editor.set_editable(False)
        self._current_note = None
        self._editor_stack.set_visible_child_name("empty")

    def _on_trash_restore_note(self, note: Note) -> None:
        self._db.restore_note(note.id)
        child = self._note_list.get_first_child()
        while child:
            if isinstance(child, NoteRow) and child.note.id == note.id:
                self._note_list.remove(child)
                break
            child = child.get_next_sibling()
        self._update_empty_state()
        self._show_toast(f"“{note.subject or '(no title)'}” restored")
        if self._sync_engine:
            from .imap_backend import CmdType, _Cmd
            self._sync_engine.cmd_queue.put(_Cmd(CmdType.SYNC_NOW))

    def _on_trash_purge_note(self, note: Note) -> None:
        self._db.purge_note(note.id)
        child = self._note_list.get_first_child()
        while child:
            if isinstance(child, NoteRow) and child.note.id == note.id:
                self._note_list.remove(child)
                break
            child = child.get_next_sibling()
        self._update_empty_state()
        self._show_toast(f"“{note.subject or '(no title)'}” permanently deleted")

    def _sync_note_list(self) -> None:
        """Update note list after sync, adding/removing rows without a full rebuild."""
        if self._all_notes_mode:
            new_notes = self._db.get_all_notes()
        elif self._current_folder is not None:
            new_notes = self._db.get_notes(self._current_folder.id)
        else:
            return

        new_by_id = {n.id: n for n in new_notes}

        existing: dict[int, NoteRow] = {}
        child = self._note_list.get_first_child()
        while child:
            if isinstance(child, NoteRow):
                existing[child.note.id] = child
            child = child.get_next_sibling()

        to_remove = set(existing.keys()) - new_by_id.keys()
        to_add = new_by_id.keys() - set(existing.keys())

        if self._all_notes_mode:
            folder_names = {f.id: f.name for f in self._db.get_folders()}
        else:
            folder_names = {}

        # Check which existing rows need their displayed data refreshed.
        to_update = set()
        for nid, row in existing.items():
            if nid not in new_by_id:
                continue
            note = new_by_id[nid]
            new_fn = folder_names.get(note.folder_id, "") if self._all_notes_mode else ""
            if (note.subject != row.note.subject
                    or note.body_text != row.note.body_text
                    or new_fn != row._folder_name):
                to_update.add(nid)

        if not to_remove and not to_add and not to_update:
            return

        logger.debug("_sync_note_list: remove=%s add=%s update=%s",
                     to_remove, to_add, to_update)

        self._last_action = "sync_note_list:block"
        self._note_list.handler_block(self._note_selected_id)

        for nid in to_remove:
            self._note_list.remove(existing[nid])

        for nid in to_update:
            row = existing[nid]
            row.note = new_by_id[nid]
            row._folder_name = folder_names.get(row.note.folder_id, "") if self._all_notes_mode else ""
            row._build()

        for note in reversed(new_notes):
            if note.id not in existing:
                fn = folder_names.get(note.folder_id, "") if self._all_notes_mode else ""
                self._note_list.prepend(NoteRow(note, folder_name=fn,
                                                on_delete=self._on_delete_note_requested))

        self._last_action = "sync_note_list:unblock"
        self._note_list.handler_unblock(self._note_selected_id)
        self._last_action = "sync_note_list:done"
        self._update_empty_state()

        if self._current_note and self._current_note.id not in new_by_id:
            logger.debug("_sync_note_list: current note %s deleted remotely → clearing",
                         self._current_note.id)
            self._editor.clear()
            self._current_note = None
            self._editor_stack.set_visible_child_name("empty")

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------

    def _on_folder_selected(self, list_box, row) -> None:
        logger.debug("_on_folder_selected: row=%s last_action=%s", type(row).__name__, self._last_action)
        if isinstance(row, AllNotesRow):
            if self._all_notes_mode and not self._trash_mode:
                return
            self._all_notes_mode = True
            self._trash_mode = False
            self._current_folder = None
            self._folder_title.set_title("All Notes")
            self._load_notes_all()
        elif isinstance(row, FolderRow):
            if not self._trash_mode and not self._all_notes_mode and self._current_folder and self._current_folder.id == row.folder.id:
                return
            self._all_notes_mode = False
            self._trash_mode = False
            self._current_folder = row.folder
            self._folder_title.set_title(row.folder.name)
            self._load_notes(row.folder)
        elif isinstance(row, TrashRow):
            if self._trash_mode:
                return
            self._all_notes_mode = False
            self._trash_mode = True
            self._current_folder = None
            self._folder_title.set_title("Recently Deleted")
            self._load_trash_notes()
        else:
            return
        if self._outer_split.get_collapsed():
            self._outer_split.set_show_sidebar(False)

    def _on_note_selected(self, list_box, row) -> None:
        if row is None or not isinstance(row, NoteRow):
            return
        if self._current_note is not None and self._current_note.id == row.note.id:
            return
        if not self._trash_mode:
            self._discard_empty_note()
        self._current_note = row.note
        self._editor.load_note(row.note)
        editable = not self._trash_mode
        self._editor.set_editable(editable)
        self._editor_stack.set_visible_child_name("editor")
        if self._outer_split.get_collapsed():
            self._outer_split.set_show_sidebar(False)

    def _on_note_changed(self, _widget, note: Note) -> None:
        """Called after autosave debounce — persist and optionally push."""
        self._db.save_note(note)
        self._show_save_icon()
        self._refresh_note_row(note)
        # If All Notes is active, make sure this note appears (it may not be in the list
        # if it was just created while viewing a specific folder that was then switched to All Notes)
        if self._all_notes_mode:
            self._ensure_note_in_list(note)
        if self._audit_log:
            action = "create" if not note.synced_at else "edit"
            self._audit_log.record(action, note.id, note.subject)
        if self._sync_engine:
            from .imap_backend import CmdType, _Cmd
            self._sync_engine.cmd_queue.put(_Cmd(CmdType.NOTE_SAVED, data=note.id))

    def _refresh_note_row(self, note: Note) -> None:
        row = self._note_list.get_first_child()
        while row:
            if isinstance(row, NoteRow) and row.note.id == note.id:
                row.note = note
                self._note_list.handler_block(self._note_selected_id)
                row._build()
                # Move to top if not already there (list is sorted newest-first)
                if self._note_list.get_first_child() is not row:
                    self._note_list.remove(row)
                    self._note_list.prepend(row)
                    self._note_list.select_row(row)
                self._note_list.handler_unblock(self._note_selected_id)
                break
            row = row.get_next_sibling()

    def _on_new_note(self, _btn) -> None:
        if self._trash_mode:
            return
        if self._all_notes_mode:
            folders = self._db.get_folders()
            folder = (
                next((f for f in folders if f.name == "Notes"), None)
                or (folders[0] if folders else None)
                or self._db.ensure_folder("Notes", "Notes")
            )
        elif self._current_folder is not None:
            folder = self._current_folder
        else:
            return
        now = datetime.now(timezone.utc).isoformat()
        note = Note(folder_id=folder.id, created_at=now, modified_at=now)
        self._db.save_note(note)
        new_row = NoteRow(note, on_delete=self._on_delete_note_requested)
        self._note_list.prepend(new_row)
        self._editor_stack.set_visible_child_name("editor")
        self._note_list.select_row(new_row)
        GLib.idle_add(self._editor.focus)

    def _on_new_folder(self, _btn) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="New Folder",
            body="Enter a name for the new folder:",
        )
        entry = Gtk.Entry()
        entry.set_placeholder_text("Folder name")
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("create", "Create")
        dialog.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", self._on_new_folder_response, entry)
        dialog.present()

    def _on_new_folder_response(self, dialog, response_id: str, entry: Gtk.Entry) -> None:
        if response_id == "create":
            name = entry.get_text().strip()
            if name:
                self._db.ensure_folder(name, name)
                self._load_folders()

    def _on_delete_folder_requested(self, folder: Folder) -> None:
        count = self._db.get_note_count(folder.id)
        if count > 0:
            dialog = Adw.MessageDialog(
                transient_for=self,
                heading=f"Cannot Delete \u201c{folder.name}\u201d",
                body=f"This folder contains {count} note{'s' if count != 1 else ''}. "
                     "Move or delete all notes before deleting the folder.",
            )
            dialog.add_response("ok", "OK")
            dialog.present()
            return

        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=f"Delete \u201c{folder.name}\u201d?",
            body="This folder will be permanently deleted.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_delete_folder_response, folder)
        dialog.present()

    def _on_delete_folder_response(self, _dialog, response_id: str, folder: Folder) -> None:
        if response_id != "delete":
            return
        self._db.delete_folder(folder.id)
        if self._current_folder and self._current_folder.id == folder.id:
            self._all_notes_mode = True
            self._current_folder = None
        self._load_folders()

    def _on_delete_note_requested(self, note: Note) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=f"Delete \u201c{note.subject or '(no title)'}\u201d?",
            body="This note will be moved to Recently Deleted.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_delete_note_response, note)
        dialog.present()

    def _on_delete_note_response(self, _dialog, response_id: str, note: Note) -> None:
        if response_id != "delete":
            return
        self._do_delete_note(note)

    def _do_delete_note(self, note: Note) -> None:
        if self._audit_log:
            self._audit_log.record("delete", note.id, note.subject)
        self._db.delete_note(note.id)
        child = self._note_list.get_first_child()
        while child:
            if isinstance(child, NoteRow) and child.note.id == note.id:
                self._note_list.remove(child)
                break
            child = child.get_next_sibling()
        if self._current_note and self._current_note.id == note.id:
            self._current_note = None
            self._editor.clear()
            self._editor.set_editable(False)
            self._editor_stack.set_visible_child_name("empty")
        self._update_empty_state()
        if self._sync_engine:
            from .imap_backend import CmdType, _Cmd
            self._sync_engine.cmd_queue.put(_Cmd(CmdType.SYNC_NOW))

    # ------------------------------------------------------------------
    # Save icon
    # ------------------------------------------------------------------

    def _ensure_note_in_list(self, note) -> None:
        """Add note to the list if it isn't already there (used for All Notes view)."""
        child = self._note_list.get_first_child()
        while child:
            if isinstance(child, NoteRow) and child.note.id == note.id:
                return  # already present — _refresh_note_row handled it
            child = child.get_next_sibling()
        folder_names = {f.id: f.name for f in self._db.get_folders()}
        new_row = NoteRow(note, folder_name=folder_names.get(note.folder_id, ""),
                          on_delete=self._on_delete_note_requested)
        self._note_list.handler_block(self._note_selected_id)
        self._note_list.prepend(new_row)
        self._note_list.select_row(new_row)
        self._note_list.handler_unblock(self._note_selected_id)
        self._update_empty_state()

    def _select_first_note(self) -> None:
        """Select and load the first note row, or show the empty editor state."""
        first = self._note_list.get_first_child()
        if isinstance(first, NoteRow):
            self._note_list.select_row(first)
            self._current_note = first.note
            self._editor.load_note(first.note)
            self._editor.set_editable(True)
            self._editor_stack.set_visible_child_name("editor")
        else:
            self._editor.clear()
            self._current_note = None
            self._editor_stack.set_visible_child_name("empty")

    def _discard_empty_note(self) -> None:
        """Delete a newly created note that the user never typed anything into."""
        note = self._current_note
        if note is None or note.imap_uid is not None:
            return
        # Check the live buffer first — autosave may not have fired yet
        if self._editor._note is note and self._editor.has_content:
            return
        if note.body_text.strip():
            return
        self._db.delete_note(note.id)
        child = self._note_list.get_first_child()
        while child:
            if isinstance(child, NoteRow) and child.note.id == note.id:
                self._note_list.remove(child)
                break
            child = child.get_next_sibling()

    def _update_empty_state(self, search: str = "") -> None:
        has_notes = self._note_list.get_first_child() is not None
        if has_notes:
            self._note_stack.set_visible_child_name("list")
        else:
            if self._trash_mode:
                self._empty_state.set_title("No Deleted Notes")
            else:
                self._empty_state.set_title("No Results" if search else "No Notes")
            self._note_stack.set_visible_child_name("empty")

    def _show_save_icon(self) -> None:
        self._save_icon.set_visible(True)
        if self._save_icon_timeout_id is not None:
            GLib.source_remove(self._save_icon_timeout_id)
        self._save_icon_timeout_id = GLib.timeout_add(1500, self._hide_save_icon)

    def _hide_save_icon(self) -> bool:
        self._save_icon.set_visible(False)
        self._save_icon_timeout_id = None
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------------
    # Drag-and-drop: move note to folder
    # ------------------------------------------------------------------

    def _move_note_to_folder(self, note_id: int, target_folder: Folder) -> None:
        note = self._db.get_note(note_id)
        if note is None or note.folder_id == target_folder.id:
            return

        # Reset sync state so the note is re-pushed to the new IMAP folder.
        # Setting imap_uid=None prevents _push from trying to delete the old
        # message from the wrong folder; the old message is deduplicated by
        # apple_uuid if it reappears on the next pull.
        note.folder_id = target_folder.id
        note.imap_uid = None
        note.imap_message_id = None
        note.synced_at = None
        note.modified_at = datetime.now(timezone.utc).isoformat()
        self._db.save_note(note)

        if self._all_notes_mode:
            # Update the folder tag on the row in place
            folder_names = {f.id: f.name for f in self._db.get_folders()}
            child = self._note_list.get_first_child()
            while child:
                if isinstance(child, NoteRow) and child.note.id == note_id:
                    child.note = note
                    child._folder_name = folder_names.get(target_folder.id, "")
                    self._note_list.handler_block(self._note_selected_id)
                    child._build()
                    self._note_list.handler_unblock(self._note_selected_id)
                    break
                child = child.get_next_sibling()
        else:
            # Specific folder view: remove the note from the current list
            child = self._note_list.get_first_child()
            while child:
                if isinstance(child, NoteRow) and child.note.id == note_id:
                    self._note_list.remove(child)
                    if self._current_note and self._current_note.id == note_id:
                        self._current_note = None
                        self._editor.clear()
                        self._editor.set_editable(False)
                        self._editor_stack.set_visible_child_name("empty")
                    self._update_empty_state()
                    break
                child = child.get_next_sibling()

        self._show_toast(f"Moved to \u201c{target_folder.name}\u201d")

        if self._sync_engine:
            from .imap_backend import CmdType, _Cmd
            self._sync_engine.cmd_queue.put(_Cmd(CmdType.SYNC_NOW))

    # ------------------------------------------------------------------
    # Other handlers
    # ------------------------------------------------------------------

    def _on_error_btn_clicked(self, _btn) -> None:
        log_text = self._audit_log.format_text() if self._audit_log else "(no audit log)"

        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Sync Errors",
            body="One or more notes failed to sync with IMAP. See the log below.",
        )

        # Scrollable log view
        buf = Gtk.TextBuffer()
        buf.set_text(log_text)
        tv = Gtk.TextView(buffer=buf)
        tv.set_editable(False)
        tv.set_monospace(True)
        tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        tv.set_margin_start(8)
        tv.set_margin_end(8)
        tv.set_margin_top(8)
        tv.set_margin_bottom(8)

        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(180)
        scroll.set_min_content_width(480)
        scroll.set_child(tv)

        frame = Gtk.Frame()
        frame.set_child(scroll)
        frame.set_margin_top(8)
        dialog.set_extra_child(frame)

        dialog.add_response("close", "Close")
        dialog.add_response("copy", "Copy Log")
        dialog.set_default_response("close")
        dialog.set_close_response("close")

        def _on_response(dlg, response_id):
            if response_id == "copy":
                clipboard = Gdk.Display.get_default().get_clipboard()
                clipboard.set(log_text)
                self._show_toast("Log copied to clipboard")
            # Hide the error button if there are no remaining errors
            if self._audit_log and not self._audit_log.has_errors():
                self._error_btn.set_visible(False)

        dialog.connect("response", _on_response)
        dialog.present()

    def _on_sync_now(self, _action, _param) -> None:
        if self._sync_engine:
            self._sync_engine.request_full_sync()
            self._show_toast("Reloading from IMAP…")

    # ------------------------------------------------------------------
    # Export / Import handlers
    # ------------------------------------------------------------------

    def _on_export_md(self, _action, _param) -> None:
        if self._current_note:
            from .export import export_note_markdown
            export_note_markdown(self._current_note, self)

    def _on_export_txt(self, _action, _param) -> None:
        if self._current_note:
            from .export import export_note_text
            export_note_text(self._current_note, self)

    def _on_export_html(self, _action, _param) -> None:
        if self._current_note:
            from .export import export_note_html
            export_note_html(self._current_note, self)

    def _on_export_pdf(self, _action, _param) -> None:
        if self._current_note:
            from .export import export_note_pdf
            export_note_pdf(self._current_note, self)

    def _on_import_md(self, _action, _param) -> None:
        folder_id = self._current_folder.id if self._current_folder else self._db.get_folders()[0].id
        from .export import import_note_markdown
        import_note_markdown(self._db, folder_id, self, self._on_note_imported)

    def _on_import_txt(self, _action, _param) -> None:
        folder_id = self._current_folder.id if self._current_folder else self._db.get_folders()[0].id
        from .export import import_note_text
        import_note_text(self._db, folder_id, self, self._on_note_imported)

    def _on_export_folder_zip(self, _action, _param) -> None:
        if self._current_folder:
            from .export import export_folder_zip
            export_folder_zip(self._db, self._current_folder.id,
                              self._current_folder.name, self)

    def _on_export_all(self, _action, _param) -> None:
        from .export import export_all_zip
        export_all_zip(self._db, self)

    def _on_note_imported(self, note) -> None:
        """Called after a successful import — show the new note."""
        self._sync_note_list()
        if self._sync_engine:
            from .imap_backend import CmdType, _Cmd
            self._sync_engine.cmd_queue.put(_Cmd(CmdType.SYNC_NOW))

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        if self._trash_mode:
            return
        query = entry.get_text().strip()
        if self._all_notes_mode:
            self._load_notes_all(query)
        elif self._current_folder is not None:
            self._load_notes(self._current_folder, query)

    def _toggle_search(self) -> None:
        bar = self._search_bar
        bar.set_search_mode_enabled(not bar.get_search_mode_enabled())

    def _toggle_distraction_free(self) -> None:
        self._distraction_free = not self._distraction_free
        if self._distraction_free:
            self._outer_split.set_show_sidebar(False)
            self._inner_split.set_show_sidebar(False)
        else:
            self._outer_split.set_show_sidebar(True)
            self._inner_split.set_show_sidebar(True)

    def _show_jump_to(self) -> None:
        """Ctrl+P command palette: fuzzy-search notes by title."""
        dialog = Adw.Dialog()
        dialog.set_title("Jump to Note")
        dialog.set_content_width(480)
        dialog.set_content_height(400)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        box.append(header)

        entry = Gtk.SearchEntry()
        entry.set_placeholder_text("Search notes…")
        entry.set_margin_start(12)
        entry.set_margin_end(12)
        entry.set_margin_top(8)
        entry.set_margin_bottom(8)
        box.append(entry)

        list_box = Gtk.ListBox()
        list_box.set_selection_mode(Gtk.SelectionMode.SINGLE)
        list_box.add_css_class("boxed-list-separate")
        list_box.set_margin_start(8)
        list_box.set_margin_end(8)
        list_box.set_margin_bottom(8)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_child(list_box)
        box.append(scroll)

        dialog.set_child(box)

        all_notes = self._db.get_all_notes()

        def _populate(query: str = "") -> None:
            while child := list_box.get_first_child():
                list_box.remove(child)
            q = query.lower()
            for note in all_notes:
                if q and q not in (note.subject or "").lower() and q not in (note.body_text or "").lower():
                    continue
                row = Gtk.ListBoxRow()
                lbl = Gtk.Label(label=note.subject or "(no title)")
                lbl.set_halign(Gtk.Align.START)
                lbl.set_margin_start(12)
                lbl.set_margin_end(12)
                lbl.set_margin_top(8)
                lbl.set_margin_bottom(8)
                lbl.set_ellipsize(3)
                row._note = note
                row.set_child(lbl)
                list_box.append(row)

        _populate()
        entry.connect("search-changed", lambda e: _populate(e.get_text()))

        def _on_row_activated(lb, row) -> None:
            note = row._note
            dialog.close()
            self._navigate_to_note(note)

        list_box.connect("row-activated", _on_row_activated)
        entry.connect("activate", lambda _: (
            _on_row_activated(list_box, list_box.get_selected_row())
            if list_box.get_selected_row() else None
        ))

        dialog.present(self)
        entry.grab_focus()

    def _navigate_to_note(self, note) -> None:
        """Select a note in the list, switching folder if needed."""
        folder = self._db.get_folders()
        folder_map = {f.id: f for f in folder}
        target_folder = folder_map.get(note.folder_id)
        if target_folder is None:
            return

        # Switch to the note's folder
        self._all_notes_mode = False
        self._current_folder = target_folder
        self._folder_title.set_title(target_folder.name)

        # Select the folder row
        child = self._folder_list.get_first_child()
        while child:
            if isinstance(child, FolderRow) and child.folder.id == target_folder.id:
                self._folder_list.handler_block(self._folder_selected_id)
                self._folder_list.select_row(child)
                self._folder_list.handler_unblock(self._folder_selected_id)
                break
            child = child.get_next_sibling()

        self._load_notes(target_folder)
        # Select the note row
        child = self._note_list.get_first_child()
        while child:
            if isinstance(child, NoteRow) and child.note.id == note.id:
                self._note_list.select_row(child)
                break
            child = child.get_next_sibling()

    # ------------------------------------------------------------------
    # Sync events
    # ------------------------------------------------------------------

    def _on_sync_event(self, event: SyncEvent) -> None:
        if event.type == SyncEventType.SYNC_STARTED:
            self._sync_spinner.set_visible(True)
            self._sync_spinner.start()
            self._sync_status_icon.set_visible(False)

        elif event.type == SyncEventType.NOTES_UPDATED:
            logger.debug("sync event NOTES_UPDATED folder=%s", event.data)
            if self._all_notes_mode or (self._current_folder and event.data == self._current_folder.id):
                self._sync_note_list()

        elif event.type == SyncEventType.SYNC_COMPLETE:
            logger.debug("sync event SYNC_COMPLETE")
            self._sync_spinner.stop()
            self._sync_spinner.set_visible(False)
            self._sync_status_icon.set_from_icon_name("emblem-ok-symbolic")
            self._sync_status_icon.add_css_class("success")
            self._sync_status_icon.set_tooltip_text("Synced")
            self._sync_status_icon.set_visible(True)
            GLib.timeout_add(3000, self._hide_sync_status_icon)
            self._load_folders()

        elif event.type == SyncEventType.SYNC_ERROR:
            self._sync_spinner.stop()
            self._sync_spinner.set_visible(False)
            self._sync_status_icon.set_from_icon_name("dialog-warning-symbolic")
            self._sync_status_icon.remove_css_class("success")
            self._sync_status_icon.add_css_class("error")
            self._sync_status_icon.set_tooltip_text(f"Sync error: {event.error}")
            self._sync_status_icon.set_visible(True)
            self._error_btn.set_visible(True)
            self._send_desktop_notification("Jotter sync error", event.error or "Failed to sync")

        elif event.type == SyncEventType.SYNC_CONFLICT:
            data = event.data if isinstance(event.data, dict) else {}
            n = data.get("count", event.data or 1)
            notes = data.get("notes", [])
            msg = f"{n} note{'s' if n != 1 else ''} had conflicts — local edits kept"
            self._show_toast(msg)
            if self._audit_log:
                for note_id, subject in notes:
                    self._audit_log.mark_conflict(
                        note_id, subject,
                        "Remote change skipped — local edit will be pushed on next sync"
                    )
            self._error_btn.set_visible(True)

        elif event.type == SyncEventType.CONNECTED:
            self._sync_status_icon.set_from_icon_name("network-wireless-symbolic")
            self._sync_status_icon.remove_css_class("error")
            self._sync_status_icon.set_tooltip_text("Connected")

        elif event.type == SyncEventType.DISCONNECTED:
            self._sync_spinner.stop()
            self._sync_spinner.set_visible(False)
            self._sync_status_icon.set_from_icon_name("network-offline-symbolic")
            self._sync_status_icon.remove_css_class("success")
            self._sync_status_icon.set_tooltip_text("Offline")
            self._sync_status_icon.set_visible(True)

        elif event.type == SyncEventType.AUTH_REQUIRED:
            self._show_add_account_dialog()

        return GLib.SOURCE_REMOVE

    def _hide_sync_status_icon(self) -> bool:
        self._sync_status_icon.set_visible(False)
        return GLib.SOURCE_REMOVE

    def _send_desktop_notification(self, title: str, body: str) -> None:
        try:
            from gi.repository import Gio
            notification = Gio.Notification.new(title)
            notification.set_body(body)
            notification.set_priority(Gio.NotificationPriority.HIGH)
            app = self.get_application()
            if app:
                app.send_notification("jotter-sync-error", notification)
        except Exception as exc:
            logger.debug("Desktop notification failed: %s", exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _open_online_accounts_settings(self) -> None:
        try:
            from gi.repository import Gio
            Gio.AppInfo.launch_default_for_uri("settings://online-accounts", None)
        except Exception:
            import subprocess
            subprocess.Popen(["gnome-control-center", "online-accounts"])

    def _show_add_account_dialog(self) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Google Account Needed",
            body=(
                "To sync notes with Gmail and Apple Notes, add your Google Account "
                "in GNOME Settings → Online Accounts."
            ),
        )
        dialog.add_response("cancel", "Not Now")
        dialog.add_response("open", "Open Settings")
        dialog.set_response_appearance("open", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", lambda d, r: self._open_online_accounts_settings() if r == "open" else None)
        dialog.present()

    def _maybe_show_welcome(self) -> bool:
        if not self._db.get_meta("first_launch_done"):
            self._show_welcome_dialog()
        return GLib.SOURCE_REMOVE

    def _show_welcome_dialog(self) -> None:
        dialog = Adw.Dialog()
        dialog.set_title("Welcome to Jotter")
        dialog.set_content_width(480)
        dialog.set_content_height(380)
        dialog.set_can_close(False)

        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        toolbar_view.add_top_bar(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        box.set_margin_start(32)
        box.set_margin_end(32)
        box.set_margin_top(24)
        box.set_margin_bottom(32)
        box.set_vexpand(True)

        status = Adw.StatusPage()
        status.set_icon_name("accessories-text-editor-symbolic")
        status.set_title("Welcome to Jotter")
        status.set_description(
            "Jotter keeps your notes in sync with Apple Notes via Gmail IMAP.\n"
            "Add your Google account in GNOME Settings to enable sync, "
            "or start writing notes offline."
        )
        status.set_vexpand(True)
        box.append(status)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        btn_box.set_halign(Gtk.Align.CENTER)

        if self._auth_source == "none":
            setup_btn = Gtk.Button(label="Add Google Account…")
            setup_btn.add_css_class("pill")
            setup_btn.add_css_class("suggested-action")
            setup_btn.connect("clicked", lambda _: (
                self._open_online_accounts_settings(),
                self._db.set_meta("first_launch_done", "1"),
                dialog.close(),
            ))
            btn_box.append(setup_btn)

        offline_btn = Gtk.Button(label="Continue Offline")
        offline_btn.add_css_class("pill")
        if self._auth_source != "none":
            offline_btn.add_css_class("suggested-action")
        offline_btn.connect("clicked", lambda _: (
            self._db.set_meta("first_launch_done", "1"),
            dialog.close(),
        ))
        btn_box.append(offline_btn)

        box.append(btn_box)
        toolbar_view.set_content(box)
        dialog.set_child(toolbar_view)
        dialog.present(self)

    def _show_toast(self, message: str) -> None:
        toast = Adw.Toast(title=message)
        toast.set_timeout(3)
        self._toast_overlay.add_toast(toast)

    def _restore_window_state(self) -> None:
        w = self._db.get_meta("window_width")
        h = self._db.get_meta("window_height")
        if w and h:
            try:
                self.set_default_size(int(w), int(h))
            except ValueError:
                pass
        saved_sort = self._db.get_meta("sort_by")
        if saved_sort in ("modified", "created", "title_asc", "title_desc"):
            self._sort_by = saved_sort

    def _save_window_state(self) -> None:
        alloc = self.get_default_size()
        self._db.set_meta("window_width", str(alloc[0]))
        self._db.set_meta("window_height", str(alloc[1]))

    def do_close_request(self) -> bool:
        self._save_window_state()
        if self._sync_engine:
            self._sync_engine.stop()
        return False  # allow close
