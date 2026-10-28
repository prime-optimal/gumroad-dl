import json
import logging
from collections import defaultdict

from bs4 import BeautifulSoup
from pathlib3x import Path
from requests import Session as _RequestsSession

__all__ = ["FilesCache", "GumroadSession"]


def _sanitize_cookie_value(value: str) -> str:
    return value.replace("+", "%2B").replace("/", "%2F").replace("=", "%3D")


class GumroadSession(_RequestsSession):
    def __init__(
        self,
        app_session: str,
        guid: str,
        user_agent: str,
        email: str = "",
        cf_clearance: str = "",
    ) -> None:
        super().__init__()

        self.cookies.set("_gumroad_app_session", _sanitize_cookie_value(app_session))
        self.cookies.set("_gumroad_guid", guid)

        # Cloudflare clearance cookie - required to bypass Cloudflare protection
        if cf_clearance:
            self.cookies.set("cf_clearance", cf_clearance, domain=".gumroad.com")

        self.headers["User-Agent"] = user_agent
        self.email = email

    @property
    def base_url(self) -> str:
        return "https://app.gumroad.com"

    def normalize_url(self, url: str) -> str:
        """Normalize URLs to use app.gumroad.com instead of gumroad.com"""
        # Replace gumroad.com with app.gumroad.com (avoid Cloudflare blocks)
        if "://gumroad.com/" in url:
            url = url.replace("://gumroad.com/", "://app.gumroad.com/")
        return url

    def get_soup(self, url: str) -> BeautifulSoup:
        url = self.normalize_url(url)
        response = self.get(url, allow_redirects=True)

        # Check for Cloudflare challenge
        if (
            response.status_code == 403
            and response.headers.get("cf-mitigated") == "challenge"
        ):
            raise RuntimeError(
                "Cloudflare is blocking the request. Run `gumroad-utils --setup` and add your cf_clearance cookie.\n"
                "Copy it from DevTools > Application > Cookies > https://gumroad.com > cf_clearance."
            )

        response.raise_for_status()
        return BeautifulSoup(response.content, "lxml")


class FilesCache:
    def __init__(self, file_path: Path) -> None:
        self._file_path = file_path
        self._storage: dict[str, set] = defaultdict(set)
        self._logger = logging.getLogger("cache")
        self.load()  # Load cache on initialization

    def load(self) -> None:
        if not self._file_path.exists():
            return

        with open(self._file_path, "r", encoding="utf-8") as f:
            for k, v in json.load(f).items():
                self._storage[k] = set(v)

        self._logger.info("Cache has been loaded.")

    def save(self) -> None:
        with open(self._file_path, "w", encoding="utf-8") as f:
            json.dump(self._storage, f, default=list, indent=2)

        self._logger.info("Cache has been saved.")

    def is_cached(self, product_id: str, file_id: str) -> bool:
        return file_id in self._storage.get(product_id, [])

    def cache(self, product_id: str, file_id: str) -> None:
        self._storage[product_id].add(file_id)
