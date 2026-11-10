"""Prompts are versioned artefacts, not strings buried in code."""
import pathlib

import yaml

_DIR = pathlib.Path(__file__).with_name("prompts")


def load_prompt(name: str, version: str) -> dict:
    data = yaml.safe_load((_DIR / f"{name}_{version}.yaml").read_text(encoding="utf-8"))
    data["id"] = f"{name}:{data['version']}"
    return data
