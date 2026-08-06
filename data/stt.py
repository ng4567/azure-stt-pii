"""
Speech-to-text accuracy + latency benchmark for the three implemented architectures.

Runs a mono or dual-channel call through three STT pipelines, emits chronological
speaker turns, and scores the derived flat transcript against a reference:

  1. Azure Speech real-time  - classic `azure-cognitiveservices-speech` SDK
     (`SpeechRecognizer`, continuous recognition). Truly incremental: the service
     runs its own VAD and finalizes each utterance as audio streams in.
  2. MAI-Transcribe-1.5 real-time - Voice Live WebSocket, with
     `input_audio_transcription.model = "mai-transcribe"`.
  3. MAI-Transcribe-1.5 batch - post-call VAD utterances sent concurrently to the
     fast-transcription REST API. The model returns no usable whole-call phrase
     timing, so utterance requests preserve the turn boundary needed downstream.

Fair segmentation
-----------------
MAI-Transcribe has no incremental streaming path: it never emits
`input_audio_transcription.delta`, and Voice Live's own VAD returns a transcript only
for the first turn of a session (verified against server VAD, semantic VAD,
`create_response`, and manual `response.create`). It must therefore be driven with
explicit buffer commits.

Committing on a fixed clock would slice utterances mid-sentence and punish MAI for
our chunking rather than its recognition (at a 3s cadence WER nearly quadruples).
Instead we run a local VAD independently over each MAI channel and commit at natural
pauses - the same kind of boundary the Speech SDK picks internally.

Latency
-------
For all engines latency is *finalization lag*: the delay between the end of an
utterance's audio and the arrival of its final transcript. That is measured the same
way on both real-time paths, so the numbers are directly comparable. Architecture 3
is reported as whole-call turnaround instead, since it has no per-utterance notion.

Note that MAI still pays a structural penalty in real time: it cannot return anything
until an utterance closes, whereas the Speech SDK streams partial hypotheses while the
speaker is still talking.

All three architectures run concurrently, so a full pass costs roughly one call
duration rather than three.

Authentication uses Microsoft Entra ID (`DefaultAzureCredential`) because local
(key-based) auth is disabled on the Speech resource.
"""

import array
import argparse
import asyncio
import base64
import json
import math
import re
import statistics
import string
import sys
import time
import tempfile
import wave
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

import azure.cognitiveservices.speech as speechsdk
import jiwer
import requests
import websockets
from azure.identity import DefaultAzureCredential
try:
    from conversation import (
        AudioTiming,
        MAX_CONVERSATION_ITEM_CHARS,
        Turn,
        finalize_turns,
        flatten_turns,
        serialize_conversation,
        validate_channel_map,
    )
except ModuleNotFoundError:  # imported as `data.stt` by tests and other packages
    from data.conversation import (
        AudioTiming,
        MAX_CONVERSATION_ITEM_CHARS,
        Turn,
        finalize_turns,
        flatten_turns,
        serialize_conversation,
        validate_channel_map,
    )

RESOURCE_NAME = "charter-stt-pii-resource"
SPEECH_ENDPOINT = f"https://{RESOURCE_NAME}.cognitiveservices.azure.com"
RESOURCE_ID = (
    "/subscriptions/fd918039-a89e-49a7-8e32-af614b3765f9"
    "/resourceGroups/rg-charter-stt-pii"
    f"/providers/Microsoft.CognitiveServices/accounts/{RESOURCE_NAME}"
)

VOICE_LIVE_URL = (
    f"wss://{RESOURCE_NAME}.services.ai.azure.com/voice-live/realtime"
    "?api-version=2026-04-10&model=gpt-4.1"
)
FAST_TRANSCRIPTION_API_VERSION = "2025-10-15"

CHUNK_SECONDS = 0.1  # audio is streamed to Voice Live in 100 ms frames

# Local VAD, tuned against this call: 103 segments, median 2.6s, which is the same
# ballpark as the 73 utterances the Speech SDK finds on its own.
VAD_FRAME_SECONDS = 0.02
VAD_RMS_THRESHOLD = 150
VAD_MIN_SILENCE_SECONDS = 0.5
VAD_MAX_UTTERANCE_SECONDS = 20.0

DATA_DIR = Path(__file__).parent
AUDIO_PATH = DATA_DIR / "mock-call.wav"
TRANSCRIPT_PATH = DATA_DIR / "mock-call-transcript.txt"
RESULTS_PATH = DATA_DIR / "stt-benchmark-results.json"

SPEAKER_RE = re.compile(r"^(REP|CUSTOMER):\s*(.*)$")
# Uploaded transcripts use their own speaker labels ("Agent:", "Caller 2:"), which
# are formatting rather than spoken words and must not be scored.
GENERIC_SPEAKER_RE = re.compile(r"^([A-Za-z][\w'-]*(?:[ ][\w'-]+){0,2}):\s+(.*)$")
DIRECTION_RE = re.compile(r"\[[^\]]*\]")

