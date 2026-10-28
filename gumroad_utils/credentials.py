import json
import os
import shutil
import subprocess
from dataclasses import astuple, dataclass, fields
from importlib.resources import files

from pathlib3x import Path

__all__ = [
    "Credentials",
    "CredentialsError",
    "env_name",
    "fnox_config_path",
    "load_credentials",
    "store_credentials",
]


class CredentialsError(RuntimeError):
    pass


@dataclass(frozen=True)
class Credentials:
    app_session: str = ""
    guid: str = ""
    user_agent: str = ""
    email: str = ""
    cf_clearance: str = ""

    @property
    def complete(self) -> bool:
        return all(astuple(self)[:3])


def env_name(field_name: str) -> str:
    return f"GUMROAD_{field_name.upper()}"


def fnox_config_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(config_home) / "gumroad-utils" / "fnox.toml"


def _ensure_fnox_config() -> Path:
    path = fnox_config_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(files(__package__).joinpath("fnox.toml").read_text())
    return path


def _fnox(*args: str, stdin: str | None = None) -> str:
    executable = shutil.which("fnox")
    if executable is None:
        raise CredentialsError(
            "fnox is not installed. Install it with `mise use -g fnox`."
        )

    result = subprocess.run(
        [executable, "-c", str(_ensure_fnox_config()), *args],
        input=stdin,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise CredentialsError(f"`fnox {args[0]}` failed: {result.stderr.strip()}")
    return result.stdout


def load_credentials() -> Credentials:
    names = [field.name for field in fields(Credentials)]
    values = {name: os.environ.get(env_name(name), "") for name in names}

    if not Credentials(**values).complete:
        stored = json.loads(_fnox("export", "--format", "json"))["secrets"]
        values = {
            name: values[name] or stored.get(env_name(name), "") for name in names
        }

    return Credentials(**values)


def store_credentials(credentials: Credentials) -> None:
    for field in fields(Credentials):
        value = getattr(credentials, field.name)
        if value:
            _fnox("set", env_name(field.name), stdin=value)
