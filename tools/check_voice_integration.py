"""Board integration check using a generated utterance, not microphone capture.

Uses the user's existing voice modules. Outputs only canned test content.
"""
import argparse
import json
from pathlib import Path
import sys
import time
import wave

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--play", action="store_true", help="Play a short test through the existing volume-aware PCM player")
    args = parser.parse_args()
    sys.path.insert(0, "/userdata/voice-assistant")
    import numpy as np
    import sherpa_onnx
    import voice_assistant as voice
    from continuous_audio import ContinuousPlayer
    from streaming_voice import AudioClip, stream_reply
    from voice_commands import parse_command
    from wake_word import wake_remainder
    folder = ROOT / "integration-artifacts"
    folder.mkdir(exist_ok=True)
    result = {"physical_microphone_tested": False}
    started = time.monotonic()
    print("VOICE_INTEGRATION_START generated_audio_only", flush=True)
    voice.check_rkllama("http://127.0.0.1:8080", "qwen2-0.5b")
    voice.check_rkllama("http://127.0.0.1:8080", "deepseek-r1-1.5b")
    tts = voice.create_tts(voice.DEFAULT_TTS_DIR, 4)
    config = sherpa_onnx.GenerationConfig()
    config.sid, config.speed, config.silence_scale = 2, 1.25, .2
    question_audio = tts.generate("一加一等于几？", config)
    samples = np.asarray(question_audio.samples, dtype=np.float32)
    rate = question_audio.sample_rate
    recognizer = voice.create_recognizer(voice.DEFAULT_ASR_DIR, 2)
    if rate != 16000:
        samples = np.interp(np.arange(int(len(samples)*16000/rate))*rate/16000,
                            np.arange(len(samples)), samples).astype(np.float32)
        rate = 16000
    text, asr_seconds = voice.transcribe_samples(recognizer, rate, samples)
    if not text.strip():
        raise RuntimeError("Generated test audio was not recognized")
    result.update(asr_text=text, asr_seconds=asr_seconds)
    print("ASR_GENERATED_UTTERANCE_OK " + text, flush=True)
    reply = stream_reply("http://127.0.0.1:8080", "qwen2-0.5b", text + "请只用一句简短中文回答。",
                         max_speech_chars=120, segment_chars=60)
    if not reply.text.strip():
        raise RuntimeError("Existing streaming voice client returned no text")
    result["qwen_reply"] = reply.text
    answer = tts.generate(reply.text[:80], config)
    output = np.asarray(answer.samples, dtype=np.float32)
    if not len(output) or not np.isfinite(output).all() or np.max(np.abs(output)) <= 1e-5:
        raise RuntimeError("TTS output was empty or invalid")
    with wave.open(str(folder / "test-answer.wav"), "wb") as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(answer.sample_rate)
        wav.writeframes((np.clip(output, -1, 1)*32767).astype("<i2").tobytes())
    result["tts_audio_seconds"] = len(output)/answer.sample_rate
    print("STREAMING_CLIENT_AND_TTS_OK", flush=True)
    if args.play:
        player = ContinuousPlayer("plughw:CARD=rockchipes8388,DEV=0", voice.PLAYBACK_VOLUME, log=print)
        try:
            player.play(AudioClip(output, answer.sample_rate))
            player.close()
        finally:
            player.close(drain=False)
        result["alsa_playback_command_succeeded"] = True
        print("ALSA_PCM_PLAYBACK_OK", flush=True)
    reply = stream_reply("http://127.0.0.1:8080", "deepseek-r1-1.5b", "一加一等于几？请只给出简短答案。",
                         max_speech_chars=120, segment_chars=60)
    if not reply.text.strip() or "<think>" in reply.text or "</think>" in reply.text:
        raise RuntimeError("Reasoning filter returned invalid visible text")
    result["deepseek_visible_reply"] = reply.text
    print("DEEPSEEK_EXISTING_REASONING_FILTER_OK " + reply.text, flush=True)
    commands = {"切换到千问":"model", "调高音量":"volume_step", "换个音色":"speaker_next",
                "语速快一点":"speed_step", "结束对话":"exit"}
    for text, kind in commands.items():
        command = parse_command(text)
        if command is None or command.kind != kind:
            raise RuntimeError("Existing voice command parsing failed: " + text)
    # Recognition/matching only; do not change model preferences or volume.
    match = wake_remainder("你好小助手", "你好小助手")
    if match is None:
        raise RuntimeError("Wake phrase matching failed")
    result.update(command_parser_checked=True, wake_phrase_match_checked=True,
                  seconds=time.monotonic()-started, ok=True)
    (folder / "voice-integration.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("VOICE_INTEGRATION_OK", flush=True)


if __name__ == "__main__":
    main()
