import gc
import logging
import signal
import sys
from argparse import Namespace
from dataclasses import asdict
from typing import cast

from pathlib3x import Path
from rich.console import Console
from rich.logging import RichHandler

from .cli import get_cli_arg_parser
from .credentials import Credentials, CredentialsError, load_credentials
from .onboarding import run_setup
from .picker import LibraryPicker, print_library
from .scrapper import GumroadScrapper
from .session import FilesCache, GumroadSession


def _set_sigint_handler(files_cache: FilesCache) -> None:
    original_sigint_handler = signal.getsignal(signal.SIGINT)

    def _sigint_handler(signal, frame):
        files_cache.save()
        original_sigint_handler(signal, frame)

    signal.signal(signal.SIGINT, _sigint_handler)


def _resolve_credentials() -> Credentials:
    credentials = load_credentials()
    if credentials.complete:
        return credentials
    if not sys.stdin.isatty():
        raise CredentialsError(
            "No Gumroad credentials found. Run `gumroad-utils --setup` in a terminal, "
            "or set GUMROAD_APP_SESSION, GUMROAD_GUID and GUMROAD_USER_AGENT."
        )
    return run_setup(credentials)


def _links_from_library(scrapper: GumroadScrapper, args: Namespace) -> list[str]:
    console = Console()
    creators = set(args.creator) if args.creator else None
    with console.status("Loading your Gumroad library..."):
        items = scrapper.fetch_library(creators)

    if args.all:
        return [item.url for item in items]

    if args.list or not (sys.stdin.isatty() and sys.stdout.isatty()):
        print_library(items, console)
        if not args.list:
            console.print(
                "Not a terminal: pass links, --links FILE or --all to download."
            )
        return []

    return [item.url for item in LibraryPicker(items).run() or []]


def main() -> None:
    try:
        args = get_cli_arg_parser().parse_args()
    except FileNotFoundError as e:
        print(f"File not found: {str(e)}!")
        sys.exit(1)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="[%(name)s] %(message)s",
        datefmt="%X",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
    )
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    try:
        if args.setup:
            run_setup(load_credentials())
            return
        credentials = _resolve_credentials()
    except CredentialsError as e:
        print(e, file=sys.stderr)
        sys.exit(1)

    session = GumroadSession(**asdict(credentials))
    files_cache = FilesCache(cast("Path", args.output) / "gumroad.cache")
    scrapper = GumroadScrapper(
        session,
        files_cache,
        root_folder=args.output,
        product_folder_tmpl=args.folder_template,
        slash_replacement=args.slash_replacement,
        zip_archives=args.zip,
    )
    try:
        links = list(args.link)
        if args.links:
            lines = cast(Path, args.links).read_text(encoding="utf-8").splitlines()
            links += [line.strip() for line in lines if line.strip()]
            if not links:
                logging.getLogger().warning("File with links is empty.")
                return

        if not links:
            links = _links_from_library(scrapper, args)
            if not links:
                return

        _set_sigint_handler(files_cache)
        for link in links:
            scrapper.scrap_product_page(link)
            gc.collect()

    except Exception:
        logging.getLogger().exception("")

    files_cache.save()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
