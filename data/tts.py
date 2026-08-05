"""
Text-to-speech generation of the mock call recording.

This script takes the two-speaker call transcript in `data/mock-call-transcript.txt`
and synthesizes it into a single audio file (`data/mock-call.wav`) using the new
**MAI Voice 2** neural TTS model on Azure AI Speech.

Each `REP:` / `CUSTOMER:` turn is rendered with its own MAI-Voice-2 voice, and the
inline stage directions in the transcript (e.g. `[PAUSE 3 SECONDS]`,
`[MUTED 4 SECONDS]`, `[PAPERS RUSTLING]`) are converted into real silence so the
result sounds like an actual recorded support call.

The generated audio is the input for the speech-to-text / PII-redaction benchmark
described in the repo README.

Authentication uses Microsoft Entra ID (`DefaultAzureCredential`) because local
(key-based) auth is disabled on the Speech resource.

For more samples please visit https://github.com/Azure-Samples/cognitive-services-speech-sdk
"""

import argparse
import array
import json
import re
import wave
from pathlib import Path
from urllib.parse import urlparse

import azure.cognitiveservices.speech as speechsdk
from azure.identity import DefaultAzureCredential

ENDPOINT_URL = "https://charter-stt-pii-resource.cognitiveservices.azure.com/"
RESOURCE_ID = (
    "/subscriptions/fd918039-a89e-49a7-8e32-af614b3765f9"
    "/resourceGroups/rg-charter-stt-pii"
    "/providers/Microsoft.CognitiveServices/accounts/charter-stt-pii-resource"
)

TRANSCRIPT_PATH = Path(__file__).with_name("mock-call-transcript.txt")
OUTPUT_PATH = Path(__file__).with_name("mock-call.wav")
STEREO_OUTPUT_PATH = Path(__file__).with_name("mock-call-stereo.wav")
STEREO_REFERENCE_PATH = Path(__file__).with_name("mock-call-stereo-turns.json")

# MAI Voice 2 voices, one per speaker in the transcript.
VOICES = {
    "REP": "en-US-Olivia:MAI-Voice-2",
    "CUSTOMER": "en-US-Harper:MAI-Voice-2",
}

# Raw 24 kHz / 16-bit / mono PCM so turns can be concatenated into one WAV file.
SAMPLE_RATE = 24000
SAMPLE_WIDTH = 2
GAP_SECONDS = 0.4  # natural gap between speaker turns

SPEAKER_RE = re.compile(r"^(REP|CUSTOMER):\s*(.*)$")
DIRECTION_RE = re.compile(r"\[([^\]]*)\]")
TIMED_DIRECTION_RE = re.compile(r"(?:PAUSE|MUTED)\s+(\d+)\s+SECONDS?", re.IGNORECASE)


def silence(seconds: float) -> bytes:
    return b"\x00" * (int(SAMPLE_RATE * seconds) * SAMPLE_WIDTH)


def stereo_frames(mono: bytes, channel: int) -> bytes:
    samples = array.array("h")
    samples.frombytes(mono)
    stereo = array.array("h", [0]) * (len(samples) * 2)
    stereo[channel::2] = samples
    return stereo.tobytes()


def parse_transcript(path: Path) -> list[tuple[str, str]]:
    """Return [(speaker, line), ...] for every spoken turn in the transcript."""
    turns: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = SPEAKER_RE.match(line.strip())
        if match:
            turns.append((match.group(1), match.group(2).strip()))
    return turns


def split_segments(text: str) -> list[tuple[str, float]]:
    """Split a turn into (spoken_text, trailing_silence_seconds) segments.

    Bracketed stage directions are never spoken; timed ones become real silence.
    """
    segments: list[tuple[str, float]] = []
    cursor = 0
    for match in DIRECTION_RE.finditer(text):
        spoken = text[cursor : match.start()].strip()
        timed = TIMED_DIRECTION_RE.search(match.group(1))
        pause = float(timed.group(1)) if timed else 1.0
        segments.append((spoken, pause))
        cursor = match.end()
    trailing = text[cursor:].strip()
    if trailing or not segments:
        segments.append((trailing, 0.0))
    return [(spoken, pause) for spoken, pause in segments if spoken or pause]


def build_speech_config() -> speechsdk.SpeechConfig:
    parsed = urlparse(ENDPOINT_URL)
    base_endpoint = f"{parsed.scheme}://{parsed.netloc}"

    credential = DefaultAzureCredential()
    token = credential.get_token("https://cognitiveservices.azure.com/.default")

    speech_config = speechsdk.SpeechConfig(endpoint=base_endpoint)
    speech_config.authorization_token = f"aad#{RESOURCE_ID}#{token.token}"
    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Raw24Khz16BitMonoPcm
    )
    return speech_config


