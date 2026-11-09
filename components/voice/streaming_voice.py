"""Incremental local chat, speech segmentation and ordered audio playback.

Only the standard library is used here. Model/ALSA implementations are injected
by voice_assistant.py, so transport and ordering can be tested without a board.
"""

import json
import queue
import re
import threading
import time
from dataclasses import dataclass
from urllib.request import Request, urlopen


VOICE_VERSION = "0.7.0"
MAX_LINE_BYTES = 1024 * 1024


def iter_chat(api, model, question, opener=None):
    """Yield validated Ollama-shaped chunks; EOF without done is an error."""
    body = {"model": model, "messages": [{"role": "user", "content": question}],
            "stream": True}
    if model.lower().startswith("deepseek-r1"):
        # In the pinned RKLLama handler this also inserts an opening <think>
        # when an R1 tokenizer already put that marker into the input prompt.
        # It does not disable or accelerate the model's reasoning.
        body["enable_thinking"] = True
    request = Request(api.rstrip("/") + "/api/chat",
                      data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                      headers={"Content-Type": "application/json"})
    opener = opener or urlopen
    with opener(request, timeout=900) as response:
        while True:
            raw = response.readline(MAX_LINE_BYTES + 1)
            if not raw:
                raise RuntimeError("RKLLama连接在完成标记前中断，回答可能不完整")
            if len(raw) > MAX_LINE_BYTES:
                raise RuntimeError("RKLLama单条响应超过大小限制")
            if not raw.strip():
                continue
            try:
                chunk = json.loads(raw.decode("utf-8"))
            except (UnicodeError, ValueError) as error:
                raise RuntimeError("RKLLama返回了无效的JSON响应") from error
            if not isinstance(chunk, dict):
                raise RuntimeError("RKLLama响应不是JSON对象")
            if chunk.get("error"):
                raise RuntimeError("RKLLama错误：{}".format(chunk["error"]))
            message = chunk.get("message") or {}
            if not isinstance(message, dict):
                raise RuntimeError("RKLLama message字段格式异常")
            text = message.get("content", chunk.get("response", ""))
            if not isinstance(text, str):
                raise RuntimeError("RKLLama回答内容不是文本")
            done = chunk.get("done") is True
            # Separate reasoning fields are deliberately not sent to speech.
            yield text, done
            if done:
                return


class ReasoningFilter:
    """Hide think blocks, including tags split across arbitrary chunks."""
    def __init__(self, coalesce_initial=False):
        self.pending = ""
        self.depth = 0
        self.coalesce_initial = coalesce_initial
        self.before_content = True

    def feed(self, text, final=False):
        self.pending += text
        output = []
        tags = ("<think>", "</think>")
        while self.pending:
            start = self.pending.find("<")
            if start != 0:
                size = len(self.pending) if start < 0 else start
                if self.pending[:size].strip():
                    self.before_content = False
                if not self.depth:
                    output.append(self.pending[:size])
                self.pending = self.pending[size:]
                continue
            lower = self.pending.lower()
            if lower.startswith(tags[0]):
                # The pinned R1 handler can prepend <think> before the first
                # token '<', followed by the model's own split opening tag.
                # Coalesce only such initial duplicate wrappers for R1.
                if not (self.coalesce_initial and self.depth == 1 and self.before_content):
                    self.depth += 1
                self.pending = self.pending[len(tags[0]):]
            elif lower.startswith(tags[1]):
                self.depth = max(0, self.depth - 1)
                if not self.depth:
                    self.before_content = False
                self.pending = self.pending[len(tags[1]):]
            elif any(tag.startswith(lower) for tag in tags):
                # Do not speak a partial marker, even at truncated EOF.
                if final:
                    self.pending = ""
                break
            else:
                if not self.depth:
                    output.append(self.pending[0])
                self.pending = self.pending[1:]
        return "".join(output)


class SpeechCleaner:
    """Remove URLs/markup without losing URL state at a network boundary."""
    def __init__(self):
        self.pending = ""
        self.in_url = False

    def feed(self, text, final=False):
        self.pending += text
        output = []
        prefixes = ("http://", "https://")
        while self.pending:
            if self.in_url:
                end = re.search(r"[\s。！？；，、]", self.pending)
                if end is None:
                    self.pending = ""
                    break
                self.pending = self.pending[end.start():]
                self.in_url = False
                continue
            lower = self.pending.lower()
            prefix = next((p for p in prefixes if lower.startswith(p)), None)
            if prefix:
                self.pending = self.pending[len(prefix):]
                self.in_url = True
            elif not final and any(p.startswith(lower) for p in prefixes):
                break
            else:
                character = self.pending[0]
                self.pending = self.pending[1:]
                if character not in "`*_#>|[]{}":
                    output.append(character)
        return "".join(output)


