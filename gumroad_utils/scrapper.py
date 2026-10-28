import json
import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import humanize
from bs4 import BeautifulSoup
from dateutil.parser import parse as parse_date
from pathlib3x import Path
from rich.progress import Progress as RichProgress

from .session import FilesCache, GumroadSession

__all__ = ["GumroadScrapper", "LibraryItem"]


@dataclass(frozen=True)
class LibraryItem:
    product: str
    creator: str
    creator_username: str
    variant: str
    kind: str
    archived: bool
    url: str

    @classmethod
    def from_result(cls, result: dict) -> "LibraryItem":
        product = result["product"]
        purchase = result["purchase"]
        # NOTE(PxINKY) Seems on very very rare occasions the profile can be missing the 'creator' object
        creator = product.get("creator") or {}
        # Gumroad added "?recommended_by=library" to the end of 'profile_url'
        match = re.search(r"https://(.*)\.gumroad\.com", creator.get("profile_url", ""))
        return cls(
            product=product["name"],
            creator=creator.get("name", "unknown creator"),
            creator_username=match.group(1) if match else "none",
            variant=purchase.get("variants") or "",
            kind=product.get("native_type") or "",
            archived=bool(purchase.get("is_archived")),
            url=purchase["download_url"],
        )


def _file_ids(items: list[dict]) -> set[str]:
    ids = set()
    for item in items:
        if item["type"] == "folder":
            ids |= _file_ids(item["children"])
        elif item["type"] == "file" and item["download_url"] is not None:
            ids.add(item["id"])
    return ids


def _load_page_props(soup: BeautifulSoup) -> tuple[str, dict]:
    app = soup.find(id="app")
    if app is None or not app.get("data-page"):
        raise RuntimeError(
            "Could not find Gumroad page data. The page structure may have changed."
        )

    page = json.loads(app["data-page"])
    if page["component"] == "Logins/New":
        raise RuntimeError(
            "Gumroad redirected to the login page. Your cookies have expired; run `gumroad-utils --setup`."
        )
    return page["component"], page["props"]


def _expect_page(soup: BeautifulSoup, component: str) -> dict:
    actual, props = _load_page_props(soup)
    if actual != component:
        raise RuntimeError(f"Expected Gumroad {component!r} page, got {actual!r}.")
    return props


# https://www.xormedia.com/string-truncate-middle-with-ellipsis/
def shorten(s: str, n: int = 40) -> str:
    if len(s) <= n:
        return s

    n_2 = int(n) / 2 - 3
    n_1 = n - n_2 - 3

    return "{0}..{1}".format(s[: int(n_1)], s[-int(n_2) :])


