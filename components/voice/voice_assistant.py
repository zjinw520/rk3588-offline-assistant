#!/usr/bin/env python3
"""Offline push-to-talk assistant for the ATK-DLRK3588 board.

Pipeline: ES8388 microphone -> SenseVoice on CPU -> RKLLama on localhost.
The program makes no cloud requests.
"""

import argparse
import json
import re
import subprocess
import sys
import time
import wave
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import numpy as np
import sherpa_onnx

from system_volume import VolumeFile, play_pcm_with_volume
from streaming_voice import AudioClip, SpeechPipeline, VOICE_VERSION, stream_reply
from continuous_audio import ContinuousPlayer
from wake_word import DEFAULT_WAKE_PHRASE, is_cancel, normalize_phrase, prompt_samples, wake_remainder
from voice_commands import MODEL_NAMES, parse_command, publish_state
from microphone import microphone_lease
from desktop_wake_state import claim_request


DEFAULT_ASR_DIR = Path(
    "/userdata/voice-models/"
    "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
)
DEFAULT_WORK_DIR = Path("/userdata/voice-assistant")
DEFAULT_TTS_DIR = Path("/userdata/voice-models/sherpa-onnx-vits-zh-ll")
DEFAULT_VAD_MODEL = Path("/userdata/voice-models/silero_vad.onnx")
PLAYBACK_VOLUME = VolumeFile()


def parse_args():
    parser = argparse.ArgumentParser(description="RK3588 offline voice assistant")
    parser.add_argument("--model", default="deepseek-r1-1.5b")
    parser.add_argument("--api", default="http://127.0.0.1:8080")
    parser.add_argument("--asr-dir", type=Path, default=DEFAULT_ASR_DIR)
    parser.add_argument("--seconds", type=int, default=7)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--device", default="hw:CARD=rockchipes8388,DEV=0")
    parser.add_argument("--tts-dir", type=Path, default=DEFAULT_TTS_DIR)
    parser.add_argument("--speaker", type=int, default=2)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument(
        "--max-speech-chars",
        type=int,
        default=120,
        help="最多合成并播报多少个字符，默认120；文本答案仍完整显示",
    )
    parser.add_argument("--play-device", default="plughw:CARD=rockchipes8388,DEV=0")
    parser.add_argument("--no-speech", action="store_true")
    parser.add_argument("--audio-test", action="store_true",
                        help="只播放三个低幅测试音，不加载识别、语言或合成模型")
    parser.add_argument("--no-stream", action="store_true",
                        help="使用原来的整段回答和整段合成流程，便于对比或回退")
    parser.add_argument("--tts-chunk-chars", type=int, default=60,
                        help="每段语音最多合成多少个字符，默认60")
    parser.add_argument("--auto", action="store_true")
    parser.add_argument("--wake-phrase", default=DEFAULT_WAKE_PHRASE,
                        help="自动模式的唤醒词，默认：你好小助手")
    parser.add_argument("--no-wake-word", action="store_true",
                        help="自动模式直接接收问题，保留之前的免唤醒行为")
    parser.add_argument("--wake-timeout", type=float, default=15.0,
                        help="唤醒后等待开始提问的秒数，默认15")
    parser.add_argument("--wake-test", action="store_true",
                        help="只测试唤醒、提示音和问题识别，不连接语言模型或加载TTS")
    parser.add_argument("--vad-model", type=Path, default=DEFAULT_VAD_MODEL)
    parser.add_argument("--vad-threshold", type=float, default=0.5)
    parser.add_argument("--vad-silence", type=float, default=0.8)
    parser.add_argument("--max-speech-seconds", type=float, default=20.0)
    return parser.parse_args()


def read_json(url, timeout=15):
    with urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def check_rkllama(api, model):
    try:
        payload = read_json(api.rstrip("/") + "/api/tags")
    except (OSError, URLError, ValueError) as exc:
        raise RuntimeError(f"无法连接RKLLama服务：{exc}") from exc

    names = {
        item.get("name") or item.get("model")
        for item in payload.get("models", [])
    }
    if model not in names:
        raise RuntimeError(f"没有找到模型 {model}；当前模型：{sorted(names)}")


