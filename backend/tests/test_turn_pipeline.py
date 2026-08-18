import array
import tempfile
import unittest
import wave
from pathlib import Path

from backend.app.audio import prepare_audio
from data.conversation import (
    MAX_CONVERSATION_ITEM_CHARS,
    Turn,
    conversation_pii_input,
    finalize_turns,
    serialize_conversation,
    validate_channel_map,
)
from data.stt import load_audio


def write_pcm(path: Path, channels: int) -> None:
    samples = array.array("h")
    for index in range(160):
        if channels == 1:
            samples.append(index)
        else:
            samples.extend((index, -index))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(samples.tobytes())


class AudioIntakeTests(unittest.TestCase):
    def test_stereo_pcm_is_preserved_and_split(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.wav"
            target = Path(directory) / "target.wav"
            write_pcm(source, 2)

            info = prepare_audio(source, target)
            channel_audio, sample_rate = load_audio(target)

            self.assertFalse(info.transcoded)
            self.assertEqual(info.channels, 2)
            self.assertEqual(sample_rate, 16_000)
            self.assertEqual(len(channel_audio), 2)
            left = array.array("h")
            left.frombytes(channel_audio[0])
            right = array.array("h")
            right.frombytes(channel_audio[1])
            self.assertEqual(left.tolist(), list(range(160)))
            self.assertEqual(right.tolist(), [-value for value in range(160)])


class ConversationTests(unittest.TestCase):
    def test_turns_are_sorted_and_overlaps_are_preserved(self) -> None:
        turns = finalize_turns(
            [
                Turn("CUSTOMER", 1, 20, 10, "Second"),
                Turn("REP", 0, 10, 30, "First overlaps second"),
            ]
        )
        self.assertEqual([turn.id for turn in turns], ["turn-0001", "turn-0002"])
        self.assertEqual([turn.participant_id for turn in turns], ["REP", "CUSTOMER"])

    def test_pii_projection_strips_benchmark_only_fields(self) -> None:
        conversation = serialize_conversation(
            [Turn("REP", 0, 0, 10, "Hello")],
            {0: "REP"},
            speaker_attributed=False,
        )
        projected = conversation_pii_input(conversation)
        item = projected["conversationItems"][0]
        self.assertNotIn("channel", item)
        self.assertNotIn("offset", item)
        self.assertEqual(item["participantId"], "REP")

    def test_conversation_item_limit_is_enforced(self) -> None:
        with self.assertRaisesRegex(ValueError, "Conversation PII"):
            finalize_turns(
                [Turn("REP", 0, 0, 10, "x" * (MAX_CONVERSATION_ITEM_CHARS + 1))]
            )

    def test_channel_map_requires_distinct_complete_labels(self) -> None:
        self.assertEqual(
            validate_channel_map({"0": "REP", "1": "CUSTOMER"}, 2),
            {0: "REP", 1: "CUSTOMER"},
        )
        with self.assertRaises(ValueError):
            validate_channel_map({0: "speaker", 1: "speaker"}, 2)


if __name__ == "__main__":
    unittest.main()
