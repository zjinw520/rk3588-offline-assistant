"""Explicit, offline model configuration; no shell evaluation or downloads."""

import json
import math
from dataclasses import dataclass
from pathlib import Path


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Model:
    name: str
    path: Path
    tokenizer: Path
    context: int = 1024
    max_new_tokens: int = 256
    temperature: float = 0.7
    system: str = "你是本地中文助手。请准确、简洁地回答问题。"


@dataclass(frozen=True)
class Settings:
    runtime: Path
    bridge: Path
    models: dict


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ConfigError("{} must be an integer in {}..{}".format(name, low, high))
    return value


def _path(value, folder, name):
    if not isinstance(value, str) or not value or "\0" in value:
        raise ConfigError("Invalid " + name)
    path = Path(value).expanduser()
    return (folder / path).resolve() if not path.is_absolute() else path.resolve()


def load_settings(filename, check_files=True):
    filename = Path(filename).resolve()
    value = json.loads(filename.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or type(value.get("format")) is not int or value.get("format") != 1:
        raise ConfigError("Expected configuration format 1")
    folder = filename.parent
    runtime = _path(value.get("runtime"), folder, "runtime")
    bridge = _path(value.get("bridge"), folder, "bridge")
    records = value.get("models")
    if not isinstance(records, list) or not 1 <= len(records) <= 16:
        raise ConfigError("models must contain 1..16 entries")
    models = {}
    for item in records:
        if not isinstance(item, dict):
            raise ConfigError("Invalid model entry")
        name = item.get("name")
        if (not isinstance(name, str) or not 1 <= len(name) <= 80
                or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_.:" for c in name)
                or name in models):
            raise ConfigError("Invalid or duplicate model name")
        context = _integer(item.get("context", 1024), "context", 128, 16384)
        maximum = _integer(item.get("max_new_tokens", 256), "max_new_tokens", 1, context - 32)
        temperature = item.get("temperature", 0.7)
        if (type(temperature) not in (int, float) or not math.isfinite(temperature)
                or not 0 <= temperature <= 2):
            raise ConfigError("Invalid temperature")
        system = item.get("system", "你是本地中文助手。请准确、简洁地回答问题。")
        if not isinstance(system, str) or len(system) > 4096:
            raise ConfigError("Invalid system prompt")
        model = Model(name, _path(item.get("path"), folder, "model path"),
                      _path(item.get("tokenizer"), folder, "tokenizer"),
                      context, maximum, float(temperature), system)
        if check_files:
            if not model.path.is_file() or model.path.stat().st_size == 0:
                raise ConfigError("Missing or empty model: " + str(model.path))
            if not model.tokenizer.is_dir() or not (model.tokenizer / "tokenizer_config.json").is_file():
                raise ConfigError("Missing local tokenizer: " + str(model.tokenizer))
        models[name] = model
    return Settings(runtime, bridge, models)
