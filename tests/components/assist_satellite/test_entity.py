"""Test the Assist Satellite entity."""

import asyncio
from collections.abc import Generator
from dataclasses import asdict
from unittest.mock import Mock, patch

import pytest

from homeassistant.components import stt
from homeassistant.components.assist_pipeline import (
    OPTION_PREFERRED,
    AudioSettings,
    Pipeline,
    PipelineEvent,
    PipelineEventType,
    PipelineStage,
    async_get_pipeline,
    async_update_pipeline,
    vad,
)
from homeassistant.components.assist_satellite import (
    AssistSatelliteAnnouncement,
    AssistSatelliteAnswer,
    SatelliteBusyError,
)
from homeassistant.components.assist_satellite.const import PREANNOUNCE_URL
from homeassistant.components.assist_satellite.entity import AssistSatelliteState
from homeassistant.components.media_source import PlayMedia
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from . import ENTITY_ID
from .conftest import MockAssistSatellite

from tests.components.tts.common import MockResultStream


@pytest.fixture
def mock_chat_session_conversation_id() -> Generator[Mock]:
    """Mock the ulid library."""
    with patch("homeassistant.helpers.chat_session.ulid_now") as mock_ulid_now:
        mock_ulid_now.return_value = "mock-conversation-id"
        yield mock_ulid_now


@pytest.fixture(autouse=True)
async def set_pipeline_tts(hass: HomeAssistant, init_components: ConfigEntry) -> None:
    """Set up a pipeline with a TTS engine."""
    await async_update_pipeline(
        hass,
        async_get_pipeline(hass),
        tts_engine="tts.mock_entity",
        tts_language="en",
        tts_voice="test-voice",
    )


async def test_entity_state(
    hass: HomeAssistant, init_components: ConfigEntry, entity: MockAssistSatellite
) -> None:
    """Test entity state represent events."""

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == AssistSatelliteState.IDLE

    context = Context()
    audio_stream = object()

    entity.async_set_context(context)

    with patch(
        "homeassistant.components.assist_satellite.entity.async_pipeline_from_audio_stream"
    ) as mock_start_pipeline:
        await entity.async_accept_pipeline_from_satellite(audio_stream)

    assert mock_start_pipeline.called
    kwargs = mock_start_pipeline.call_args[1]
    assert kwargs["context"] is context
    assert kwargs["event_callback"] == entity._internal_on_pipeline_event
    assert kwargs["stt_metadata"] == stt.SpeechMetadata(
        language="",
        format=stt.AudioFormats.WAV,
        codec=stt.AudioCodecs.PCM,
        bit_rate=stt.AudioBitRates.BITRATE_16,
        sample_rate=stt.AudioSampleRates.SAMPLERATE_16000,
        channel=stt.AudioChannels.CHANNEL_MONO,
    )
    assert kwargs["stt_stream"] is audio_stream
    assert kwargs["pipeline_id"] is None
    assert kwargs["device_id"] is entity.device_entry.id
    assert kwargs["tts_audio_output"] == {"test-option": "test-value"}
    assert kwargs["wake_word_phrase"] is None
    assert kwargs["audio_settings"] == AudioSettings(
        silence_seconds=vad.VadSensitivity.to_seconds(vad.VadSensitivity.DEFAULT)
    )
    assert kwargs["start_stage"] == PipelineStage.STT
    assert kwargs["end_stage"] == PipelineStage.TTS

    for event_type, event_data, expected_state in (
        (PipelineEventType.RUN_START, {}, AssistSatelliteState.IDLE),
        (PipelineEventType.RUN_END, {}, AssistSatelliteState.IDLE),
        (
            PipelineEventType.WAKE_WORD_START,
            {},
            AssistSatelliteState.IDLE,
        ),
        (PipelineEventType.WAKE_WORD_END, {}, AssistSatelliteState.IDLE),
        (PipelineEventType.STT_START, {}, AssistSatelliteState.LISTENING),
        (PipelineEventType.STT_VAD_START, {}, AssistSatelliteState.LISTENING),
        (PipelineEventType.STT_VAD_END, {}, AssistSatelliteState.LISTENING),
        (PipelineEventType.STT_END, {}, AssistSatelliteState.LISTENING),
        (PipelineEventType.INTENT_START, {}, AssistSatelliteState.PROCESSING),
        (
            PipelineEventType.INTENT_END,
            {
                "intent_output": {
                    "conversation_id": "mock-conversation-id",
                }
            },
            AssistSatelliteState.PROCESSING,
        ),
        (PipelineEventType.TTS_START, {}, AssistSatelliteState.RESPONDING),
        (PipelineEventType.TTS_END, {}, AssistSatelliteState.RESPONDING),
        (PipelineEventType.ERROR, {}, AssistSatelliteState.RESPONDING),
    ):
        kwargs["event_callback"](PipelineEvent(event_type, event_data))
        state = hass.states.get(ENTITY_ID)
        assert state.state == expected_state, event_type

    entity.tts_response_finished()
    state = hass.states.get(ENTITY_ID)
    assert state.state == AssistSatelliteState.IDLE