# Spoken-form expansions so scoring compares words, not formatting choices.
NORMALIZATIONS = [
    (r"\bmr\b", "mister"),
    (r"\bms\b", "miss"),
    (r"\bmrs\b", "missus"),
    (r"\bdr\b", "doctor"),
    (r"\bok\b", "okay"),
    (r"\bwi fi\b", "wifi"),
    (r"\bwi-fi\b", "wifi"),
    (r"\be[- ]mail\b", "email"),
    (r"\bam\b", "a m"),
    (r"\bpm\b", "p m"),
    (r"\$(\d+)", r"\1 dollars"),
    (r"%", " percent"),
]

# The engines apply different inverse text normalization ("three hundred" vs "300",
# "eleventh" vs "11th", "4111 1111" vs "4111111"). Numbers are folded into a single
# canonical digit form on both sides so WER reflects recognition quality rather than
# formatting style.
UNITS = {
    "zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
ORDINALS = {
    "first": "one", "second": "two", "third": "three", "fourth": "four",
    "fifth": "five", "sixth": "six", "seventh": "seven", "eighth": "eight",
    "ninth": "nine", "tenth": "ten", "eleventh": "eleven", "twelfth": "twelve",
    "thirteenth": "thirteen", "fourteenth": "fourteen", "fifteenth": "fifteen",
    "sixteenth": "sixteen", "seventeenth": "seventeen", "eighteenth": "eighteen",
    "nineteenth": "nineteen", "twentieth": "twenty", "thirtieth": "thirty",
}


# --------------------------------------------------------------------------- #
# Reference transcript + scoring
# --------------------------------------------------------------------------- #
def reference_text(path: Path = TRANSCRIPT_PATH) -> str:
    """Reference = exactly the words that were fed to TTS, in order.

    The mock call is speaker-labelled (`REP:` / `CUSTOMER:`), and so are most real
    call transcripts. When any speaker labels are present only labelled lines are
    scored, so headers and section markers stay out of the reference. A transcript
    with no labels at all is taken as plain prose instead.
    """
    lines = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    labelled = [
        match
        for match in (SPEAKER_RE.match(line) or GENERIC_SPEAKER_RE.match(line)
                      for line in lines)
        if match
    ]

    if labelled:
        spoken = [match.group(2) for match in labelled]
    else:
        spoken = lines

    return " ".join(DIRECTION_RE.sub(" ", text) for text in spoken)


def reference_by_participant(path: Path = TRANSCRIPT_PATH) -> dict[str, str]:
    """Return labelled reference text grouped by participant."""
    grouped: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = SPEAKER_RE.match(line.strip()) or GENERIC_SPEAKER_RE.match(line.strip())
        if match:
            grouped.setdefault(match.group(1), []).append(
                DIRECTION_RE.sub(" ", match.group(2))
            )
    return {participant: " ".join(parts) for participant, parts in grouped.items()}


def words_to_digits(tokens: list[str]) -> list[str]:
    """Convert spoken number words into digit tokens (e.g. 'eighty two' -> '82')."""
    out: list[str] = []
    index = 0
    while index < len(tokens):
        token = ORDINALS.get(tokens[index], tokens[index])
        if token not in UNITS and token not in TENS:
            out.append(tokens[index])
            index += 1
            continue

        value = TENS[token] if token in TENS else UNITS[token]
        index += 1
        if token in TENS and index < len(tokens):
            nxt = ORDINALS.get(tokens[index], tokens[index])
            if nxt in UNITS and UNITS[nxt] < 10:
                value += UNITS[nxt]
                index += 1
        while index < len(tokens) and tokens[index] in ("hundred", "thousand"):
            value *= 100 if tokens[index] == "hundred" else 1000
            index += 1
        out.append(str(value))
    return out


def fold_numbers(text: str) -> str:
    """Digitize number words, drop ordinal suffixes, and join adjacent digit runs."""
    tokens = words_to_digits(text.split())
    tokens = [re.sub(r"^(\d+)(st|nd|rd|th)$", r"\1", t) for t in tokens]

    folded: list[str] = []
    for token in tokens:
        if token.isdigit() and folded and folded[-1].isdigit():
            folded[-1] += token
        else:
            folded.append(token)
    return " ".join(folded)


def normalize(text: str) -> str:
    """Lowercase, expand abbreviations, drop punctuation, canonicalize numbers."""
    text = text.lower().replace("\n", " ")
    text = re.sub(r"(?<=\d),(?=\d)", "", text)  # 1,000 -> 1000
    for pattern, replacement in NORMALIZATIONS:
        text = re.sub(pattern, replacement, text)
    text = text.translate(str.maketrans({c: " " for c in string.punctuation}))
    text = re.sub(r"\s+", " ", text).strip()
    return fold_numbers(text)


def score(reference: str, hypothesis: str) -> dict:
    measures = jiwer.process_words(normalize(reference), normalize(hypothesis))
    return {
        "wer": measures.wer,
        "accuracy": 1.0 - measures.wer,
        "substitutions": measures.substitutions,
        "deletions": measures.deletions,
        "insertions": measures.insertions,
        "hits": measures.hits,
    }


def lag_stats(lags: list[float]) -> dict:
    """Finalization lag = delay from end of an utterance's audio to its transcript."""
    if not lags:
        return {"mean": None, "median": None, "p95": None, "max": None, "count": 0}
    ordered = sorted(lags)
    return {
        "mean": statistics.fmean(lags),
        "median": statistics.median(lags),
        "p95": ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))],
        "max": ordered[-1],
        "count": len(lags),
    }


