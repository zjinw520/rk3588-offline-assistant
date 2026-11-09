"""Read-only, best-effort view of the existing voice pipeline's text log.

No model, microphone, player or service is started here. Only recognised log
markers become UI state. The source is deliberately labelled ``log-derived``:
it is not a sample-accurate recording/playback event stream. In particular, a
running process alone never means that the microphone is currently listening.
"""

import hashlib
import os
import re
from pathlib import Path


MAX_LOG_BYTES = 1024 * 1024
MAX_QUESTION_CHARS = 2048
MAX_ANSWER_CHARS = 32768
MAX_NOTICE_CHARS = 600
MODEL_PATTERN = r"(?:qwen2-0\.5b|deepseek-r1-1\.5b)"
MODEL_ANSWER = re.compile(r"^(" + MODEL_PATTERN + r")[：:]\s?(.*)$", re.I)
MODEL_START = re.compile(r"^(" + MODEL_PATTERN + r")正在生成回答", re.I)
MODEL_TIME = re.compile(r"^" + MODEL_PATTERN + r"耗时[：:]", re.I)
SEGMENT = re.compile(r"^准备播报第(\d+)段[：:]\s?(.*)$")
SYNTHESIZED = re.compile(r"^第\d+段合成完成[：:]")
LOG_START = re.compile(r"^语音流水线v[\d.]+[：:]")
CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

PHASE_LABELS = {
    "stopped": "对话已停止",
    "running": "语音运行中",
    "loading": "正在准备语音模型",
    "waiting": "等待你的唤醒",
    "waking": "已唤醒，正在准备",
    "listening": "等待你提问",
    "thinking": "正在生成回答",
    "preparing": "正在准备语音",
    "answering": "正在播报回答",
    "answered": "回答已生成",
    "complete": "本轮播报完成",
    "control": "已收到本机控制指令",
    "exiting": "正在返回桌面",
    "error": "语音运行遇到问题",
}


def _clean(text, limit):
    return CONTROL_CHARACTERS.sub("", str(text)).strip()[:limit]


def _hide_reasoning(text, model=""):
    """Suppress closed, nested, partial and unclosed think sections.

    A stray closing tag in an R1 response may mean an opening tag was omitted
    by the upstream prompt. Its whole preceding prefix is discarded. A partial
    opening marker at a polled EOF is discarded rather than exposed briefly.
    """
    text = re.sub(r"&lt;\s*(/?)think\s*&gt;", r"<\1think>", text, flags=re.I)
    tokens = re.split(r"(<\s*/?\s*think\s*>)", text, flags=re.I)
    output = []
    depth = 0
    for token in tokens:
        if re.fullmatch(r"<\s*think\s*>", token, flags=re.I):
            depth += 1
        elif re.fullmatch(r"<\s*/\s*think\s*>", token, flags=re.I):
            if depth:
                depth -= 1
            elif model.lower().startswith("deepseek-r1"):
                output = []
        elif not depth:
            # Even a lone '<' at EOF could be the beginning of a hidden tag.
            token = re.sub(r"<\s*/?\s*(?:t(?:h(?:i(?:n(?:k)?)?)?)?)?\s*$", "", token,
                           flags=re.I)
            output.append(token)
    return _clean("".join(output), MAX_ANSWER_CHARS)


def _diagnostic(line):
    return (line.startswith(("/project/", "Traceback (", "  File ", "PCM连续播放器",
                             "首段进入播放耗时：", "首段正式文字到达：",
                             "控制已执行，确认语音播放失败：", "关闭播放器失败："))
            or SYNTHESIZED.match(line) is not None)