def create_recognizer(asr_dir, threads):
    model = asr_dir / "model.int8.onnx"
    tokens = asr_dir / "tokens.txt"
    if not model.is_file() or not tokens.is_file():
        raise RuntimeError(f"SenseVoice模型文件不完整：{asr_dir}")

    return sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=str(model),
        tokens=str(tokens),
        num_threads=threads,
        language="zh",
        use_itn=True,
        debug=False,
        provider="cpu",
    )


def create_tts(tts_dir, threads):
    required = [
        "model.onnx",
        "lexicon.txt",
        "tokens.txt",
        "phone.fst",
        "date.fst",
        "number.fst",
    ]
    missing = [name for name in required if not (tts_dir / name).is_file()]
    if missing:
        raise RuntimeError(f"TTS模型文件不完整，缺少：{missing}")

    config = sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                model=str(tts_dir / "model.onnx"),
                lexicon=str(tts_dir / "lexicon.txt"),
                tokens=str(tts_dir / "tokens.txt"),
            ),
            provider="cpu",
            debug=False,
            num_threads=threads,
        ),
        rule_fsts=",".join(
            str(tts_dir / name)
            for name in ("phone.fst", "date.fst", "number.fst")
        ),
        max_num_sentences=1,
    )
    if not config.validate():
        raise RuntimeError("TTS模型配置校验失败")
    return sherpa_onnx.OfflineTts(config)


def create_vad(model, threshold, silence, max_speech_seconds):
    if not model.is_file():
        raise RuntimeError(f"没有找到VAD模型：{model}")
    config = sherpa_onnx.VadModelConfig()
    config.silero_vad.model = str(model)
    config.silero_vad.threshold = threshold
    config.silero_vad.min_silence_duration = silence
    config.silero_vad.min_speech_duration = 0.3
    config.silero_vad.max_speech_duration = max_speech_seconds
    config.sample_rate = 16000
    config.num_threads = 1
    return sherpa_onnx.VoiceActivityDetector(
        config, buffer_size_in_seconds=max_speech_seconds + 10
    ), config.silero_vad.window_size


def record_audio(device, seconds, output):
    with microphone_lease():
        return _record_audio_locked(device, seconds, output)


def _record_audio_locked(device, seconds, output):
    output.unlink(missing_ok=True)
    command = [
        "arecord",
        "-D",
        device,
        "-r",
        "44100",
        "-f",
        "S16_LE",
        "-d",
        str(seconds),
        "-c",
        "2",
        str(output),
    ]
    result = subprocess.run(command, check=False)

    # Some vendor ES8388/ALSA builds return 1 while closing the capture stream
    # even though a complete, playable WAV file has already been written.
    # Validate the artifact instead of treating that vendor-specific exit code
    # as an unconditional recording failure.
    if not output.is_file() or output.stat().st_size <= 44:
        raise RuntimeError(
            f"录音失败：arecord退出码为{result.returncode}，且没有生成有效WAV文件"
        )

    try:
        with wave.open(str(output), "rb") as wav_file:
            duration = wav_file.getnframes() / wav_file.getframerate()
    except (OSError, wave.Error) as exc:
        raise RuntimeError(f"录音文件无法读取：{exc}") from exc

    if duration < max(1.0, seconds * 0.5):
        raise RuntimeError(
            f"录音不完整：期望{seconds}秒，实际只有{duration:.2f}秒"
        )


def load_wav(filename):
    with wave.open(str(filename), "rb") as wav_file:
        sample_rate = wav_file.getframerate()
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        frames = wav_file.readframes(wav_file.getnframes())

    if sample_width != 2:
        raise RuntimeError(f"只支持16位PCM录音，当前采样宽度为{sample_width}字节")

    audio = np.frombuffer(frames, dtype=np.int16)
    if channels > 1:
        # The onboard electret microphone is carried by the first channel.
        audio = audio.reshape(-1, channels)[:, 0]
    return sample_rate, audio.astype(np.float32) / 32768.0


def transcribe(recognizer, filename):
    sample_rate, audio = load_wav(filename)
    return transcribe_samples(recognizer, sample_rate, audio)


def transcribe_samples(recognizer, sample_rate, audio):
    stream = recognizer.create_stream()
    stream.accept_waveform(sample_rate, audio)
    started = time.monotonic()
    recognizer.decode_stream(stream)
    elapsed = time.monotonic() - started
    return stream.result.text.strip(), elapsed