class SpeechSegments:
    def __init__(self, max_chars=60, min_chars=12):
        if not 1 <= min_chars <= max_chars:
            raise ValueError("Invalid speech segment limits")
        self.max_chars = max_chars
        self.min_chars = min_chars
        self.cleaner = SpeechCleaner()
        self.pending = ""

    def feed(self, visible, final=False):
        self.pending += self.cleaner.feed(visible, final)
        output = []
        while self.pending:
            boundary = None
            for match in re.finditer(r"[。！？!?；;\n]|(?<!\d)\.(?!\d)", self.pending):
                if self.min_chars <= match.end() <= self.max_chars:
                    boundary = match.end()
                    break
            if boundary is None and len(self.pending) >= self.max_chars:
                soft = list(re.finditer(r"[，,、：:\s]", self.pending[:self.max_chars]))
                boundary = next((m.end() for m in reversed(soft)
                                 if m.end() >= self.min_chars), self.max_chars)
            if boundary is None:
                if not final:
                    break
                boundary = len(self.pending)
            segment = re.sub(r"\s+", " ", self.pending[:boundary]).strip()
            self.pending = self.pending[boundary:]
            if segment and re.search(r"[\w\u3400-\u9fff]", segment):
                output.append(segment)
        return output


@dataclass
class AudioClip:
    samples: object
    sample_rate: int

    @property
    def duration(self):
        return len(self.samples) / self.sample_rate


@dataclass
class Reply:
    text: str
    elapsed: float
    spoken_chars: int
    speech_capped: bool


def stream_reply(api, model, question, submit=None, max_speech_chars=1000,
                 segment_chars=60, first_text=None, opener=None):
    started = time.monotonic()
    reasoning = ReasoningFilter(coalesce_initial=model.lower().startswith("deepseek-r1"))
    segments = SpeechSegments(segment_chars)
    visible_parts = []
    raw_size = 0
    spoken_chars = 0
    capped = False
    text_seen = False
    for token, done in iter_chat(api, model, question, opener=opener):
        raw_size += len(token)
        if raw_size > 32768:
            raise RuntimeError("模型回答超过本次语音对话的文本上限")
        visible = reasoning.feed(token, final=done)
        if visible.strip() and not text_seen:
            text_seen = True
            if first_text:
                first_text(time.monotonic() - started)
        visible_parts.append(visible)
        for segment in segments.feed(visible, final=done):
            remaining = max(0, max_speech_chars - spoken_chars)
            if len(segment) > remaining:
                capped = True
                segment = segment[:remaining].rstrip()
            if segment and submit:
                submit(segment)
            spoken_chars += len(segment)
    text = "".join(visible_parts).strip()
    if not text:
        if reasoning.depth:
            raise RuntimeError("模型尚未输出思考结束标记，没有可播报的正式回答")
        raise RuntimeError("模型没有返回有效回答文本")
    return Reply(text, time.monotonic() - started, spoken_chars, capped)


@dataclass
class SpeechStats:
    synthesized: int = 0
    played: int = 0
    synthesis_seconds: float = 0.0
    audio_seconds: float = 0.0


class SpeechPipeline:
    """One synthesizer and one player; at most two prepared clips are queued.

    The caller caps submitted text, so its text queue is small. This lets the
    HTTP reader drain the model output even when speech runs more slowly.
    """
    def __init__(self, synthesize, play, first_audio=None, log=None, idle=None):
        self.synthesize = synthesize
        self.play = play
        self.first_audio = first_audio or (lambda: None)
        self.log = log or (lambda message: None)
        self.idle = idle
        self.texts = queue.Queue()
        self.audio = queue.Queue(maxsize=2)
        self.cancelled = threading.Event()
        self.error = None
        self.stats = SpeechStats()
        self.finished = False
        self.synth_thread = threading.Thread(target=self._synth_loop, daemon=True)
        self.play_thread = threading.Thread(target=self._play_loop, daemon=True)
        self.synth_thread.start()
        self.play_thread.start()

    def submit(self, text):
        if self.finished:
            raise RuntimeError("Speech pipeline is already closed")
        if self.cancelled.is_set():
            return False
        self.texts.put(text)
        return True

    def _fail(self, stage, error):
        self.error = self.error or RuntimeError("{}失败：{}".format(stage, error))
        self.cancelled.set()

    def _put_audio(self, clip):
        while not self.cancelled.is_set():
            try:
                self.audio.put(clip, timeout=0.1)
                return
            except queue.Full:
                pass

    def _synth_loop(self):
        try:
            while not self.cancelled.is_set():
                try:
                    text = self.texts.get(timeout=0.1)
                except queue.Empty:
                    continue
                if text is None:
                    self._put_audio(None)
                    return
                started = time.monotonic()
                clip = self.synthesize(text)
                elapsed = time.monotonic() - started
                self.stats.synthesized += 1
                self.stats.synthesis_seconds += elapsed
                self.log("第{}段合成完成：{:.3f}秒".format(self.stats.synthesized, elapsed))
                self._put_audio(clip)
        except Exception as error:
            self._fail("语音合成", error)

    def _play_loop(self):
        try:
            while not self.cancelled.is_set():
                try:
                    clip = self.audio.get(timeout=0.02 if self.idle else 0.1)
                except queue.Empty:
                    if self.idle is not None:
                        self.idle()
                    continue
                if clip is None or self.cancelled.is_set():
                    return
                if not self.stats.played:
                    self.first_audio()
                self.play(clip)
                self.stats.played += 1
                self.stats.audio_seconds += clip.duration
        except Exception as error:
            self._fail("语音播放", error)

    def finish(self, cancel=False):
        if not self.finished:
            self.finished = True
            if cancel:
                self.cancelled.set()
            self.texts.put(None)
            # Never resume microphone capture while a speech worker is active.
            # The existing process-group stop button can terminate the turn.
            self.synth_thread.join()
            self.play_thread.join()
        if self.error:
            raise self.error
        return self.stats