async def test_new_pipeline_cancels_pipeline(
    hass: HomeAssistant,
    init_components: ConfigEntry,
    entity: MockAssistSatellite,
) -> None:
    """Test that a new pipeline run cancels any running pipeline."""
    pipeline1_started = asyncio.Event()
    pipeline1_finished = asyncio.Event()
    pipeline1_cancelled = asyncio.Event()
    pipeline2_finished = asyncio.Event()

    async def async_pipeline_from_audio_stream(*args, **kwargs):
        if not pipeline1_started.is_set():
            # First pipeline run
            pipeline1_started.set()

            # Wait for pipeline to be cancelled
            try:
                await pipeline1_finished.wait()
            except asyncio.CancelledError:
                pipeline1_cancelled.set()
                raise
        else:
            # Second pipeline run
            pipeline2_finished.set()

    with (
        patch(
            "homeassistant.components.assist_satellite.entity.async_pipeline_from_audio_stream",
            new=async_pipeline_from_audio_stream,
        ),
    ):
        hass.async_create_task(
            entity.async_accept_pipeline_from_satellite(
                object(),  # type: ignore[arg-type]
            )
        )

        async with asyncio.timeout(1):
            await pipeline1_started.wait()

            # Start a second pipeline
            await entity.async_accept_pipeline_from_satellite(
                object(),  # type: ignore[arg-type]
            )
            await pipeline1_cancelled.wait()
            await pipeline2_finished.wait()