def get_token() -> str:
    credential = DefaultAzureCredential()
    return credential.get_token("https://cognitiveservices.azure.com/.default").token


def load_audio(path: Path = AUDIO_PATH) -> tuple[list[bytes], int]:
    """Load mono/stereo PCM and return one synchronized mono byte stream per channel."""
    with wave.open(str(path)) as wav:
        channel_count = wav.getnchannels()
        if channel_count not in (1, 2) or wav.getsampwidth() != 2:
            raise ValueError(
                f"{path.name}: expected 16-bit mono/stereo PCM WAV, got "
                f"{channel_count} channel(s) / {wav.getsampwidth() * 8}-bit"
            )
        raw = wav.readframes(wav.getnframes())
        sample_rate = wav.getframerate()

    if channel_count == 1:
        return [raw], sample_rate

    samples = array.array("h")
    samples.frombytes(raw)
    if sys.byteorder != "little":
        samples.byteswap()
    channels = []
    for channel in range(channel_count):
        mono = array.array("h", samples[channel::channel_count])
        if sys.byteorder != "little":
            mono.byteswap()
        channels.append(mono.tobytes())
    return channels, sample_rate


def wav_bytes(pcm: bytes, sample_rate: int) -> bytes:
    output = BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return output.getvalue()


# --------------------------------------------------------------------------- #
# Local VAD - shared utterance boundaries
# --------------------------------------------------------------------------- #
def find_utterances(pcm: bytes, sample_rate: int) -> list[tuple[float, float]]:
    """Return [(start_seconds, end_seconds), ...] for speech separated by pauses.

    Commits are aligned to these boundaries so MAI is chunked the way the Speech
    SDK's own endpointing would chunk it, instead of on an arbitrary clock.
    """
    samples = array.array("h")
    samples.frombytes(pcm)
    frame = int(sample_rate * VAD_FRAME_SECONDS)

    utterances: list[tuple[float, float]] = []
    start_frame: int | None = None
    silent_run = 0
    min_silent_frames = int(VAD_MIN_SILENCE_SECONDS / VAD_FRAME_SECONDS)

    for index in range(len(samples) // frame):
        window = samples[index * frame : (index + 1) * frame]
        rms = math.sqrt(sum(s * s for s in window) / frame)

        if rms > VAD_RMS_THRESHOLD:
            if start_frame is None:
                start_frame = index
            silent_run = 0
            # Force a boundary so one long monologue cannot stall the stream.
            if (index - start_frame) * VAD_FRAME_SECONDS >= VAD_MAX_UTTERANCE_SECONDS:
                utterances.append(
                    (start_frame * VAD_FRAME_SECONDS, index * VAD_FRAME_SECONDS)
                )
                start_frame = None
        elif start_frame is not None:
            silent_run += 1
            if silent_run >= min_silent_frames:
                end = (index - silent_run) * VAD_FRAME_SECONDS
                utterances.append((start_frame * VAD_FRAME_SECONDS, end))
                start_frame = None
                silent_run = 0

    if start_frame is not None:
        total = (len(samples) // frame) * VAD_FRAME_SECONDS
        utterances.append((start_frame * VAD_FRAME_SECONDS, total))
    return utterances


def _bounded_sentences(text: str) -> list[str]:
    """Split display text at sentence/word boundaries under the PII item limit."""
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", text.strip())
        if sentence.strip()
    ]
    bounded: list[str] = []
    for sentence in sentences:
        while len(sentence) > MAX_CONVERSATION_ITEM_CHARS:
            cut = sentence.rfind(" ", 0, MAX_CONVERSATION_ITEM_CHARS + 1)
            if cut <= 0:
                cut = MAX_CONVERSATION_ITEM_CHARS
            bounded.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if sentence:
            bounded.append(sentence)
    return bounded


def align_batch_text_to_vad(
    text: str,
    pcm: bytes,
    sample_rate: int,
    channel: int,
    participant: str,
) -> list[Turn]:
    """Estimate phrase timing by aligning sentence word mass to local speech spans.

    MAI batch currently returns one full-duration phrase per mono channel. Local VAD
    supplies real speech intervals without changing the one-request transcription
    architecture; sentence positions are mapped monotonically onto those intervals.
    """
    sentences = _bounded_sentences(text)
    utterances = find_utterances(pcm, sample_rate)
    if not sentences:
        return []
    if not utterances:
        duration = len(pcm) / (sample_rate * 2)
        utterances = [(0.0, duration)]

    sentence_words = [max(1, len(sentence.split())) for sentence in sentences]
    total_words = sum(sentence_words)
    speech_durations = [max(0.001, end - start) for start, end in utterances]
    total_speech = sum(speech_durations)

    groups: dict[int, list[str]] = {}
    words_seen = 0
    utterance_index = 0
    speech_seen = speech_durations[0]
    for sentence, word_count in zip(sentences, sentence_words):
        midpoint_ratio = (words_seen + word_count / 2) / total_words
        target_speech = midpoint_ratio * total_speech
        while (
            utterance_index < len(utterances) - 1
            and target_speech > speech_seen
        ):
            utterance_index += 1
            speech_seen += speech_durations[utterance_index]
        groups.setdefault(utterance_index, []).append(sentence)
        words_seen += word_count

    turns: list[Turn] = []
    for index, parts in groups.items():
        chunks: list[str] = []
        current = ""
        for part in parts:
            candidate = f"{current} {part}".strip()
            if current and len(candidate) > MAX_CONVERSATION_ITEM_CHARS:
                chunks.append(current)
                current = part
            else:
                current = candidate
        if current:
            chunks.append(current)

        start, end = utterances[index]
        span = (end - start) / len(chunks)
        for chunk_index, chunk in enumerate(chunks):
            chunk_start = start + chunk_index * span
            turns.append(
                Turn(
                    participant_id=participant,
                    channel=channel,
                    offset=int(chunk_start * 10_000_000),
                    duration=int(span * 10_000_000),
                    text=chunk,
                )
            )
    return turns


# --------------------------------------------------------------------------- #
# Architecture 1 - Azure Speech real-time (Speech SDK continuous recognition)
# --------------------------------------------------------------------------- #
class ThrottledPcmStream(speechsdk.audio.PullAudioInputStreamCallback):
    """Feeds PCM to the Speech SDK at 1x.

    A file-based `AudioConfig` lets the SDK consume audio as fast as it can (~2x
    here), which makes streaming latency meaningless. Pacing the pull stream to
    wall-clock time reproduces a real-time call.
    """

    def __init__(self, pcm: bytes, bytes_per_second: int) -> None:
        super().__init__()
        self._pcm = pcm
        self._bytes_per_second = bytes_per_second
        self._position = 0
        self._start: float | None = None

    def read(self, buffer: memoryview) -> int:
        if self._start is None:
            self._start = time.perf_counter()
        size = min(len(buffer), len(self._pcm) - self._position)
        if size <= 0:
            return 0

        end = self._position + size
        delay = (self._start + end / self._bytes_per_second) - time.perf_counter()
        if delay > 0:
            time.sleep(delay)

        buffer[:size] = self._pcm[self._position : end]
        self._position = end
        return size

    def close(self) -> None:
        return None


def _speech_turn(
    result: speechsdk.SpeechRecognitionResult, channel: int, participant: str
) -> Turn:
    lexical = itn = masked_itn = None
    timings: tuple[AudioTiming, ...] = ()
    try:
        payload = json.loads(result.json)
        best = payload.get("NBest", [{}])[0]
        lexical = best.get("Lexical")
        itn = best.get("ITN")
        masked_itn = best.get("MaskedITN")
        timings = tuple(
            AudioTiming(
                word=word.get("Word", ""),
                offset=int(word.get("Offset", 0)),
                duration=int(word.get("Duration", 0)),
            )
            for word in best.get("Words", [])
            if word.get("Word")
        )
    except (json.JSONDecodeError, IndexError, TypeError):
        pass
    return Turn(
        participant_id=participant,
        channel=channel,
        offset=result.offset,
        duration=result.duration,
        text=result.text,
        lexical=lexical,
        itn=itn,
        masked_itn=masked_itn,
        audio_timings=timings,
    )


def _run_azure_speech_channel(
    token: str,
    pcm: bytes,
    sample_rate: int,
    channel: int,
    participant: str,
) -> tuple[list[Turn], list[float], float]:
    speech_config = speechsdk.SpeechConfig(endpoint=SPEECH_ENDPOINT)
    speech_config.authorization_token = f"aad#{RESOURCE_ID}#{token}"
    speech_config.speech_recognition_language = "en-US"
    speech_config.output_format = speechsdk.OutputFormat.Detailed
    speech_config.request_word_level_timestamps()

    stream_format = speechsdk.audio.AudioStreamFormat(
        samples_per_second=sample_rate, bits_per_sample=16, channels=1
    )
    callback = ThrottledPcmStream(pcm, sample_rate * 2)
    pull_stream = speechsdk.audio.PullAudioInputStream(callback, stream_format)
    audio_config = speechsdk.audio.AudioConfig(stream=pull_stream)
    recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config, audio_config=audio_config
    )

    turns: list[Turn] = []
    lags: list[float] = []
    finished: list[bool] = []
    errors: list[str] = []
    start = time.perf_counter()

    def on_recognized(evt: speechsdk.SpeechRecognitionEventArgs) -> None:
        if evt.result.reason != speechsdk.ResultReason.RecognizedSpeech:
            return
        if not evt.result.text:
            return
        turns.append(_speech_turn(evt.result, channel, participant))
        # offset/duration are in 100-nanosecond ticks from the start of the stream,
        # so this is the lag from the end of the utterance's audio.
        audio_end = (evt.result.offset + evt.result.duration) / 1e7
        lags.append(max(0.0, (time.perf_counter() - start) - audio_end))

    def on_canceled(evt: speechsdk.SpeechRecognitionCanceledEventArgs) -> None:
        if evt.reason == speechsdk.CancellationReason.Error:
            errors.append(evt.error_details or "Speech SDK canceled")
        finished.append(True)

    recognizer.recognized.connect(on_recognized)
    recognizer.canceled.connect(on_canceled)
    recognizer.session_stopped.connect(lambda _: finished.append(True))

    recognizer.start_continuous_recognition()
    deadline = start + len(pcm) / (sample_rate * 2) + 120
    while not finished and time.perf_counter() < deadline:
        time.sleep(0.2)
    recognizer.stop_continuous_recognition()
    if not finished:
        raise TimeoutError(
            f"Speech SDK channel {channel} did not stop after the audio ended."
        )
    if errors:
        raise RuntimeError(f"Speech SDK channel {channel}: {errors[0]}")

    wall = time.perf_counter() - start
    return turns, lags, wall


