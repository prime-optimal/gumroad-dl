from typing import ClassVar

from rich.console import Console
from rich.table import Table
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header, Input

from .scrapper import LibraryItem

__all__ = ["LibraryPicker", "print_library"]

_COLUMNS = ("", "Creator", "Product", "Variant", "Type", "Archived")
_WIDTHS = {"": 1, "Product": 60, "Variant": 30}


def _row(item: LibraryItem, selected: bool) -> tuple[str, ...]:
    return (
        "✓" if selected else "",
        item.creator,
        item.product,
        item.variant,
        item.kind,
        "yes" if item.archived else "",
    )


def print_library(items: list[LibraryItem], console: Console | None = None) -> None:
    table = Table("#", *_COLUMNS[1:], "Link", title=f"Gumroad library ({len(items)})")
    for idx, item in enumerate(items, 1):
        table.add_row(str(idx), *_row(item, False)[1:], item.url)
    (console or Console()).print(table)


class LibraryPicker(App[list[LibraryItem]]):
    TITLE = "Gumroad library"
    BINDINGS: ClassVar = [
        Binding("space", "toggle_row", "Select"),
        Binding("a", "toggle_all", "Select all shown"),
        Binding("slash", "filter", "Filter"),
        Binding("enter", "submit", "Download", priority=True),
        Binding("escape", "leave", "Back / quit", priority=True),
        Binding("q", "quit_picker", "Quit"),
    ]

    def __init__(self, items: list[LibraryItem]) -> None:
        super().__init__()
        self._items = items
        self._selected: set[str] = set()
        self._shown: list[LibraryItem] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(placeholder="Filter by product, variant or creator", id="filter")
        yield DataTable(id="library", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        for label in _COLUMNS:
            table.add_column(label, key=label or "mark", width=_WIDTHS.get(label))
        self._refresh_rows("")
        table.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._refresh_rows(event.value)

    def _refresh_rows(self, query: str) -> None:
        query = query.casefold()
        self._shown = [
            item
            for item in self._items
            if query in f"{item.product} {item.variant} {item.creator}".casefold()
        ]
        table = self.query_one(DataTable)
        table.clear()
        for item in self._shown:
            table.add_row(*_row(item, item.url in self._selected), key=item.url)
        self._update_subtitle()

    def _update_subtitle(self) -> None:
        self.sub_title = f"{len(self._selected)} selected · {len(self._shown)}/{len(self._items)} shown"

    def _set_selected(self, item: LibraryItem, selected: bool) -> None:
        if selected:
            self._selected.add(item.url)
        else:
            self._selected.discard(item.url)
        self.query_one(DataTable).update_cell(item.url, "mark", "✓" if selected else "")

    def _highlighted(self) -> LibraryItem | None:
        table = self.query_one(DataTable)
        if not self._shown:
            return None
        return self._shown[table.cursor_row]

    def action_toggle_row(self) -> None:
        item = self._highlighted()
        if item:
            self._set_selected(item, item.url not in self._selected)
            self._update_subtitle()

    def action_toggle_all(self) -> None:
        select = not all(item.url in self._selected for item in self._shown)
        for item in self._shown:
            self._set_selected(item, select)
        self._update_subtitle()

    def action_filter(self) -> None:
        self.query_one(Input).focus()

    def action_leave(self) -> None:
        if isinstance(self.focused, Input):
            self.query_one(DataTable).focus()
        else:
            self.exit([])

    def action_quit_picker(self) -> None:
        self.exit([])

    def action_submit(self) -> None:
        if isinstance(self.focused, Input):
            self.query_one(DataTable).focus()
            return
        if not self._selected:
            item = self._highlighted()
            if item is None:
                return
            self._selected.add(item.url)
        self.exit([item for item in self._items if item.url in self._selected])