@pytest.mark.parametrize(
    ("service_data", "expected_params"),
    [
        (
            {"message": "Hello", "preannounce": False},
            AssistSatelliteAnnouncement(
                message="Hello",
                media_id="http://10.10.10.10:8123/api/tts_proxy/test-token",
                original_media_id="media-source://bla",
                tts_token="test-token",
                media_id_source="tts",
            ),
        ),
        (
            {
                "message": "Hello",
                "media_id": "media-source://given",
                "preannounce": False,
            },
            AssistSatelliteAnnouncement(
                message="Hello",
                media_id="https://www.home-assistant.io/resolved.mp3",
                original_media_id="media-source://given",
                tts_token=None,
                media_id_source="media_id",
            ),
        ),
        (
            {"media_id": "http://example.com/bla.mp3", "preannounce": False},
            AssistSatelliteAnnouncement(
                message="",
                media_id="http://example.com/bla.mp3",
                original_media_id="http://example.com/bla.mp3",
                tts_token=None,
                media_id_source="url",
            ),
        ),
        (
            {
                "media_id": "http://example.com/bla.mp3",
                "preannounce_media_id": "http://example.com/preannounce.mp3",
            },
            AssistSatelliteAnnouncement(
                message="",
                media_id="http://example.com/bla.mp3",
                original_media_id="http://example.com/bla.mp3",
                tts_token=None,
                media_id_source="url",
                preannounce_media_id="http://example.com/preannounce.mp3",
            ),
        ),
        (
            {
                "message": "Hello",
                "media_id": {
                    "media_content_id": "media-source://given",
                    "media_content_type": "provider",
                },
                "preannounce": False,
            },
            AssistSatelliteAnnouncement(
                message="Hello",
                media_id="https://www.home-assistant.io/resolved.mp3",
                original_media_id="media-source://given",
                tts_token=None,
                media_id_source="media_id",
            ),
        ),
        (
            {
                "media_id": {
                    "media_content_id": "http://example.com/bla.mp3",
                    "media_content_type": "audio",
                },
                "preannounce_media_id": {
                    "media_content_id": "http://example.com/preannounce.mp3",
                    "media_content_type": "audio",
                },
            },
            AssistSatelliteAnnouncement(
                message="",
                media_id="http://example.com/bla.mp3",
                original_media_id="http://example.com/bla.mp3",
                tts_token=None,
                media_id_source="url",
                preannounce_media_id="http://example.com/preannounce.mp3",
            ),
        ),
    ],
)
async def test_announce(
    hass: HomeAssistant,
    init_components: ConfigEntry,
    entity: MockAssistSatellite,
    service_data: dict,
    expected_params: tuple[str, str],
) -> None:
    """Test announcing on a device."""
    original_announce = entity.async_announce

    async def async_announce(announcement):
        # Verify state change
        assert entity.state == AssistSatelliteState.RESPONDING
        await original_announce(announcement)

    def tts_generate_media_source_id(
        hass: HomeAssistant,
        message: str,
        engine: str | None = None,
        language: str | None = None,
        options: dict | None = None,
        cache: bool | None = None,
    ):
        # Check that TTS options are passed here
        assert options == {"test-option": "test-value", "voice": "test-voice"}
        return "media-source://bla"

    with (
        patch(
            "homeassistant.components.tts.generate_media_source_id",
            new=tts_generate_media_source_id,
        ),
        patch(
            "homeassistant.components.tts.async_resolve_engine",
            return_value="tts.cloud",
        ),
        patch(
            "homeassistant.components.tts.async_create_stream",
            return_value=MockResultStream(hass, "wav", b""),
        ),
        patch(
            "homeassistant.components.media_source.async_resolve_media",
            return_value=PlayMedia(
                url="https://www.home-assistant.io/resolved.mp3",
                mime_type="audio/mp3",
            ),
        ),
        patch.object(entity, "async_announce", new=async_announce),
    ):
        await hass.services.async_call(
            "assist_satellite",
            "announce",
            service_data,
            target={"entity_id": "assist_satellite.test_entity"},
            blocking=True,
        )
        assert entity.state == AssistSatelliteState.IDLE

    assert entity.announcements[0] == expected_params


async def test_announce_busy(
    hass: HomeAssistant,
    init_components: ConfigEntry,
    entity: MockAssistSatellite,
) -> None:
    """Test that announcing while an announcement is in progress raises an error."""
    media_id = "https://www.home-assistant.io/resolved.mp3"
    announce_started = asyncio.Event()
    got_error = asyncio.Event()

    async def async_announce(announcement):
        announce_started.set()

        # Block so we can do another announcement
        await got_error.wait()

    with patch.object(entity, "async_announce", new=async_announce):
        announce_task = asyncio.create_task(
            entity.async_internal_announce(media_id=media_id)
        )
        async with asyncio.timeout(1):
            await announce_started.wait()

            # Try to do a second announcement
            with pytest.raises(SatelliteBusyError):
                await entity.async_internal_announce(media_id=media_id)

    # Avoid lingering task
    got_error.set()
    await announce_task


async def test_announce_cancels_pipeline(
    hass: HomeAssistant,
    init_components: ConfigEntry,
    entity: MockAssistSatellite,
) -> None:
    """Test that announcements cancel any running pipeline."""
    media_id = "https://www.home-assistant.io/resolved.mp3"
    pipeline_started = asyncio.Event()
    pipeline_finished = asyncio.Event()
    pipeline_cancelled = asyncio.Event()

    async def async_pipeline_from_audio_stream(*args, **kwargs):
        pipeline_started.set()

        # Wait for pipeline to be cancelled
        try:
            await pipeline_finished.wait()
        except asyncio.CancelledError:
            pipeline_cancelled.set()
            raise

    with (
        patch(
            "homeassistant.components.assist_satellite.entity.async_pipeline_from_audio_stream",
            new=async_pipeline_from_audio_stream,
        ),
        patch.object(entity, "async_announce") as mock_async_announce,
    ):
        hass.async_create_task(
            entity.async_accept_pipeline_from_satellite(
                object(),  # type: ignore[arg-type]
            )
        )

        async with asyncio.timeout(1):
            await pipeline_started.wait()
            await entity.async_internal_announce(None, media_id)
            await pipeline_cancelled.wait()

        mock_async_announce.assert_called_once()