def _parse(text, running=False, pid=0, base_offset=0):
    try:
        pid = max(0, int(pid)) if not isinstance(pid, bool) else 0
    except (TypeError, ValueError, OverflowError):
        pid = 0
    result = {
        "schema_version": 1, "source": "log-derived", "running": bool(running),
        "pid": pid, "phase": "running" if running else "stopped",
        "phase_label": "", "phase_uncertain": False, "question": "", "answer": "",
        "answer_partial": False, "model": "", "turn_id": "", "session_key": "",
        "warning": "", "feedback": "", "session_started": False,
    }
    if not isinstance(text, str):
        text = ""
    # The public parser is also bounded when called directly with an oversized
    # string. A partial first line is excluded, just as in read_snapshot().
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) > MAX_LOG_BYTES:
        omitted = len(encoded) - MAX_LOG_BYTES
        tail = encoded[-MAX_LOG_BYTES:]
        newline = tail.find(b"\n")
        if newline >= 0:
            omitted += newline + 1
            tail = tail[newline + 1:]
        else:
            tail = b""
            omitted = len(encoded)
        text = tail.decode("utf-8", errors="replace")
        base_offset += omitted

    segments = {}
    answer_lines = None
    answer_chars = 0
    final_answer = None
    answer_complete = False
    has_played = False
    session_offset = base_offset
    byte_position = base_offset

    def reset_session(position):
        nonlocal segments, answer_lines, answer_chars, final_answer, answer_complete, has_played
        segments, answer_lines, final_answer = {}, None, None
        answer_chars = 0
        answer_complete = has_played = False
        for field in ("question", "answer", "model", "turn_id", "warning", "feedback"):
            result[field] = ""
        result["session_key"] = "{}:{}".format(pid, position)
        result["session_started"] = True

    def start_question(question, position):
        nonlocal segments, answer_lines, answer_chars, final_answer, answer_complete, has_played
        segments, answer_lines, final_answer = {}, None, None
        answer_chars = 0
        answer_complete = has_played = False
        result["question"] = _clean(question, MAX_QUESTION_CHARS)
        result["warning"] = result["feedback"] = ""
        digest = hashlib.sha256(result["question"].encode("utf-8")).hexdigest()[:12]
        result["turn_id"] = "{}:{}:{}".format(pid, position, digest)
        result["phase"] = "thinking"

    for original in text.splitlines(keepends=True):
        position = byte_position
        byte_position += len(original.encode("utf-8", errors="replace"))
        line = CONTROL_CHARACTERS.sub("", original.rstrip("\r\n"))
        if LOG_START.match(line):
            reset_session(position)
            session_offset = position
            result["phase"] = "loading"
            continue

        # A final model response can contain newlines or even text resembling a
        # question marker. Its known timing/footer closes this block, not an
        # arbitrary line beginning '你说'. Async audio diagnostics are omitted.
        footer = line.startswith("模型流接收完成：") or MODEL_TIME.match(line)
        if answer_lines is not None and not footer:
            if line.startswith("首段进入播放耗时："):
                has_played = True
                result["phase"] = "answering"
            if not _diagnostic(line):
                answer_lines.append(line)
                answer_chars += len(line) + 1
            # Avoid unbounded list growth even in malformed log output.
            if answer_chars > MAX_ANSWER_CHARS * 2:
                answer_lines = ["\n".join(answer_lines)[:MAX_ANSWER_CHARS * 2]]
                answer_chars = MAX_ANSWER_CHARS * 2
            continue
        if footer and answer_lines is not None:
            final_answer = "\n".join(answer_lines)
            answer_lines = None
            answer_complete = True
            if not has_played:
                result["phase"] = "answered"
            continue

        if line.startswith("你说：") or line.startswith("你说:"):
            start_question(line[3:], position)
            continue
        model_start = MODEL_START.match(line)
        if model_start:
            result["model"] = model_start.group(1).lower()
            result["phase"] = "thinking"
            continue
        model_answer = MODEL_ANSWER.match(line)
        if model_answer and result["question"]:
            result["model"] = model_answer.group(1).lower()
            answer_lines = [model_answer.group(2)]
            answer_chars = len(model_answer.group(2))
            continue
        segment = SEGMENT.match(line)
        if segment and result["question"]:
            index = int(segment.group(1))
            if 1 <= index <= 1024:
                segments[index] = segment.group(2)[:MAX_ANSWER_CHARS]
            if not has_played:
                result["phase"] = "preparing"
            continue
        if line.startswith(("检查RKLLama服务和", "加载SenseVoice", "加载离线中文语音合成")):
            result["phase"] = "loading"
        elif line.startswith("准备完成。"):
            result["phase"] = "running"
        elif line.startswith("正在自动监听，等待唤醒词："):
            result["phase"] = "waiting"
        elif line.startswith(("WAKE_DETECTED：", "DESKTOP_WAKE_ACCEPTED：")):
            result["phase"] = "waking"
        elif line.startswith(("提示音结束，请提问；", "正在自动监听，请直接说话", "请说话，录音")):
            result["phase"] = "listening"
        elif line.startswith(("WAKE_TIMEOUT：", "WAKE_CANCELLED：", "没有识别到有效问题，返回待唤醒")):
            result["phase"] = "waiting"
            result["feedback"] = _clean(line.split("：", 1)[-1], MAX_NOTICE_CHARS)
        elif line.startswith("首段进入播放耗时：") and result["question"]:
            has_played = True
            result["phase"] = "answering"
        elif line.startswith("正在生成并播放语音回答") and result["question"]:
            result["phase"] = "preparing"
        elif line.startswith("播报完成：") and result["question"]:
            result["phase"] = "complete"
        elif line.startswith("语音合成耗时：") and result["question"]:
            result["phase"] = "complete"
        elif line.startswith("本机控制："):
            feedback = _clean(line[len("本机控制："):], MAX_NOTICE_CHARS)
            result["feedback"] = feedback
            result["phase"] = "exiting" if feedback.startswith("对话已结束") else "control"
        elif line.startswith("已达到播报上限"):
            result["warning"] = _clean(line, MAX_NOTICE_CHARS)
        elif (line.startswith(("错误：", "本轮回答或播报未完成：", "语音合成或播放失败：",
                               "本机控制失败：", "控制已执行，确认语音播放失败："))
              or re.match(r"^调用" + MODEL_PATTERN + r"失败[：:]", line, flags=re.I)):
            result["warning"] = _clean(line, MAX_NOTICE_CHARS)
            result["phase"] = "error"
        elif line == "已退出。":
            result["phase"] = "stopped"

    if not result["session_key"]:
        result["session_key"] = "{}:{}".format(pid, session_offset)
    if answer_lines is not None:
        final_answer = "\n".join(answer_lines)
    raw_answer = final_answer if final_answer is not None else "".join(segments[i] for i in sorted(segments))
    result["answer"] = _hide_reasoning(raw_answer, result["model"])
    result["answer_partial"] = bool(result["answer"]) and not answer_complete
    if not running:
        result["phase"] = "stopped"
    result["phase_label"] = PHASE_LABELS[result["phase"]]
    result["phase_uncertain"] = result["phase"] == "running"
    return result