def run_azure_speech_realtime(
    token: str,
    channels: list[bytes],
    sample_rate: int,
    channel_map: dict[int, str],
) -> tuple[list[Turn], dict]:
    with ThreadPoolExecutor(max_workers=len(channels)) as pool:
        futures = [
            pool.submit(
                _run_azure_speech_channel,
                token,
                pcm,
                sample_rate,
                channel,
                channel_map[channel],
            )
            for channel, pcm in enumerate(channels)
        ]
        outputs = [future.result() for future in futures]

    turns = finalize_turns(turn for output in outputs for turn in output[0])
    lags = [lag for output in outputs for lag in output[1]]
    wall = max(output[2] for output in outputs)
    metrics = {
        "mode": "real-time (incremental)",
        "wall_seconds": wall,
        # Transcription overlaps the call, so the transcript is done moments after
        # the caller stops talking.
        "time_to_full_transcript": wall,
        "finalization_lag": lag_stats(lags),
        "segments": len(turns),
        "channel_sessions": len(channels),
    }
    return turns, metrics


# --------------------------------------------------------------------------- #
# Architecture 2 STT - MAI-Transcribe-1.5 real-time (Voice Live, VAD-aligned commits)
# --------------------------------------------------------------------------- #
async def _run_mai_realtime_channel(
    token: str,
    pcm: bytes,
    sample_rate: int,
    channel: int,
    participant: str,
) -> tuple[list[Turn], list[float], int, float]:
    bytes_per_second = sample_rate * 2
    chunk_bytes = int(bytes_per_second * CHUNK_SECONDS)
    utterances = find_utterances(pcm, sample_rate)
    commit_at = [end for _, end in utterances]

    turns: list[Turn] = []
    lags: list[float] = []
    pending: list[tuple[float, float, float]] = []
    fatal_errors: list[str] = []
    start = time.perf_counter()

    async with websockets.connect(
        VOICE_LIVE_URL,
        additional_headers={"Authorization": f"Bearer {token}"},
        max_size=None,
        ping_interval=20,
        ping_timeout=60,
    ) as socket:
        await socket.recv()  # session.created
        await socket.send(
            json.dumps(
                {
                    "type": "session.update",
                    "session": {
                        "modalities": ["text"],
                        "input_audio_format": "pcm16",
                        "input_audio_transcription": {"model": "mai-transcribe"},
                        "turn_detection": None,
                    },
                }
            )
        )

        async def reader() -> None:
            async for raw in socket:
                event = json.loads(raw)
                event_type = event.get("type", "")
                if event_type == "error":
                    message = event.get("error", {}).get("message", "Voice Live error")
                    if "buffer too small" in message:
                        if pending:
                            pending.pop(0)
                    else:
                        fatal_errors.append(message)
                        return
                elif event_type.endswith("input_audio_transcription.completed"):
                    if pending:
                        committed_at, utterance_start, utterance_end = pending.pop(0)
                        lags.append(max(0.0, time.perf_counter() - committed_at))
                        if event.get("transcript"):
                            turns.append(
                                Turn(
                                    participant_id=participant,
                                    channel=channel,
                                    offset=int(utterance_start * 10_000_000),
                                    duration=int(
                                        (utterance_end - utterance_start) * 10_000_000
                                    ),
                                    text=event["transcript"],
                                )
                            )

        reader_task = asyncio.create_task(reader())

        # Stream at 1x, committing when each utterance's audio has been sent.
        next_commit = 0
        next_send = start
        for offset in range(0, len(pcm), chunk_bytes):
            if fatal_errors:
                break
            if reader_task.done():
                await reader_task
                raise RuntimeError(
                    f"Voice Live channel {channel} reader stopped unexpectedly."
                )
            chunk = pcm[offset : offset + chunk_bytes]
            await socket.send(
                json.dumps(
                    {
                        "type": "input_audio_buffer.append",
                        "audio": base64.b64encode(chunk).decode(),
                    }
                )
            )

            sent_seconds = (offset + len(chunk)) / bytes_per_second
            if next_commit < len(commit_at) and sent_seconds >= commit_at[next_commit]:
                await socket.send(json.dumps({"type": "input_audio_buffer.commit"}))
                utterance_start, utterance_end = utterances[next_commit]
                pending.append(
                    (time.perf_counter(), utterance_start, utterance_end)
                )
                next_commit += 1

            next_send += CHUNK_SECONDS
            await asyncio.sleep(max(0.0, next_send - time.perf_counter()))

        deadline = time.perf_counter() + 60
        while (
            pending
            and not fatal_errors
            and not reader_task.done()
            and time.perf_counter() < deadline
        ):
            await asyncio.sleep(0.2)
        if reader_task.done():
            await reader_task
        else:
            reader_task.cancel()
            try:
                await reader_task
            except asyncio.CancelledError:
                pass
        if fatal_errors:
            raise RuntimeError(f"Voice Live channel {channel}: {fatal_errors[0]}")
        if pending:
            raise TimeoutError(
                f"Voice Live channel {channel} left {len(pending)} commit(s) pending."
            )

    wall = time.perf_counter() - start
    return turns, lags, len(commit_at), time.perf_counter() - start


