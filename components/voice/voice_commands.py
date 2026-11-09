"""Small, explicit voice controls; ordinary questions remain LLM requests."""

import json
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path


CONTROL_STATE = Path("/userdata/voice-assistant/voice-control-state.json")
MODEL_NAMES = {"qwen2-0.5b": "千问", "deepseek-r1-1.5b": "深度求索"}


@dataclass(frozen=True)
class Command:
    kind: str
    value: object = None


def number(text):
    text = text.replace("两", "二").replace("〇", "零")
    if text.startswith("负"):
        return -number(text[1:])
    digits = {char: str(index) for index, char in enumerate("零一二三四五六七八九")}
    if "点" in text:
        whole, fraction = text.split("点", 1)
        return float(str(number(whole)) + "." + "".join(digits.get(c, c) for c in fraction))
    if text in ("百", "一百"):
        return 100
    if "百" in text:
        first, rest = text.split("百", 1)
        return int(number(first)) * 100 + (number(rest.lstrip("零")) if rest.strip("零") else 0)
    if "十" in text:
        tens, units = text.split("十", 1)
        return (int(number(tens)) if tens else 1) * 10 + (int(number(units)) if units else 0)
    return float("".join(digits.get(c, c) for c in text)) if "." in text else int(
        "".join(digits.get(c, c) for c in text))


def parse_command(text):
    text = unicodedata.normalize("NFKC", text).casefold().strip().rstrip("。.!?！？,，;； ")
    text = "".join(char for char in text if not char.isspace()
                   and (not unicodedata.category(char).startswith("P") or char in ".%-"))
    if text in {"结束对话", "退出对话", "退出助手", "退出应用", "返回桌面", "关闭助手"}:
        return Command("exit")
    if text in {"控制帮助", "语音帮助"}:
        return Command("help")
    if text in {"静音", "关闭声音"}:
        return Command("mute")
    if text in {"取消静音", "恢复声音", "恢复音量"}:
        return Command("unmute")
    if text in {"音量大一点", "声音大一点", "大声一点", "调高音量"}:
        return Command("volume_step", 10)
    if text in {"音量小一点", "声音小一点", "小声一点", "调低音量"}:
        return Command("volume_step", -10)
    if text in {"语速快一点", "说快一点", "加快语速"}:
        return Command("speed_step", 0.1)
    if text in {"语速慢一点", "说慢一点", "放慢语速"}:
        return Command("speed_step", -0.1)
    if text in {"换个音色", "换一个音色", "下一个音色", "切换音色"}:
        return Command("speaker_next")
    model = re.fullmatch(r"(?:切换模型到|切换到|切换模型为|使用|换成|切换)(.+?)(?:模型)?", text)
    if model:
        aliases = {"千问": "qwen2-0.5b", "通义千问": "qwen2-0.5b", "qwen": "qwen2-0.5b",
                   "qwen2": "qwen2-0.5b", "快速模型": "qwen2-0.5b",
                   "deepseek": "deepseek-r1-1.5b", "深度求索": "deepseek-r1-1.5b"}
        if model[1] in aliases:
            return Command("model", aliases[model[1]])
    value = r"([负+-]?[0-9零〇一二两三四五六七八九十百点.]+)"
    patterns = (
        ("volume", r"(?:把)?音量(?:调到|调为|设为|设置为|改成)?(?:百分之)?" + value + r"%?"),
        ("speaker", r"(?:切换|把|设置)?音色(?:调到|设为|设置为|改成|切换到)?" + value + r"号?"),
        ("speaker", r"(?:使用|切换到)" + value + r"号音色"),
        ("speed", r"(?:把)?语速(?:调到|设为|设置为|改成)?" + value + r"(?:倍|倍速)?"),
    )
    for kind, pattern in patterns:
        match = re.fullmatch(pattern, text)
        if match:
            try:
                value = number(match[1])
            except (ValueError, OverflowError):
                return Command("invalid", "没有听清设置数值，请重说。")
            valid = ((kind == "volume" and 0 <= value <= 100 and int(value) == value)
                     or (kind == "speaker" and 0 <= value <= 4 and int(value) == value)
                     or (kind == "speed" and 0.5 <= value <= 2.0))
            if not valid:
                limits = {"volume": "音量范围是零到一百。", "speaker": "音色编号是零到四。",
                          "speed": "语速范围是零点五到两倍。"}
                return Command("invalid", limits[kind])
            return Command(kind, float(value) if kind == "speed" else int(value))
    return None


def publish_state(args, feedback="", exit_requested=False, path=None):
    """Atomic snapshots for the native UI; separate from the Flask PID state."""
    path = Path(path) if path is not None else CONTROL_STATE
    revision = getattr(args, "control_revision", 0) + 1
    state = {"pid": os.getpid(), "revision": revision, "model": args.model,
             "speaker": args.speaker, "speed": args.speed,
             "exit_requested": exit_requested, "feedback": feedback}
    descriptor, temporary = tempfile.mkstemp(prefix=".voice-control-", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False)
        os.replace(temporary, path)
        args.control_revision = revision
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return state