async def test_announce_default_preannounce(
    hass: HomeAssistant, init_components: ConfigEntry, entity: MockAssistSatellite
) -> None:
    """Test announcing on a device with the default preannouncement sound."""

    async def async_announce(announcement):
        assert announcement.preannounce_media_id.endswith(PREANNOUNCE_URL)

    with patch.object(entity, "async_announce", new=async_announce):
        await hass.services.async_call(
            "assist_satellite",
            "announce",
            {"media_id": "test-media-id"},
            target={"entity_id": "assist_satellite.test_entity"},
            blocking=True,
        )


async def test_context_refresh(
    hass: HomeAssistant, init_components: ConfigEntry, entity: MockAssistSatellite
) -> None:
    """Test that the context will be automatically refreshed."""
    audio_stream = object()

    # Remove context
    entity._context = None

    with patch(
        "homeassistant.components.assist_satellite.entity.async_pipeline_from_audio_stream"
    ):
        await entity.async_accept_pipeline_from_satellite(audio_stream)

    # Context should have been refreshed
    assert entity._context is not None


async def test_pipeline_entity(
    hass: HomeAssistant, init_components: ConfigEntry, entity: MockAssistSatellite
) -> None:
    """Test getting pipeline from an entity."""
    audio_stream = object()
    pipeline = Pipeline(
        conversation_engine="test",
        conversation_language="en",
        language="en",
        name="test-pipeline",
        stt_engine=None,
        stt_language=None,
        tts_engine=None,
        tts_language=None,
        tts_voice=None,
        wake_word_entity=None,
        wake_word_id=None,
    )

    pipeline_entity_id = "select.pipeline"
    hass.states.async_set(pipeline_entity_id, pipeline.name)
    entity._attr_pipeline_entity_id = pipeline_entity_id

    done = asyncio.Event()

    async def async_pipeline_from_audio_stream(*args, pipeline_id: str, **kwargs):
        assert pipeline_id == pipeline.id
        done.set()

    with (
        patch(
            "homeassistant.components.assist_satellite.entity.async_pipeline_from_audio_stream",
            new=async_pipeline_from_audio_stream,
        ),
        patch(
            "homeassistant.components.assist_satellite.entity.async_get_pipelines",
            return_value=[pipeline],
        ),
    ):
        async with asyncio.timeout(1):
            await entity.async_accept_pipeline_from_satellite(audio_stream)
            await done.wait()


async def test_pipeline_entity_preferred(
    hass: HomeAssistant, init_components: ConfigEntry, entity: MockAssistSatellite
) -> None:
    """Test getting pipeline from an entity with a preferred state."""
    audio_stream = object()

    pipeline_entity_id = "select.pipeline"
    hass.states.async_set(pipeline_entity_id, OPTION_PREFERRED)
    entity._attr_pipeline_entity_id = pipeline_entity_id

    done = asyncio.Event()

    async def async_pipeline_from_audio_stream(*args, pipeline_id: str, **kwargs):
        # Preferred pipeline
        assert pipeline_id is None
        done.set()

    with (
        patch(
            "homeassistant.components.assist_satellite.entity.async_pipeline_from_audio_stream",
            new=async_pipeline_from_audio_stream,
        ),
    ):
        async with asyncio.timeout(1):
            await entity.async_accept_pipeline_from_satellite(audio_stream)
            await done.wait()


async def test_vad_sensitivity_entity(
    hass: HomeAssistant, init_components: ConfigEntry, entity: MockAssistSatellite
) -> None:
    """Test getting vad sensitivity from an entity."""
    audio_stream = object()

    vad_sensitivity_entity_id = "select.vad_sensitivity"
    hass.states.async_set(vad_sensitivity_entity_id, vad.VadSensitivity.AGGRESSIVE)
    entity._attr_vad_sensitivity_entity_id = vad_sensitivity_entity_id

    done = asyncio.Event()

    async def async_pipeline_from_audio_stream(
        *args, audio_settings: AudioSettings, **kwargs
    ):
        # Verify vad sensitivity
        assert audio_settings.silence_seconds == vad.VadSensitivity.to_seconds(
            vad.VadSensitivity.AGGRESSIVE
        )
        done.set()

    with patch(
        "homeassistant.components.assist_satellite.entity.async_pipeline_from_audio_stream",
        new=async_pipeline_from_audio_stream,
    ):
        async with asyncio.timeout(1):
            await entity.async_accept_pipeline_from_satellite(audio_stream)
            await done.wait()