async def run_mai_realtime(
    token: str,
    channels: list[bytes],
    sample_rate: int,
    channel_map: dict[int, str],
) -> tuple[list[Turn], dict]:
    outputs = await asyncio.gather(
        *(
            _run_mai_realtime_channel(
                token, pcm, sample_rate, channel, channel_map[channel]
            )
            for channel, pcm in enumerate(channels)
        )
    )
    turns = finalize_turns(turn for output in outputs for turn in output[0])
    lags = [lag for output in outputs for lag in output[1]]
    wall = max(output[3] for output in outputs)
    metrics = {
        "mode": "real-time (utterance micro-batch)",
        "wall_seconds": wall,
        "time_to_full_transcript": wall,
        "finalization_lag": lag_stats(lags),
        "segments": len(turns),
        "utterances_committed": sum(output[2] for output in outputs),
        "channel_sessions": len(channels),
    }
    return turns, metrics


# --------------------------------------------------------------------------- #
# Architecture 3 STT - MAI-Transcribe-1.5 batch (post-call VAD utterances)
# --------------------------------------------------------------------------- #
def _post_with_retry(*args, **kwargs) -> requests.Response:
    """Retry only transient transport, throttling, and server failures."""
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = requests.post(*args, **kwargs)
            if response.status_code != 429 and response.status_code < 500:
                return response
            last_error = requests.HTTPError(
                f"Transient transcription response: {response.status_code}",
                response=response,
            )
            delay = float(response.headers.get("Retry-After", 2**attempt))
            response.close()
        except (requests.ConnectionError, requests.Timeout) as error:
            last_error = error
            delay = 2**attempt

        files = kwargs.get("files", {})
        for value in files.values():
            if isinstance(value, tuple) and len(value) > 1 and hasattr(value[1], "seek"):
                value[1].seek(0)
        if attempt < 2:
            time.sleep(delay)

    assert last_error is not None
    raise last_error