def parse_log(text, running=False, pid=0):
    """Return the latest recognised chat turn and coarse phase from text.

    Only the latest startup marker's session is considered. Caller-provided
    running=False always overrides stale 'listening'/'playing' log markers.
    An old log cannot prove ownership of a newly observed PID: the caller must
    hold back this snapshot until it sees the new process's startup marker or
    otherwise knows the log file belongs to that process.
    """
    return _parse(text, running=running, pid=pid)


def read_snapshot(log_path, running=False, pid=0):
    """Read at most 1 MiB of a log, without modifying it or any state files."""
    try:
        with Path(log_path).open("rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            offset = max(0, size - MAX_LOG_BYTES)
            stream.seek(offset)
            raw = stream.read(MAX_LOG_BYTES)
        if offset:
            newline = raw.find(b"\n")
            if newline < 0:
                raw, offset = b"", size
            else:
                offset += newline + 1
                raw = raw[newline + 1:]
        # Polling a line while print() is still writing it must not turn a
        # fragment of a question/think tag into an apparently complete item.
        if raw and not raw.endswith(b"\n"):
            end = raw.rfind(b"\n")
            raw = raw[:end + 1] if end >= 0 else b""
        result = _parse(raw.decode("utf-8", errors="replace"), running=running,
                        pid=pid, base_offset=offset)
        result["log_available"] = True
        result["log_bytes"] = size
        return result
    except OSError:
        result = parse_log("", running=running, pid=pid)
        result["log_available"] = False
        result["log_bytes"] = 0
        return result