class GumroadScrapper:
    def __init__(
        self,
        session: GumroadSession,
        files_cache: FilesCache,
        root_folder: Path,
        product_folder_tmpl: str,
        slash_replacement: str,
        zip_archives: bool = False,
    ) -> None:
        self._session = session
        self._root_folder = root_folder
        self._product_folder_tmpl = product_folder_tmpl
        self._slash_replacement = slash_replacement
        self._zip_archives = zip_archives

        self._files_cache = files_cache
        self._logger = logging.getLogger("scraper")

    # Pages - Library

    def fetch_library(self, creators: set[str] | None = None) -> list[LibraryItem]:
        items = []
        page = 1
        while True:
            soup = self._session.get_soup(
                f"{self._session.base_url}/library?page={page}"
            )
            self._detect_redirect(soup)
            props = _expect_page(soup, "Library/Index")

            for result in props["results"]:
                item = LibraryItem.from_result(result)
                if creators and item.creator_username not in creators:
                    self._logger.debug(
                        "Skipping %r product of %r.", item.product, item.creator
                    )
                    continue
                items.append(item)

            if page >= props["pagination"]["pages"]:
                return items
            page += 1

    # Pages - Product content

    def scrap_product_page(self, url: str) -> None:
        if url.isalnum():
            url = f"{self._session.base_url}/d/{url}"

        # Normalize URL to app.gumroad.com
        url = self._session.normalize_url(url)

        self._logger.info("Scrapping %r...", url)

        soup = self._session.get_soup(url)
        self._detect_redirect(soup)

        script = self._load_download_page(soup)
        purchase = script["purchase"]

        if (
            not script["content"]["content_items"]
            and script["content"]["bundle_products"]
        ):
            self._logger.info(
                "Skipping %r because it's a bundle!", purchase["product_name"]
            )
            return

        creator, price = self._scrap_recipe_page(purchase, script["token"])

        # NOTE(PxINKY) Gumroad filters the username (Page URL / Profile Link username) on creation/edit
        # but not the "name" (["creator"]["name"]) from having invalid characters
        # additionally filenames can also contain invalid characters
        # Simply sanitizing the filename before using it fixes this issue
        product_creator = self.sanitize_filename(creator.strip())
        product_name = self.sanitize_filename(purchase["product_name"])
        purchase_date = parse_date(purchase["created_at"]).date()

        product_folder_name = self._product_folder_tmpl.format(
            product_name=product_name,
            purchase_at=purchase_date,
            price=price,
        ).strip()
        product_folder = self._root_folder / product_creator / product_folder_name

        self._logger.info(
            "Downloading %r product of %r creator...", product_name, product_creator
        )
        zipped = (
            self._download_archives(script, product_folder, product_name)
            if self._zip_archives
            else set()
        )
        self._download_content(script, product_folder, skip=zipped)

    # Item - ZIP archives

    def _archive_targets(
        self, script: dict, product_name: str
    ) -> list[tuple[str | None, str, set[str]]]:
        content = script["content"]
        if content.get("download_all_button"):
            return [(None, product_name, _file_ids(content["content_items"]))]

        targets = []
        for page in content.get("rich_content_pages") or []:
            for node in (page.get("description") or {}).get("content") or []:
                if node.get("type") != "fileEmbedGroup":
                    continue
                ids = {
                    child["attrs"]["id"]
                    for child in node.get("content") or []
                    if child.get("type") == "fileEmbed"
                }
                if ids:
                    name = node["attrs"].get("name") or product_name
                    targets.append((node["attrs"]["uid"], name, ids))
        return targets

    def _download_archives(
        self, script: dict, product_folder: Path, product_name: str
    ) -> set[str]:
        product_id = script["purchase"]["product_id"]
        covered: set[str] = set()
        for folder_id, name, file_ids in self._archive_targets(script, product_name):
            zip_url = f"{self._session.base_url}/zip/{script['token']}"
            if folder_id:
                zip_url += f"?folder_id={folder_id}"

            ready = self._session.get(zip_url, headers={"Accept": "application/json"})
            ready.raise_for_status()
            if not ready.json().get("url"):
                self._logger.info(
                    "Gumroad hasn't built the ZIP for %r yet; downloading its files one by one.",
                    name,
                )
                continue

            zip_path = product_folder / f"{self.sanitize_filename(name)}.zip"
            if self._fancy_download_file(
                product_id,
                f"zip:{folder_id or 'all'}",
                zip_url,
                Path("/"),
                zip_path,
                transient=False,
            ):
                covered |= file_ids
        return covered

    # Item - scanner / Downloader

    def _download_content(
        self, script: dict, parent_folder: Path, skip: set[str]
    ) -> None:
        product_id = script["purchase"]["product_id"]

        def _wanted(item: dict) -> bool:
            # NOTE(PxINKY) Gumroad added a "file" type that's embedded into the page resulting in no download_url
            return (
                item["type"] == "file"
                and item["download_url"] is not None
                and item["id"] not in skip
            )

        def _traverse_tree(
            items: list[dict], tree_path: Path, parent_folder: Path
        ) -> None:
            files_count = sum(1 for item in items if _wanted(item))
            file_idx = 0

            for item in items:
                if item["type"] != "folder":
                    continue

                folder_name = item["name"]
                _traverse_tree(
                    item["children"],
                    tree_path / folder_name.strip(),
                    parent_folder / folder_name.strip(),
                )

            for item in items:
                if not _wanted(item):
                    continue

                file_id = item["id"]
                # Sanitize the file name
                # Issue arises if the file_name is something like: 'File 1.0 / 2.0'
                # Sanitizing the file_name should prevent this
                file_name = self.sanitize_filename(item["file_name"])
                file_type = item["extension"].lower()
                file_url = self._session.base_url + item["download_url"]

                file_path = (parent_folder / file_name).with_suffix("." + file_type)
                file_idx += 1

                self._fancy_download_file(
                    product_id,
                    file_id,
                    file_url,
                    tree_path,
                    file_path,
                    files_count,
                    file_idx,
                    transient=True,
                )

        _traverse_tree(script["content"]["content_items"], Path("/"), parent_folder)

    # Pages - Recipe

    def _scrap_recipe_page(self, purchase: dict, token: str) -> tuple[str, str]:
        url = f"{self._session.base_url}/purchases/{purchase['receipt_purchase_id']}/receipt"
        soup = self._session.get_soup(url)

        # NOTE: A receipt covers a whole order, so pick the line item that links to this purchase.
        for item in soup.select(".item"):
            if item.select_one(f'a[href$="/d/{token}"]') is None:
                continue

            creator = item.select_one(".footer span a")
            price_label = item.find(
                "span", class_="attribute-label", string=re.compile("price", re.I)
            )
            price = (
                "".join(
                    price_label.parent.find_all(string=True, recursive=False)
                ).strip()
                if price_label
                else ""
            )
            if creator:
                return creator.get_text(strip=True), price

        # NOTE(PxINKY) If the purchase is a gift, A recite cant be generated, fall back to the store subdomain
        self._logger.warning("Failed to extract creator and price from %s", url)
        return urlparse(purchase["product_long_url"]).hostname.split(".")[0], ""

    # (PxINKY) - File name sanitizer

    def sanitize_filename(self, filename: str) -> str:
        invalid_chars = r'<>:"/\|?*'
        # Replace invalid characters and spaces with underscores
        sanitized_filename = "".join(
            self._slash_replacement if c in invalid_chars else c for c in filename
        )
        return sanitized_filename

    # File downloader

    def _fancy_download_file(
        self,
        product_id: str,
        file_id: str,
        url: str,
        tree_path: Path,
        file_path: Path,
        files_total_count: int = 0,
        file_idx: int = 0,
        *,
        transient: bool,
    ) -> bool:
        tree_file_path = tree_path / file_path.name
        if self._files_cache.is_cached(product_id, file_id):
            self._logger.debug("'%s' is already downloaded! Skipping.", tree_file_path)
            return True

        response = self._session.get(url, stream=True)
        response.raise_for_status()

        if response.headers.get("content-type", "").startswith("text/html"):
            self._logger.warning(
                "Failed to download '%s' file, Gumroad returned a web page instead (%s).",
                tree_file_path,
                response.url,
            )
            return False

        total_size_in_bytes = int(response.headers.get("content-length", 0))
        if total_size_in_bytes == 0:
            self._logger.warning(
                "Failed to download '%s' file, received zero content length!",
                tree_file_path,
            )
            return False

        human_size = humanize.naturalsize(total_size_in_bytes)
        if transient:
            task_desc = f"[{file_idx}/{files_total_count}] Downloading '{shorten(file_path.name)}' ({human_size})..."
        else:
            task_desc = f"Downloading '{shorten(file_path.name)}' ({human_size})..."

        file_path.parent.mkdir(parents=True, exist_ok=True)

        with RichProgress(expand=True, transient=transient) as progress:
            task = progress.add_task(task_desc, total=total_size_in_bytes)

            with file_path.open("wb") as file:
                for chunk in response.iter_content(chunk_size=4096):  # 4kb
                    if chunk:  # filter out keep-alive new chunks
                        progress.advance(task, len(chunk))
                        file.write(chunk)

        self._files_cache.cache(product_id, file_id)
        self._files_cache.save()  # save after each sucsessful download
        self._logger.info("Downloaded '%s' to '%s'", tree_file_path, file_path)
        return True

    # Utils

    def _load_download_page(self, soup: BeautifulSoup) -> dict:
        component, props = _load_page_props(soup)
        if component == "UrlRedirects/DownloadPage":
            return props
        if component != "UrlRedirects/ConfirmPage":
            reason = props.get("content_unavailability_reason_code") or component
            raise RuntimeError(f"Content unavailable: {reason}")

        if not self._session.email:
            raise RuntimeError(
                "Email confirmation required by Gumroad. "
                "Run `gumroad-utils --setup` and enter the email you used to purchase products."
            )

        self._logger.info(
            "Email confirmation required, submitting confirmation form..."
        )

        confirmation_info = props["confirmation_info"]
        form_data = {
            "utf8": "✓",
            "authenticity_token": props["authenticity_token"],
            "id": confirmation_info["id"],
            "destination": confirmation_info.get("destination") or "download_page",
            "display": confirmation_info.get("display") or "",
            "email": self._session.email,
        }

        response = self._session.post(
            f"{self._session.base_url}/confirm-redirect",
            data=form_data,
            allow_redirects=True,
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.content, "lxml")
        self._detect_redirect(soup)

        component, props = _load_page_props(soup)
        if component == "UrlRedirects/ConfirmPage":
            raise RuntimeError(
                "Email confirmation failed. Make sure the email stored by `gumroad-utils --setup` matches "
                "the email used to purchase this product."
            )
        if component != "UrlRedirects/DownloadPage":
            raise RuntimeError(
                f"Could not load download page after email confirmation (got {component!r})."
            )
        return props

    def _detect_redirect(self, soup: BeautifulSoup) -> None:
        text = soup.find(text=True, recursive=False)
        if text and ("You are being" in text):  # You are being redirected.
            raise RuntimeError("You are being redirected to a login page!")
