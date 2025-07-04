"""Helper to gather system info."""

from __future__ import annotations

from functools import cache
from getpass import getuser
import logging
import platform
from typing import TYPE_CHECKING, Any

from homeassistant.const import __version__ as current_version
from homeassistant.core import HomeAssistant
from homeassistant.loader import bind_hass
from homeassistant.util.package import is_docker_env, is_virtual_env
from homeassistant.util.system_info import is_official_image

from .hassio import is_hassio
from .importlib import async_import_module
from .singleton import singleton

_LOGGER = logging.getLogger(__name__)

_DATA_MAC_VER = "system_info_mac_ver"
_DATA_CONTAINER_ARCH = "system_info_container_arch"


@singleton(_DATA_MAC_VER)
async def async_get_mac_ver(hass: HomeAssistant) -> str:
    """Return the macOS version."""
    return (await hass.async_add_executor_job(platform.mac_ver))[0]


@singleton(_DATA_CONTAINER_ARCH)
async def async_get_container_arch(hass: HomeAssistant) -> str:
    """Return the container architecture."""

    def _read_arch_file() -> str:
        """Read the architecture from /etc/apk/arch."""
        with open("/etc/apk/arch", encoding="utf-8") as arch_file:
            return arch_file.read().strip()

    try:
        raw_arch = await hass.async_add_executor_job(_read_arch_file)
    except FileNotFoundError:
        return "unknown"
    return {"x86": "i386", "x86_64": "amd64"}.get(raw_arch, raw_arch)


# Cache the result of getuser() because it can call getpwuid() which
# can do blocking I/O to look up the username in /etc/passwd.
cached_get_user = cache(getuser)


@bind_hass
async def async_get_system_info(hass: HomeAssistant) -> dict[str, Any]:
    """Return info about the system."""
    # Local import to avoid circular dependencies
    # We use the import helper because hassio
    # may not be loaded yet and we don't want to
    # do blocking I/O in the event loop to import it.
    if TYPE_CHECKING:
        from homeassistant.components import hassio  # noqa: PLC0415
    else:
        hassio = await async_import_module(hass, "homeassistant.components.hassio")

    is_hassio_ = is_hassio(hass)

    info_object = {
        "installation_type": "Unknown",
        "version": current_version,
        "dev": "dev" in current_version,
        "hassio": is_hassio_,
        "virtualenv": is_virtual_env(),
        "python_version": platform.python_version(),
        "docker": False,
        "arch": platform.machine(),
        "timezone": str(hass.config.time_zone),
        "os_name": platform.system(),
        "os_version": platform.release(),
    }

    try:
        info_object["user"] = cached_get_user()
    except (KeyError, OSError):
        # OSError on python >= 3.13, KeyError on python < 3.13
        # KeyError can be removed when 3.12 support is dropped
        # see https://docs.python.org/3/whatsnew/3.13.html
        info_object["user"] = None

    if platform.system() == "Darwin":
        info_object["os_version"] = await async_get_mac_ver(hass)
    elif platform.system() == "Linux":
        info_object["docker"] = is_docker_env()

    # Determine installation type on current data
    if info_object["docker"]:
        if info_object["user"] == "root" and is_official_image():
            info_object["installation_type"] = "Home Assistant Container"
            info_object["container_arch"] = await async_get_container_arch(hass)
        else:
            info_object["installation_type"] = "Unsupported Third Party Container"

    elif is_virtual_env():
        info_object["installation_type"] = "Home Assistant Core"

    # Enrich with Supervisor information
    if is_hassio_:
        if not (info := hassio.get_info(hass)):
            _LOGGER.warning("No Home Assistant Supervisor info available")
            info = {}

        host = hassio.get_host_info(hass) or {}
        info_object["supervisor"] = info.get("supervisor")
        info_object["host_os"] = host.get("operating_system")
        info_object["docker_version"] = info.get("docker")
        info_object["chassis"] = host.get("chassis")

        if info.get("hassos") is not None:
            info_object["installation_type"] = "Home Assistant OS"
        else:
            info_object["installation_type"] = "Home Assistant Supervised"

    return info_object
