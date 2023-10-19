#!/usr/bin/env python3
"""Merge all translation sources into a single JSON file."""
from __future__ import annotations

import json
import os
import pathlib
import re

from .const import INTEGRATIONS_DIR

FILENAME_FORMAT = re.compile(r"strings\.(?P<suffix>\w+)\.json")
DOWNLOAD_DIR = pathlib.Path("build/translations-download").absolute()


def save_json(filename: str, data: list | dict):
    """Save JSON data to a file.

    Returns True on success.
    """
    data = json.dumps(data, sort_keys=True, indent=4)
    with open(filename, "w", encoding="utf-8") as fdesc:
        fdesc.write(data)
        return True
    return False


def get_component_path(lang, component):
    """Get the component translation path."""
    if os.path.isdir(os.path.join("homeassistant", "components", component)):
        return os.path.join(
            "homeassistant", "components", component, "translations", f"{lang}.json"
        )
    return None


def get_platform_path(lang, component, platform):
    """Get the platform translation path."""
    return os.path.join(
        "homeassistant",
        "components",
        component,
        "translations",
        f"{platform}.{lang}.json",
    )


def get_component_translations(translations):
    """Get the component level translations."""
    translations = translations.copy()
    translations.pop("platform", None)

    return translations


def save_language_translations(lang, translations):
    """Distribute the translations for this language."""
    components = translations.get("component", {})
    for component, component_translations in components.items():
        base_translations = get_component_translations(component_translations)
        if base_translations:
            if (path := get_component_path(lang, component)) is None:
                print(
                    f"Skipping {lang} for {component}, as the integration doesn't seem to exist."
                )
                continue
            os.makedirs(os.path.dirname(path), exist_ok=True)
            save_json(path, base_translations)

        if "platform" not in component_translations:
            continue

        for platform, platform_translations in component_translations[
            "platform"
        ].items():
            path = get_platform_path(lang, component, platform)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            save_json(path, platform_translations)


def write_integration_translations():
    """Write integration translations."""
    for lang_file in DOWNLOAD_DIR.glob("*.json"):
        lang = lang_file.stem
        translations = json.loads(lang_file.read_text())
        save_language_translations(lang, translations)


def delete_old_translations():
    """Delete old translations."""
    for fil in INTEGRATIONS_DIR.glob("*/translations/*"):
        fil.unlink()


def run():
    """Run the script."""
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    delete_old_translations()

    write_integration_translations()

    return 0