async def test_pipeline_entity_not_found(
    hass: HomeAssistant, init_components: ConfigEntry, entity: MockAssistSatellite
) -> None:
    """Test that setting the pipeline entity id to a non-existent entity raises an error."""
    audio_stream = object()

    # Set to an entity that doesn't exist
    entity._attr_pipeline_entity_id = "select.pipeline"

    with pytest.raises(RuntimeError):
        await entity.async_accept_pipeline_from_satellite(audio_stream)


async def test_vad_sensitivity_entity_not_found(
    hass: HomeAssistant, init_components: ConfigEntry, entity: MockAssistSatellite
) -> None:
    """Test that setting the vad sensitivity entity id to a non-existent entity raises an error."""
    audio_stream = object()

    # Set to an entity that doesn't exist
    entity._attr_vad_sensitivity_entity_id = "select.vad_sensitivity"

    with pytest.raises(RuntimeError):
        await entity.async_accept_pipeline_from_satellite(audio_stream)


@pytest.mark.parametrize(
    ("service_data", "expected_params"),
    [
        (
            {
                "start_message": "Hello",
                "extra_system_prompt": "Better system prompt",
                "preannounce": False,
            },
            (
                "mock-conversation-id",
                "Better system prompt",
                AssistSatelliteAnnouncement(
                    message="Hello",
                    media_id="http://10.10.10.10:8123/api/tts_proxy/test-token",
                    tts_token="test-token",
                    original_media_id="media-source://generated",
                    media_id_source="tts",
                ),
            ),
        ),
        (
            {
                "start_message": "Hello",
                "start_media_id": "media-source://given",
                "preannounce": False,
            },
            (
                "mock-conversation-id",
                "Hello",
                AssistSatelliteAnnouncement(
                    message="Hello",
                    media_id="https://www.home-assistant.io/resolved.mp3",
                    tts_token=None,
                    original_media_id="media-source://given",
                    media_id_source="media_id",
                ),
            ),
        ),
        (
            {
                "start_media_id": "http://example.com/given.mp3",
                "preannounce": False,
            },
            (
                "mock-conversation-id",
                None,
                AssistSatelliteAnnouncement(
                    message="",
                    media_id="http://example.com/given.mp3",
                    tts_token=None,
                    original_media_id="http://example.com/given.mp3",
                    media_id_source="url",
                ),
            ),
        ),
        (
            {
                "start_media_id": "http://example.com/given.mp3",
                "preannounce_media_id": "http://example.com/preannounce.mp3",
            },
            (
                "mock-conversation-id",
                None,
                AssistSatelliteAnnouncement(
                    message="",
                    media_id="http://example.com/given.mp3",
                    tts_token=None,
                    original_media_id="http://example.com/given.mp3",
                    media_id_source="url",
                    preannounce_media_id="http://example.com/preannounce.mp3",
                ),
            ),
        ),
        (
            {
                "start_message": "Hello",
                "start_media_id": {
                    "media_content_id": "media-source://given",
                    "media_content_type": "provider",
                },
                "preannounce": False,
            },
            (
                "mock-conversation-id",
                "Hello",
                AssistSatelliteAnnouncement(
                    message="Hello",
                    media_id="https://www.home-assistant.io/resolved.mp3",
                    tts_token=None,
                    original_media_id="media-source://given",
                    media_id_source="media_id",
                ),
            ),
        ),
        (
            {
                "start_media_id": {
                    "media_content_id": "http://example.com/given.mp3",
                    "media_content_type": "audio",
                },
                "preannounce_media_id": {
                    "media_content_id": "http://example.com/preannounce.mp3",
                    "media_content_type": "audio",
                },
            },
            (
                "mock-conversation-id",
                None,
                AssistSatelliteAnnouncement(
                    message="",
                    media_id="http://example.com/given.mp3",
                    tts_token=None,
                    original_media_id="http://example.com/given.mp3",
                    media_id_source="url",
                    preannounce_media_id="http://example.com/preannounce.mp3",
                ),
            ),
        ),
    ],
)
@pytest.mark.usefixtures("mock_chat_session_conversation_id")
async def test_start_conversation(
    hass: HomeAssistant,
    init_components: ConfigEntry,
    entity: MockAssistSatellite,
    service_data: dict,
    expected_params: tuple[str, str],
) -> None:
    """Test starting a conversation on a device."""
    original_start_conversation = entity.async_start_conversation

    async def async_start_conversation(start_announcement):
        # Verify state change
        assert entity.state == AssistSatelliteState.RESPONDING
        await original_start_conversation(start_announcement)

    await async_update_pipeline(
        hass,
        async_get_pipeline(hass),
        conversation_engine="conversation.some_llm",
    )

    with (
        patch(
            "homeassistant.components.tts.generate_media_source_id",
            return_value="media-source://generated",
        ),
        patch(
            "homeassistant.components.tts.async_resolve_engine",
            return_value="tts.cloud",
        ),
        patch(
            "homeassistant.components.tts.async_create_stream",
            return_value=MockResultStream(hass, "wav", b""),
        ),
        patch(
            "homeassistant.components.media_source.async_resolve_media",
            return_value=PlayMedia(
                url="https://www.home-assistant.io/resolved.mp3",
                mime_type="audio/mp3",
            ),
        ),
        patch.object(entity, "async_start_conversation", new=async_start_conversation),
    ):
        await hass.services.async_call(
            "assist_satellite",
            "start_conversation",
            service_data,
            target={"entity_id": "assist_satellite.test_entity"},
            blocking=True,
        )
        assert entity.state == AssistSatelliteState.IDLE

    assert entity.start_conversations[0] == expected_params


