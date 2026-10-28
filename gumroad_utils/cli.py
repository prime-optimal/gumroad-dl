from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser

from pathlib3x import Path

__all__ = ["get_cli_arg_parser"]


def _is_valid_path(file_path: str) -> Path:
    path = Path(file_path)
    if path.exists():
        return path
    else:
        raise FileNotFoundError(file_path)


def _to_path(file_path: str) -> Path:
    return Path(file_path)


def get_cli_arg_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description="A simple downloader for gumroad.com products",
        formatter_class=ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "link",
        nargs="*",
        help="Product links (or /d/ tokens) to download. Without links, opens an interactive library picker.",
    )
    parser.add_argument(
        "-l",
        "--links",
        type=_is_valid_path,
        help="A file with product links, one per line.",
    )
    parser.add_argument(
        "-k",
        "--creator",
        nargs="*",
        help="Only show/download library products made by these creators (profile usernames).",
        default=None,
    )
    library = parser.add_mutually_exclusive_group()
    library.add_argument(
        "--all",
        help="Download the whole library (respecting --creator) without the picker.",
        action="store_true",
    )
    library.add_argument(
        "--list",
        help="Print the library as a table and exit.",
        action="store_true",
    )
    parser.add_argument(
        "--zip",
        help="Prefer Gumroad's 'Download as ZIP' archives when they are ready.",
        action="store_true",
    )

    parser.add_argument(
        "--setup",
        help="Interactively store your Gumroad cookies in the OS keychain via fnox, then exit.",
        action="store_true",
    )
    parser.add_argument(
        "--debug",
        help="Enable debug mode.",
        action="store_true",
    )
    parser.add_argument(
        "--folder-template",
        help="Product folder name template (str.format syntax, see README).",
        default="{purchase_at:%Y.%m} {product_name} ({price})",
    )
    parser.add_argument(
        "--slash-replacement",
        help="Replacement for '/' characters in folder names.",
        default="-",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=_to_path,
        help="An output directory (default: current directory).",
        default=Path.cwd(),
    )

    return parser
