import webbrowser

from rich.console import Console
from rich.prompt import Confirm, Prompt

from .credentials import Credentials, fnox_config_path, store_credentials

__all__ = ["run_setup"]

_LIBRARY_URL = "https://gumroad.com/library"


def _ask(
    console: Console, label: str, current: str, *, secret: bool, required: bool
) -> str:
    if current:
        hint = " [dim](Enter keeps the saved value)[/]"
    elif not required:
        hint = " [dim](optional, Enter to skip)[/]"
    else:
        hint = ""

    while True:
        value = (
            Prompt.ask(
                f"{label}{hint}", password=secret, default="", show_default=False
            )
            .strip()
            .strip('"')
        )
        if value:
            if secret:
                console.print(f"  [dim]received {len(value)} characters[/]")
            return value
        if current or not required:
            return current
        console.print("  [red]This value is required.[/]")


def run_setup(current: Credentials) -> Credentials:
    console = Console()
    console.rule("gumroad-utils setup")
    console.print(
        "gumroad-utils signs in with your browser's Gumroad cookies.\n"
        "They are stored in your OS keychain through fnox, never in a plain file.\n"
    )

    console.print(f"[bold]1.[/] Log in to Gumroad in your browser: {_LIBRARY_URL}")
    if Confirm.ask("   Open it now?", default=True):
        webbrowser.open(_LIBRARY_URL)

    console.print(
        "\n[bold]2.[/] Open DevTools ([bold]⌥⌘I[/] on macOS, [bold]F12[/] elsewhere) → [bold]Application[/] tab →"
        " Storage → [bold]Cookies[/] → [bold]https://gumroad.com[/].\n"
        "   Select a cookie and copy its value. Input is hidden while you paste.\n"
    )
    app_session = _ask(
        console, "_gumroad_app_session", current.app_session, secret=True, required=True
    )
    guid = _ask(console, "_gumroad_guid", current.guid, secret=True, required=True)
    cf_clearance = _ask(
        console,
        "cf_clearance [dim](only listed if Cloudflare challenged you)[/]",
        current.cf_clearance,
        secret=True,
        required=False,
    )

    console.print(
        "\n[bold]3.[/] In the DevTools [bold]Console[/] tab run [cyan]navigator.userAgent[/] and copy the result.\n"
        "   It must come from the same browser as the cookies.\n"
    )
    user_agent = _ask(
        console, "User agent", current.user_agent, secret=False, required=True
    )

    console.print(
        "\n[bold]4.[/] Gumroad sometimes asks you to confirm the purchase email before showing downloads.\n"
    )
    email = _ask(console, "Purchase email", current.email, secret=False, required=False)

    credentials = Credentials(
        app_session=app_session,
        guid=guid,
        user_agent=user_agent,
        email=email,
        cf_clearance=cf_clearance,
    )
    store_credentials(credentials)
    console.print(
        f"\n[green]✓[/] Saved to your OS keychain (fnox config: {fnox_config_path()})"
    )
    return credentials