def _run_mai_batch_clip(
    token: str,
    pcm: bytes,
    sample_rate: int,
    channel: int,
    participant: str,
) -> tuple[list[Turn], float, bool]:
    temporary = tempfile.TemporaryDirectory()
    audio_path = Path(temporary.name) / f"channel-{channel}.wav"
    audio_path.write_bytes(wav_bytes(pcm, sample_rate))
    url = (
        f"{SPEECH_ENDPOINT}/speechtotext/transcriptions:transcribe"
        f"?api-version={FAST_TRANSCRIPTION_API_VERSION}"
    )
    definition = {
        "locales": ["en"],
        "enhancedMode": {
            "enabled": True,
            "model": "mai-transcribe-1.5",
            "transcribeStyle": "verbatim",
        },
    }

    with audio_path.open("rb") as audio:
        files = {
            "audio": (audio_path.name, audio, "audio/wav"),
            "definition": (None, json.dumps(definition), "application/json"),
        }
        start = time.perf_counter()
        response = _post_with_retry(
            url, headers={"Authorization": f"Bearer {token}"}, files=files, timeout=900
        )
        latency = time.perf_counter() - start
    temporary.cleanup()

    response.raise_for_status()
    payload = response.json()
    phrases = [phrase for phrase in payload.get("phrases", []) if phrase.get("text")]
    needs_estimated_timing = not phrases or any(
        len(phrase["text"]) > MAX_CONVERSATION_ITEM_CHARS for phrase in phrases
    )
    if needs_estimated_timing:
        text = " ".join(
            phrase["text"] for phrase in payload.get("combinedPhrases", [])
        ) or " ".join(phrase["text"] for phrase in phrases)
        turns = align_batch_text_to_vad(
            text, pcm, sample_rate, channel, participant
        )
    else:
        turns = [
            Turn(
                participant_id=participant,
                channel=channel,
                offset=int(phrase.get("offsetMilliseconds", 0)) * 10_000,
                duration=int(phrase.get("durationMilliseconds", 0)) * 10_000,
                text=phrase["text"],
            )
            for phrase in phrases
        ]
    return turns, latency, needs_estimated_timing


def _run_mai_batch_channel(
    token: str,
    pcm: bytes,
    sample_rate: int,
    channel: int,
    participant: str,
) -> tuple[list[Turn], float, int]:
    """Transcribe post-call VAD utterances concurrently to preserve real turn timing."""
    utterances = find_utterances(pcm, sample_rate)
    start = time.perf_counter()

    def transcribe(index: int, bounds: tuple[float, float]) -> Turn | None:
        utterance_start, utterance_end = bounds
        byte_start = int(utterance_start * sample_rate) * 2
        byte_end = int(utterance_end * sample_rate) * 2
        clip_turns, _, _ = _run_mai_batch_clip(
            token,
            pcm[byte_start:byte_end],
            sample_rate,
            channel,
            participant,
        )
        text = flatten_turns(clip_turns)
        if not text:
            return None
        return Turn(
            participant_id=participant,
            channel=channel,
            offset=int(utterance_start * 10_000_000),
            duration=int((utterance_end - utterance_start) * 10_000_000),
            text=text,
        )

    with ThreadPoolExecutor(max_workers=min(4, max(1, len(utterances)))) as pool:
        futures = [
            pool.submit(transcribe, index, bounds)
            for index, bounds in enumerate(utterances)
        ]
        turns = [turn for future in futures if (turn := future.result()) is not None]
    return turns, time.perf_counter() - start, len(utterances)