def listen_for_utterance(device, vad_model, threshold, silence, max_seconds,
                         start_timeout=None, prepared_vad=None, cancel_check=None):
    with microphone_lease(cancel=cancel_check) as acquired:
        if not acquired:
            return None
        return _listen_for_utterance_locked(device, vad_model, threshold, silence, max_seconds,
                                             start_timeout, prepared_vad, cancel_check)


def _listen_for_utterance_locked(device, vad_model, threshold, silence, max_seconds,
                                start_timeout=None, prepared_vad=None, cancel_check=None):
    vad, window_size = (prepared_vad if prepared_vad is not None else
                        create_vad(vad_model, threshold, silence, max_seconds))
    command = [
        "arecord",
        "-q",
        "-D",
        device,
        "-r",
        "16000",
        "-f",
        "S16_LE",
        "-c",
        "2",
        "-t",
        "raw",
        "-",
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    bytes_per_window = window_size * 2 * 2
    deadline = None if start_timeout is None else time.monotonic() + start_timeout
    speech_started = False
    try:
        while True:
            if cancel_check is not None and cancel_check():
                return None
            pending = bytearray()
            while len(pending) < bytes_per_window:
                block = process.stdout.read(bytes_per_window - len(pending))
                if not block:
                    error = process.stderr.read().decode("utf-8", errors="replace")
                    raise RuntimeError(
                        "连续录音意外停止；"
                        f"arecord退出码={process.poll()}；错误={error.strip()}"
                    )
                pending.extend(block)
            samples = np.frombuffer(pending, dtype=np.int16)
            samples = samples.reshape(-1, 2)[:, 0]
            samples = samples.astype(np.float32) / 32768.0
            vad.accept_waveform(samples)
            if not vad.empty():
                utterance = np.asarray(vad.front.samples, dtype=np.float32).copy()
                vad.pop()
                return utterance
            if deadline is not None:
                speech_started = speech_started or vad.is_speech_detected()
                if not speech_started and time.monotonic() >= deadline:
                    return None
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        for pipe in (process.stdout, process.stderr):
            if pipe is not None:
                pipe.close()


def play_wake_prompt(args):
    player = ContinuousPlayer(args.play_device, PLAYBACK_VOLUME, log=print)
    try:
        player.play(AudioClip(prompt_samples(), 16000))
        player.close()
    finally:
        player.close(drain=False)


def control_feedback(args, tts, text):
    print("本机控制：" + text)
    if tts is None:
        return
    player = ContinuousPlayer(args.play_device, PLAYBACK_VOLUME, log=print)
    try:
        config = sherpa_onnx.GenerationConfig()
        config.sid = args.speaker
        config.speed = args.speed
        config.silence_scale = 0.2
        audio = tts.generate(text, config)
        player.play(AudioClip(np.asarray(audio.samples, dtype=np.float32), audio.sample_rate))
        player.close()
    except Exception as error:
        print(f"控制已执行，确认语音播放失败：{error}", file=sys.stderr)
    finally:
        player.close(drain=False)


def execute_voice_command(args, tts, command):
    kind, value = command.kind, command.value
    try:
        if kind == "model":
            check_rkllama(args.api, value)
            args.model = value
            feedback = "已切换到" + MODEL_NAMES[value] + "。"
        elif kind in {"volume", "volume_step", "mute", "unmute"}:
            current = PLAYBACK_VOLUME.read()
            if kind == "volume":
                target = value
            elif kind == "volume_step":
                target = max(0, min(100, current + value))
            elif kind == "mute":
                if current:
                    args.previous_volume = current
                target = 0
            else:
                target = getattr(args, "previous_volume", 50) if current == 0 else current
            PLAYBACK_VOLUME.write(target)
            feedback = "已静音。" if target == 0 else f"音量已调到百分之{target}。"
        elif kind in {"speaker", "speaker_next"}:
            args.speaker = value if kind == "speaker" else (args.speaker + 1) % 5
            feedback = f"已切换到音色{args.speaker}。"
        elif kind in {"speed", "speed_step"}:
            args.speed = value if kind == "speed" else round(max(0.5, min(2.0, args.speed + value)), 2)
            feedback = f"语速已调到{args.speed:g}倍。"
        elif kind == "exit":
            # Finish the acknowledgement before asking QML to close: its normal
            # exit path stops this process group and returns via SystemUI Show.
            control_feedback(args, tts, "对话已结束。")
            publish_state(args, "对话已结束，返回桌面。", exit_requested=True)
            return "exit"
        elif kind == "help":
            feedback = "可以切换模型、调节音量、切换音色、调节语速，或者说结束对话。"
        elif kind == "invalid":
            feedback = value
        else:
            return False
        publish_state(args, feedback)
        control_feedback(args, tts, feedback)
    except Exception as error:
        control_feedback(args, tts, "控制失败，请查看屏幕日志。")
        print(f"本机控制失败：{error}", file=sys.stderr)
    return True


def run_auto_turn(args, recognizer, tts, initial_wake=None):
    """One question per wake-up. Capture is always closed before prompt/TTS."""
    def listen(start_timeout=None, prepared_vad=None):
        return listen_for_utterance(args.device, args.vad_model, args.vad_threshold,
                                    args.vad_silence, args.max_speech_seconds,
                                    start_timeout=start_timeout, prepared_vad=prepared_vad)

    if args.no_wake_word:
        print("\n正在自动监听，请直接说话……")
        audio = listen()
        question, asr_seconds = transcribe_samples(recognizer, 16000, audio)
    else:
        if initial_wake is not None:
            question = initial_wake["question"]
            asr_seconds = initial_wake.get("asr_seconds", 0.0)
            print("DESKTOP_WAKE_ACCEPTED：已接到桌面唤醒，无需再说一次唤醒词。")
        else:
            print(f"\n正在自动监听，等待唤醒词：{args.wake_phrase}")
            while True:
                audio = listen()
                text, asr_seconds = transcribe_samples(recognizer, 16000, audio)
                question = wake_remainder(text, args.wake_phrase)
                if question is not None:
                    break
                if text:
                    print(f"待唤醒识别：{text}；未匹配唤醒词。")
        print("WAKE_DETECTED：唤醒成功")
        # Prepare VAD before the cue so initialization cannot delay the next
        # microphone opening. Never capture the assistant's own prompt sound.
        prepared_vad = None
        if not question:
            prepared_vad = create_vad(args.vad_model, args.vad_threshold,
                                       args.vad_silence, args.max_speech_seconds)
        play_wake_prompt(args)
        if not question:
            print(f"提示音结束，请提问；{args.wake_timeout:g}秒内未开始说话将返回待唤醒。")
            audio = listen(start_timeout=args.wake_timeout, prepared_vad=prepared_vad)
            if audio is None:
                print("WAKE_TIMEOUT：没有开始提问，返回待唤醒。")
                return False
            question, asr_seconds = transcribe_samples(recognizer, 16000, audio)
            # A repeated greeting with a question is also fine; a greeting
            # alone does not get sent to the LLM as an unintended request.
            remainder = wake_remainder(question, args.wake_phrase)
            if remainder is not None:
                question = remainder
        if not normalize_phrase(question):
            print("没有识别到有效问题，返回待唤醒。")
            return False
        if is_cancel(question):
            print("WAKE_CANCELLED：已取消，返回待唤醒。")
            return False

    if args.wake_test:
        print(f"测试提问：{question}")
        print(f"语音识别耗时：{asr_seconds:.3f}秒")
        print("WAKE_TEST_OK v" + VOICE_VERSION)
        return True
    command = parse_command(question)
    if command is not None:
        return execute_voice_command(args, tts, command)
    answer_and_speak(args, tts, question, asr_seconds)
    return True


def ask_rkllama(api, model, question):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": question}],
        "stream": False,
    }
    request = Request(
        api.rstrip("/") + "/api/chat",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    started = time.monotonic()
    with urlopen(request, timeout=900) as response:
        payload = json.loads(response.read().decode("utf-8"))
    elapsed = time.monotonic() - started

    message = payload.get("message") or {}
    answer = message.get("content") or payload.get("response") or ""
    if not answer:
        raise RuntimeError(f"RKLLama返回格式异常：{payload}")
    return answer.strip(), elapsed


def speech_text(answer, max_chars=350):
    # Do not read hidden reasoning tags, URLs, or Markdown punctuation aloud.
    text = re.sub(r"<think>.*?</think>", "", answer, flags=re.DOTALL | re.I)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[`*_#>|\[\]{}]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def synthesize_and_play(tts, text, speaker, speed, output, play_device):
    config = sherpa_onnx.GenerationConfig()
    config.sid = speaker
    config.speed = speed
    config.silence_scale = 0.2

    started = time.monotonic()
    audio = tts.generate(text, config)
    elapsed = time.monotonic() - started
    if len(audio.samples) == 0:
        raise RuntimeError("TTS没有生成音频")

    samples = np.asarray(audio.samples, dtype=np.float32)
    samples = np.clip(samples, -1.0, 1.0)
    pcm = (samples * 32767.0).astype(np.int16)
    with wave.open(str(output), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(audio.sample_rate)
        wav_file.writeframes(pcm.tobytes())

    play_pcm_with_volume(samples, audio.sample_rate, play_device, PLAYBACK_VOLUME)
    return elapsed, len(samples) / audio.sample_rate


def answer_and_speak(args, tts, question, asr_seconds):
    if not question:
        print("没有识别到有效语音，请靠近麦克风后重试。")
        return

    print(f"你说：{question}")
    print(f"语音识别耗时：{asr_seconds:.3f}秒")
    print(f"{args.model}正在生成回答……")

    if not args.no_stream:
        return streaming_answer_and_speak(args, tts, question, asr_seconds)

    try:
        answer, llm_seconds = ask_rkllama(args.api, args.model, question)
    except Exception as exc:
        print(f"调用{args.model}失败：{exc}", file=sys.stderr)
        return

    print(f"\n{args.model}：{answer}")
    print(f"{args.model}耗时：{llm_seconds:.3f}秒")

    if tts is not None:
        spoken = speech_text(answer, args.max_speech_chars)
        if spoken:
            print("正在生成并播放语音回答……")
            try:
                tts_seconds, audio_seconds = synthesize_and_play(
                    tts,
                    spoken,
                    args.speaker,
                    args.speed,
                    DEFAULT_WORK_DIR / "answer.wav",
                    args.play_device,
                )
                print(
                    f"语音合成耗时：{tts_seconds:.3f}秒；"
                    f"音频长度：{audio_seconds:.2f}秒"
                )
            except Exception as exc:
                print(f"语音合成或播放失败：{exc}", file=sys.stderr)


def streaming_answer_and_speak(args, tts, question, asr_seconds):
    started = time.monotonic()
    pipeline = None
    writer = [None]
    sample_rate = [None]
    queued = [0]
    player = ContinuousPlayer(args.play_device, PLAYBACK_VOLUME, log=print) if tts is not None else None

    def synthesize(text):
        config = sherpa_onnx.GenerationConfig()
        config.sid = args.speaker
        config.speed = args.speed
        config.silence_scale = 0.2
        audio = tts.generate(text, config)
        samples = np.clip(np.asarray(audio.samples, dtype=np.float32), -1.0, 1.0)
        if not len(samples):
            raise RuntimeError("TTS没有生成音频")
        if writer[0] is None:
            sample_rate[0] = audio.sample_rate
            writer[0] = wave.open(str(DEFAULT_WORK_DIR / "answer.wav"), "wb")
            writer[0].setnchannels(1)
            writer[0].setsampwidth(2)
            writer[0].setframerate(audio.sample_rate)
        if sample_rate[0] != audio.sample_rate:
            raise RuntimeError("分段合成的采样率发生变化")
        writer[0].writeframes((samples * 32767.0).astype("<i2").tobytes())
        return AudioClip(samples, audio.sample_rate)

    def play(clip):
        player.play(clip)

    def first_audio():
        elapsed = time.monotonic() - started
        print(f"首段进入播放耗时：{elapsed:.3f}秒；含语音识别约{elapsed + asr_seconds:.3f}秒")

    def submit(text):
        if pipeline.submit(text):
            queued[0] += 1
            print(f"准备播报第{queued[0]}段：{text}")

    try:
        if tts is not None:
            pipeline = SpeechPipeline(synthesize, play, first_audio=first_audio, log=print,
                                      idle=player.idle)
        reply = stream_reply(
            args.api, args.model, question,
            submit=submit if pipeline is not None else None,
            max_speech_chars=args.max_speech_chars,
            segment_chars=args.tts_chunk_chars,
            first_text=lambda elapsed: print(f"首段正式文字到达：{elapsed:.3f}秒"),
        )
        print(f"\n{args.model}：{reply.text}")
        print(f"模型流接收完成：{reply.elapsed:.3f}秒")
        if reply.speech_capped and pipeline is not None:
            print(f"已达到播报上限{args.max_speech_chars}字，后续文字保留在回答中。")
        if pipeline is not None:
            stats = pipeline.finish()
            player.close()
            print(f"播报完成：{stats.played}段；合成累计{stats.synthesis_seconds:.3f}秒；"
                  f"音频总长{stats.audio_seconds:.2f}秒；"
                  f"本轮总耗时{time.monotonic() - started + asr_seconds:.3f}秒")
    except Exception as error:
        print(f"本轮回答或播报未完成：{error}", file=sys.stderr)
    finally:
        if pipeline is not None:
            try:
                pipeline.finish(cancel=True)
            except Exception:
                pass  # The original failure has already been reported.
        if player is not None:
            try:
                player.close(drain=False)
            except Exception as error:
                print(f"关闭播放器失败：{error}", file=sys.stderr)
        if writer[0] is not None:
            writer[0].close()


def main():
    args = parse_args()
    if args.seconds < 1 or args.seconds > 30:
        raise RuntimeError("录音时间必须在1到30秒之间")
    if not 0 <= args.speaker <= 4:
        raise RuntimeError("当前TTS模型的speaker必须在0到4之间")
    if not 0.5 <= args.speed <= 2.0:
        raise RuntimeError("语速必须在0.5到2.0之间")
    if not 20 <= args.max_speech_chars <= 1000:
        raise RuntimeError("最大播报字符数必须在20到1000之间")
    if not 20 <= args.tts_chunk_chars <= 120:
        raise RuntimeError("每段合成字符数必须在20到120之间")
    if not normalize_phrase(args.wake_phrase):
        raise RuntimeError("唤醒词必须包含文字或数字")
    if not 5 <= args.wake_timeout <= 60:
        raise RuntimeError("唤醒后等待时间必须在5到60秒之间")
    if args.wake_test and args.no_wake_word:
        raise RuntimeError("唤醒测试不能同时关闭唤醒词")

    if args.audio_test:
        print("固定测试音：使用同一个播放器播放三次，注意起止和间隔是否有啪声。")
        rate = 16000
        phase = np.arange(int(rate * 0.6), dtype=np.float32) / rate
        tone = (0.08 * np.sin(2 * np.pi * 440 * phase)).astype(np.float32)
        player = ContinuousPlayer(args.play_device, PLAYBACK_VOLUME, log=print)
        try:
            for index in range(3):
                print(f"测试音 {index + 1}/3")
                player.play(AudioClip(tone, rate))
                if index < 2:
                    for _ in range(10):
                        player.idle()
            player.close()
        finally:
            player.close(drain=False)
        print("AUDIO_TEST_DONE v" + VOICE_VERSION)
        return 0

    DEFAULT_WORK_DIR.mkdir(parents=True, exist_ok=True)
    wav_path = DEFAULT_WORK_DIR / "question.wav"
    initial_wake = claim_request() if args.auto and not args.wake_test else None

    print(f"语音流水线v{VOICE_VERSION}：{'整段对比模式' if args.no_stream else '流式回答、分段合成播放'}")
    if args.wake_test:
        print("唤醒测试：只加载离线识别，提示音由本机生成。")
    else:
        print(f"检查RKLLama服务和{args.model}模型……")
        check_rkllama(args.api, args.model)
    print("加载SenseVoice离线识别模型……")
    recognizer = create_recognizer(args.asr_dir, args.threads)
    tts = None
    if not args.no_speech and not args.wake_test:
        print("加载离线中文语音合成模型……")
        tts = create_tts(args.tts_dir, args.threads)
    print("准备完成。全程只访问板子本机，不访问互联网。")
    if not args.wake_test:
        publish_state(args, "已就绪，请说：你好小助手。")

    while True:
        if args.auto or args.wake_test:
            if initial_wake is None and args.auto and not args.wake_test:
                initial_wake = claim_request()
            completed = run_auto_turn(args, recognizer, tts, initial_wake=initial_wake)
            initial_wake = None
            if completed == "exit":
                return 0
            if args.wake_test and completed:
                return 0
            time.sleep(0.5)
            continue

        command = input("\n按回车开始说话，输入 q 后回车退出：").strip().lower()
        if command in {"q", "quit", "exit"}:
            return 0
        print(f"请说话，录音{args.seconds}秒……")
        record_audio(args.device, args.seconds, wav_path)
        question, asr_seconds = transcribe(recognizer, wav_path)
        answer_and_speak(args, tts, question, asr_seconds)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已退出。")
        raise SystemExit(130)
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(1)