async def test_start_conversation_reject_builtin_agent(
    hass: HomeAssistant,
    init_components: ConfigEntry,
    entity: MockAssistSatellite,
) -> None:
    """Test starting a conversation on a device."""
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "assist_satellite",
            "start_conversation",
            {"start_message": "Hey!"},
            target={"entity_id": "assist_satellite.test_entity"},
            blocking=True,
        )


async def test_start_conversation_default_preannounce(
    hass: HomeAssistant, init_components: ConfigEntry, entity: MockAssistSatellite
) -> None:
    """Test starting a conversation on a device with the default preannouncement sound."""

    async def async_start_conversation(start_announcement):
        assert PREANNOUNCE_URL in start_announcement.preannounce_media_id

    await async_update_pipeline(
        hass,
        async_get_pipeline(hass),
        conversation_engine="conversation.some_llm",
    )

    with (
        patch.object(entity, "async_start_conversation", new=async_start_conversation),
    ):
        await hass.services.async_call(
            "assist_satellite",
            "start_conversation",
            {"start_media_id": "test-media-id"},
            target={"entity_id": "assist_satellite.test_entity"},
            blocking=True,
        )


@pytest.mark.parametrize(
    ("service_data", "response_text", "expected_answer", "should_preannounce"),
    [
        (
            {},
            "jazz",
            AssistSatelliteAnswer(id=None, sentence="jazz"),
            True,
        ),
        (
            {"preannounce": False},
            "jazz",
            AssistSatelliteAnswer(id=None, sentence="jazz"),
            False,
        ),
        (
            {
                "answers": [
                    {"id": "jazz", "sentences": ["[some] jazz [please]"]},
                    {"id": "rock", "sentences": ["[some] rock [please]"]},
                ],
                "preannounce": False,
            },
            "Some Rock, please.",
            AssistSatelliteAnswer(id="rock", sentence="Some Rock, please."),
            False,
        ),
        (
            {
                "question_media_id": {
                    "media_content_id": "media-source://tts/cloud?message=What+kind+of+music+would+you+like+to+listen+to%3F&language=en-US&gender=female",
                    "media_content_type": "provider",
                },
                "answers": [
                    {
                        "id": "genre",
                        "sentences": ["genre {genre} [please]"],
                    },
                    {
                        "id": "artist",
                        "sentences": ["artist {artist} [please]"],
                    },
                ],
                "preannounce": True,
            },
            "artist Pink Floyd",
            AssistSatelliteAnswer(
                id="artist",
                sentence="artist Pink Floyd",
                slots={"artist": "Pink Floyd"},
            ),
            True,
        ),
    ],
)
async def test_ask_question(
    hass: HomeAssistant,
    init_components: ConfigEntry,
    entity: MockAssistSatellite,
    service_data: dict,
    response_text: str,
    expected_answer: AssistSatelliteAnswer,
    should_preannounce: bool,
) -> None:
    """Test asking a question on a device and matching an answer."""
    entity_id = "assist_satellite.test_entity"
    question_text = "What kind of music would you like to listen to?"

    await async_update_pipeline(
        hass, async_get_pipeline(hass), stt_engine="test-stt-engine", stt_language="en"
    )

    async def speech_to_text(self, *args, **kwargs):
        self.process_event(
            PipelineEvent(
                PipelineEventType.STT_END, {"stt_output": {"text": response_text}}
            )
        )

        return response_text

    original_start_conversation = entity.async_start_conversation

    async def async_start_conversation(start_announcement):
        # Verify state change
        assert entity.state == AssistSatelliteState.RESPONDING
        assert (
            start_announcement.preannounce_media_id is not None
        ) is should_preannounce
        await original_start_conversation(start_announcement)

        audio_stream = object()
        with (
            patch(
                "homeassistant.components.assist_pipeline.pipeline.PipelineRun.prepare_speech_to_text"
            ),
            patch(
                "homeassistant.components.assist_pipeline.pipeline.PipelineRun.speech_to_text",
                speech_to_text,
            ),
        ):
            await entity.async_accept_pipeline_from_satellite(
                audio_stream, start_stage=PipelineStage.STT
            )

    with (
        patch(
            "homeassistant.components.tts.generate_media_source_id",
            return_value="media-source://generated",
        ),
        patch(
            "homeassistant.components.tts.async_resolve_engine",
            return_value="tts.cloud",
        ),
        patch(
            "homeassistant.components.tts.async_create_stream",
            return_value=MockResultStream(hass, "wav", b""),
        ),
        patch(
            "homeassistant.components.media_source.async_resolve_media",
            return_value=PlayMedia(
                url="https://www.home-assistant.io/resolved.mp3",
                mime_type="audio/mp3",
            ),
        ),
        patch.object(entity, "async_start_conversation", new=async_start_conversation),
    ):
        response = await hass.services.async_call(
            "assist_satellite",
            "ask_question",
            {"entity_id": entity_id, "question": question_text, **service_data},
            blocking=True,
            return_response=True,
        )
        assert entity.state == AssistSatelliteState.IDLE
        assert response == asdict(expected_answer)