def run_mai_batch(
    token: str,
    channels: list[bytes],
    sample_rate: int,
    channel_map: dict[int, str],
    audio_seconds: float = 0.0,
) -> tuple[list[Turn], dict]:
    with ThreadPoolExecutor(max_workers=len(channels)) as pool:
        futures = [
            pool.submit(
                _run_mai_batch_channel,
                token,
                pcm,
                sample_rate,
                channel,
                channel_map[channel],
            )
            for channel, pcm in enumerate(channels)
        ]
        outputs = [future.result() for future in futures]
    turns = finalize_turns(turn for output in outputs for turn in output[0])
    latency = max(output[1] for output in outputs)
    metrics = {
        "mode": "batch (post-call VAD utterances)",
        "wall_seconds": latency,
        "turnaround_seconds": latency,
        # Batch cannot start until the caller hangs up, so from the moment the call
        # begins the transcript is only ready after the full call plus turnaround.
        "time_to_full_transcript": audio_seconds + latency,
        "finalization_lag": lag_stats([]),
        "segments": len(turns),
        "channel_sessions": len(channels),
        "utterance_requests": sum(output[2] for output in outputs),
        "timing_estimated": False,
    }
    return turns, metrics


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
ENGINES = {
    "architecture-1-azure-speech-realtime": "1. Azure Speech real-time (SDK)",
    "architecture-2-mai-transcribe-realtime": (
        "2. MAI-Transcribe-1.5 real-time (Voice Live)"
    ),
    "architecture-3-mai-transcribe-batch": (
        "3. MAI-Transcribe-1.5 batch (VAD utterances)"
    ),
}


async def _run_all(
    token: str,
    channels: list[bytes],
    sample_rate: int,
    channel_map: dict[int, str],
    audio_seconds: float = 0.0,
    on_progress=None,
) -> dict:
    """Run all three architectures concurrently - they are independent sessions."""
    loop = asyncio.get_running_loop()

    async def tracked(key, awaitable):
        if on_progress:
            on_progress(key, "running")
        try:
            result = await awaitable
        except Exception as error:  # one engine failing must not sink the others
            if on_progress:
                on_progress(key, "failed")
            return error
        if on_progress:
            on_progress(key, "done")
        return result

    async def post_call_batch():
        # The file is available immediately in a benchmark, but production batch
        # cannot begin until the call has ended.
        await asyncio.sleep(audio_seconds)
        return await loop.run_in_executor(
            None,
            run_mai_batch,
            token,
            channels,
            sample_rate,
            channel_map,
            audio_seconds,
        )

    keys = list(ENGINES)
    speech, mai_realtime, mai_batch = await asyncio.gather(
        tracked(
            keys[0],
            loop.run_in_executor(
                None,
                run_azure_speech_realtime,
                token,
                channels,
                sample_rate,
                channel_map,
            ),
        ),
        tracked(
            keys[1],
            run_mai_realtime(token, channels, sample_rate, channel_map),
        ),
        tracked(
            keys[2],
            post_call_batch(),
        ),
    )
    outcomes = [speech, mai_realtime, mai_batch]
    return {key: (ENGINES[key], outcome) for key, outcome in zip(keys, outcomes)}