def synthesize(speech_config: speechsdk.SpeechConfig, voice: str, text: str) -> bytes:
    speech_config.speech_synthesis_voice_name = voice
    synthesizer = speechsdk.SpeechSynthesizer(
        speech_config=speech_config, audio_config=None
    )
    result = synthesizer.speak_text_async(text).get()

    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        return result.audio_data

    if result.reason == speechsdk.ResultReason.Canceled:
        details = result.cancellation_details
        raise RuntimeError(
            f"Speech synthesis canceled: {details.reason} / {details.error_details}"
        )
    raise RuntimeError(f"Speech synthesis failed: {result.reason}")


def generate_mono(turns: list[tuple[str, str]], speech_config) -> None:
    audio = bytearray()
    for index, (speaker, text) in enumerate(turns, start=1):
        voice = VOICES[speaker]
        for spoken, pause in split_segments(text):
            if spoken:
                audio += synthesize(speech_config, voice, spoken)
            if pause:
                audio += silence(pause)
        audio += silence(GAP_SECONDS)
        print(f"  [{index}/{len(turns)}] {speaker} ({voice}): {text[:60]}")

    with wave.open(str(OUTPUT_PATH), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(SAMPLE_WIDTH)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(bytes(audio))

    duration = len(audio) / (SAMPLE_RATE * SAMPLE_WIDTH)
    print(f"Wrote {OUTPUT_PATH} ({duration:.1f}s, {len(audio) / 1e6:.1f} MB)")


def generate_stereo(turns: list[tuple[str, str]], speech_config) -> None:
    audio = bytearray()
    reference_turns = []
    sample_cursor = 0
    channel_for = {"REP": 0, "CUSTOMER": 1}

    for index, (speaker, text) in enumerate(turns, start=1):
        voice = VOICES[speaker]
        channel = channel_for[speaker]
        turn_start = sample_cursor
        spoken_text = []
        for spoken, pause in split_segments(text):
            if spoken:
                mono = synthesize(speech_config, voice, spoken)
                audio += stereo_frames(mono, channel)
                sample_cursor += len(mono) // SAMPLE_WIDTH
                spoken_text.append(spoken)
            if pause:
                mono_silence = silence(pause)
                audio += stereo_frames(mono_silence, channel)
                sample_cursor += len(mono_silence) // SAMPLE_WIDTH

        turn_end = sample_cursor
        gap = silence(GAP_SECONDS)
        audio += stereo_frames(gap, channel)
        sample_cursor += len(gap) // SAMPLE_WIDTH
        if spoken_text:
            reference_turns.append(
                {
                    "id": f"turn-{index:04d}",
                    "participantId": speaker,
                    "channel": channel,
                    "offset": int(turn_start / SAMPLE_RATE * 10_000_000),
                    "duration": int(
                        (turn_end - turn_start) / SAMPLE_RATE * 10_000_000
                    ),
                    "text": " ".join(spoken_text),
                }
            )
        print(f"  [{index}/{len(turns)}] {speaker} ch{channel} ({voice}): {text[:60]}")

    with wave.open(str(STEREO_OUTPUT_PATH), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(SAMPLE_WIDTH)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(bytes(audio))

    reference = {
        "id": "mock-call-stereo",
        "language": "en",
        "modality": "transcript",
        "speakerAttributed": True,
        "channelMap": {"0": "REP", "1": "CUSTOMER"},
        "conversationItems": reference_turns,
    }
    STEREO_REFERENCE_PATH.write_text(
        json.dumps(reference, indent=2), encoding="utf-8"
    )
    duration = sample_cursor / SAMPLE_RATE
    print(
        f"Wrote {STEREO_OUTPUT_PATH} "
        f"({duration:.1f}s, {len(audio) / 1e6:.1f} MB, stereo)"
    )
    print(f"Wrote {STEREO_REFERENCE_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stereo",
        action="store_true",
        help="Generate mock-call-stereo.wav without replacing the mono baseline.",
    )
    args = parser.parse_args()
    turns = parse_transcript(TRANSCRIPT_PATH)
    print(f"Parsed {len(turns)} turns from {TRANSCRIPT_PATH.name}")

    speech_config = build_speech_config()
    if args.stereo:
        generate_stereo(turns, speech_config)
    else:
        generate_mono(turns, speech_config)


if __name__ == "__main__":
    main()