async def test_wake_word_start_keeps_responding(
    hass: HomeAssistant, init_components: ConfigEntry, entity: MockAssistSatellite
) -> None:
    """Test entity state stays responding on wake word start event."""

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == AssistSatelliteState.IDLE

    # Get into responding state
    audio_stream = object()

    with patch(
        "homeassistant.components.assist_satellite.entity.async_pipeline_from_audio_stream"
    ) as mock_start_pipeline:
        await entity.async_accept_pipeline_from_satellite(
            audio_stream, start_stage=PipelineStage.TTS
        )

    assert mock_start_pipeline.called
    kwargs = mock_start_pipeline.call_args[1]
    event_callback = kwargs["event_callback"]
    event_callback(PipelineEvent(PipelineEventType.TTS_START, {}))

    state = hass.states.get(ENTITY_ID)
    assert state.state == AssistSatelliteState.RESPONDING

    # Verify that starting a new wake word stream keeps the state
    audio_stream = object()

    with patch(
        "homeassistant.components.assist_satellite.entity.async_pipeline_from_audio_stream"
    ) as mock_start_pipeline:
        await entity.async_accept_pipeline_from_satellite(
            audio_stream, start_stage=PipelineStage.WAKE_WORD
        )

    assert mock_start_pipeline.called
    kwargs = mock_start_pipeline.call_args[1]
    event_callback = kwargs["event_callback"]
    event_callback(PipelineEvent(PipelineEventType.WAKE_WORD_START, {}))

    state = hass.states.get(ENTITY_ID)
    assert state.state == AssistSatelliteState.RESPONDING

    # Only return to idle once TTS is finished
    entity.tts_response_finished()
    state = hass.states.get(ENTITY_ID)
    assert state.state == AssistSatelliteState.IDLE
