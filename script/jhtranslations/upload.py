#!/usr/bin/env python3
"""Merge all translation sources into a single JSON file."""
import json
import os
import pathlib
import re

from .const import INTEGRATIONS_DIR, TRANSLATIONS_DIR

FILENAME_FORMAT = re.compile(r"strings\.(?P<suffix>\w+)\.json")
LOCAL_FILE = pathlib.Path("build/translations-upload.json").absolute()
CONTAINER_FILE = "/opt/src/build/translations-upload.json"
LANG_ISO = "en"


def generate_upload_data():
    """Generate the data for uploading."""
    translations = json.loads((INTEGRATIONS_DIR.parent / "strings.json").read_text())
    translations["component"] = {}

    for path in INTEGRATIONS_DIR.glob(f"*{os.sep}strings*.json"):
        component = path.parent.name
        match = FILENAME_FORMAT.search(path.name)
        platform = match.group("suffix") if match else None

        parent = translations["component"].setdefault(component, {})

        if platform:
            platforms = parent.setdefault("platform", {})
            parent = platforms.setdefault(platform, {})

        parent.update(json.loads(path.read_text()))

    return translations


def run():
    """Run the script."""
    translations = generate_upload_data()

    TRANSLATIONS_DIR.mkdir(parents=True, exist_ok=True)
    TRANSLATIONS_DIR.joinpath("en.json").write_text(
        json.dumps(translations, indent=4, sort_keys=True)
    )

    return 0
