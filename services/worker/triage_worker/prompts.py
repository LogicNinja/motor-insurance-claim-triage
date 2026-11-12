"""Prompts are versioned artefacts, not strings buried in code."""
import hashlib
import pathlib

import yaml

_DIR = pathlib.Path(__file__).with_name("prompts")


def load_prompt(name: str, version: str) -> dict:
    path = _DIR / f"{name}_{version}.yaml"
    raw = path.read_bytes()
    data = yaml.safe_load(raw.decode("utf-8"))
    if not isinstance(data, dict) or data.get("version") != version or not isinstance(data.get("system"), str):
        raise ValueError(f"Prompt {name}:{version} has invalid or mismatched metadata")
    data["id"] = f"{name}:{version}"
    data["sha256"] = hashlib.sha256(raw).hexdigest()
    return data