def run_benchmark(
    audio_path: Path = AUDIO_PATH,
    reference_path: Path | None = TRANSCRIPT_PATH,
    on_progress=None,
    channel_map: dict[int | str, str] | None = None,
) -> dict:
    """Benchmark all three architectures against `audio_path`.

    `reference_path` is optional: without it the transcripts and latency metrics are
    still produced, but there is no reference to score word error rate against.
    """
    reference = reference_text(reference_path) if reference_path else None
    participant_reference = (
        reference_by_participant(reference_path) if reference_path else {}
    )
    channels, sample_rate = load_audio(audio_path)
    audio_seconds = len(channels[0]) / (sample_rate * 2)
    default_map = (
        {0: "speaker"}
        if len(channels) == 1
        else {0: "REP", 1: "CUSTOMER"}
    )
    normalized_map = validate_channel_map(
        channel_map or default_map, len(channels)
    )
    utterances_by_channel = {
        channel: len(find_utterances(pcm, sample_rate))
        for channel, pcm in enumerate(channels)
    }

    token = get_token()
    runs = asyncio.run(
        _run_all(
            token,
            channels,
            sample_rate,
            normalized_map,
            audio_seconds,
            on_progress,
        )
    )

    results = {}
    for key, (label, outcome) in runs.items():
        if isinstance(outcome, Exception):
            results[key] = {
                "label": label,
                "error": str(outcome),
                "transcript": "",
                "conversation": None,
            }
            continue
        turns, metrics = outcome
        hypothesis = flatten_turns(turns)
        if reference is not None:
            metrics.update(score(reference, hypothesis))
            participant_metrics = {}
            for channel, participant in normalized_map.items():
                participant_hypothesis = flatten_turns(
                    turn for turn in turns if turn.channel == channel
                )
                if participant in participant_reference:
                    participant_metrics[participant] = score(
                        participant_reference[participant], participant_hypothesis
                    )
            if participant_metrics:
                metrics["participants"] = participant_metrics
        metrics["word_count"] = len(normalize(hypothesis).split())
        conversation = serialize_conversation(
            turns,
            normalized_map,
            conversation_id=f"{audio_path.stem}-{key}",
            speaker_attributed=len(channels) > 1,
        )
        results[key] = {
            "label": label,
            "metrics": metrics,
            "transcript": hypothesis,
            "conversation": conversation,
        }

    return {
        "audio_seconds": audio_seconds,
        "channel_count": len(channels),
        "channel_map": {
            str(channel): participant
            for channel, participant in normalized_map.items()
        },
        "speaker_attributed": len(channels) > 1,
        "vad_utterances": sum(utterances_by_channel.values()),
        "vad_utterances_by_channel": {
            str(channel): count
            for channel, count in utterances_by_channel.items()
        },
        "reference_words": (
            len(normalize(reference).split()) if reference is not None else None
        ),
        "scored": reference is not None,
        "engines": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", type=Path, default=AUDIO_PATH)
    parser.add_argument("--reference", type=Path, default=TRANSCRIPT_PATH)
    parser.add_argument("--channel-0", default=None)
    parser.add_argument("--channel-1", default=None)
    parser.add_argument("--results", type=Path, default=None)
    args = parser.parse_args()

    channels, sample_rate = load_audio(args.audio)
    audio_seconds = len(channels[0]) / (sample_rate * 2)
    channel_map = None
    if args.channel_0:
        channel_map = {0: args.channel_0}
        if len(channels) == 2:
            channel_map[1] = args.channel_1 or "CUSTOMER"

    print(
        f"Audio:      {args.audio.name} "
        f"({audio_seconds:.1f}s, {len(channels)} channel(s))"
    )
    print(
        f"Running all 3 architectures concurrently "
        f"(~{audio_seconds / 60:.0f} min, bounded by 1x streaming)...\n"
    )

    report = run_benchmark(
        args.audio,
        args.reference,
        channel_map=channel_map,
    )
    results = report["engines"]

    print(f"Reference:  {report['reference_words']} words")
    print(
        f"Local VAD:  {report['vad_utterances']} utterances "
        f"across {report['channel_count']} channel(s)\n"
    )

    prefix = "" if args.audio == AUDIO_PATH else f"{args.audio.stem}-"
    for key, entry in results.items():
        transcript = entry.get("transcript", "")
        if transcript:
            (DATA_DIR / f"{prefix}transcript-{key}.txt").write_text(
                transcript, encoding="utf-8"
            )
        conversation = entry.get("conversation")
        if conversation:
            (DATA_DIR / f"{prefix}conversation-{key}.json").write_text(
                json.dumps(conversation, indent=2), encoding="utf-8"
            )

    header = (
        f"{'Architecture':<46}{'WER':>8}{'Accuracy':>10}"
        f"{'Mean lag':>10}{'p95 lag':>9}{'Transcript ready':>18}"
    )
    print(header)
    print("-" * len(header))
    for entry in results.values():
        if "error" in entry:
            print(f"{entry['label']:<46}{'failed: ' + entry['error']:>47}")
            continue
        m = entry["metrics"]
        lag = m["finalization_lag"]
        if lag["mean"] is None:
            latency = f"{m['turnaround_seconds']:>9.1f}s{'batch':>9}"
        else:
            latency = f"{lag['mean']:>9.2f}s{lag['p95']:>8.2f}s"
        print(
            f"{entry['label']:<46}{m['wer']:>7.2%}{m['accuracy']:>10.2%}"
            f"{latency}{m['time_to_full_transcript']:>17.1f}s"
        )
    print(
        "\n'Transcript ready' = seconds from the start of the call until the full "
        "transcript exists.\nReal-time overlaps the call; batch cannot start until "
        "the caller hangs up."
    )

    print()
    for entry in results.values():
        if "error" in entry:
            continue
        m = entry["metrics"]
        print(
            f"{entry['label']}: {m['mode']}, {m['substitutions']} sub / "
            f"{m['deletions']} del / {m['insertions']} ins"
        )

    # The transcripts are written alongside as `transcript-<architecture>.txt`.
    persisted = {
        key: {k: v for k, v in entry.items() if k != "transcript"}
        for key, entry in results.items()
    }
    results_path = args.results or (
        RESULTS_PATH
        if args.audio == AUDIO_PATH
        else DATA_DIR / f"{args.audio.stem}-stt-benchmark-results.json"
    )
    results_path.write_text(
        json.dumps({**report, "engines": persisted}, indent=2),
        encoding="utf-8",
    )
    print(f"\nWrote {results_path}")


if __name__ == "__main__":
    main()
