"""Assist satellite entity."""

from abc import abstractmethod
import asyncio
from collections.abc import AsyncIterable
import contextlib
from dataclasses import dataclass, field
from enum import StrEnum
import logging
import time
from typing import Any, Literal, final

from hassil import Intents, recognize
from hassil.expression import Expression, ListReference, Sequence
from hassil.intents import WildcardSlotList

from homeassistant.components import conversation, media_source, stt, tts
from homeassistant.components.assist_pipeline import (
    OPTION_PREFERRED,
    AudioSettings,
    PipelineEvent,
    PipelineEventType,
    PipelineStage,
    async_get_pipeline,
    async_get_pipelines,
    async_pipeline_from_audio_stream,
    vad,
)
from homeassistant.components.media_player import async_process_play_media_url
from homeassistant.core import Context, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import chat_session, entity
from homeassistant.helpers.entity import EntityDescription

from .const import PREANNOUNCE_URL, AssistSatelliteEntityFeature
from .errors import AssistSatelliteError, SatelliteBusyError

_LOGGER = logging.getLogger(__name__)


class AssistSatelliteState(StrEnum):
    """Valid states of an Assist satellite entity."""

    IDLE = "idle"
    """Device is waiting for user input, such as a wake word or a button press."""

    LISTENING = "listening"
    """Device is streaming audio with the voice command to Home Assistant."""

    PROCESSING = "processing"
    """Home Assistant is processing the voice command."""

    RESPONDING = "responding"
    """Device is speaking the response."""


class AssistSatelliteEntityDescription(EntityDescription, frozen_or_thawed=True):
    """A class that describes Assist satellite entities."""


@dataclass(frozen=True)
class AssistSatelliteWakeWord:
    """Available wake word model."""

    id: str
    """Unique id for wake word model."""

    wake_word: str
    """Wake word phrase."""

    trained_languages: list[str]
    """List of languages that the wake word was trained on."""


@dataclass
class AssistSatelliteConfiguration:
    """Satellite configuration."""

    available_wake_words: list[AssistSatelliteWakeWord]
    """List of available available wake word models."""

    active_wake_words: list[str]
    """List of active wake word ids."""

    max_active_wake_words: int
    """Maximum number of simultaneous wake words allowed (0 for no limit)."""


@dataclass
class AssistSatelliteAnnouncement:
    """Announcement to be made."""

    message: str
    """Message to be spoken."""

    media_id: str
    """Media ID to be played."""

    original_media_id: str
    """The raw media ID before processing."""

    tts_token: str | None
    """The TTS token of the media."""

    media_id_source: Literal["url", "media_id", "tts"]
    """Source of the media ID."""

    preannounce_media_id: str | None = None
    """Media ID to be played before announcement."""


@dataclass
class AssistSatelliteAnswer:
    """Answer to a question."""

    id: str | None
    """Matched answer id or None if no answer was matched."""

    sentence: str
    """Raw sentence text from user response."""

    slots: dict[str, Any] = field(default_factory=dict)
    """Matched slots from answer."""


class AssistSatelliteEntity(entity.Entity):
    """Entity encapsulating the state and functionality of an Assist satellite."""

    entity_description: AssistSatelliteEntityDescription
    _attr_should_poll = False
    _attr_supported_features = AssistSatelliteEntityFeature(0)
    _attr_pipeline_entity_id: str | None = None
    _attr_vad_sensitivity_entity_id: str | None = None

    _conversation_id: str | None = None

    _run_has_tts: bool = False
    _is_announcing = False
    _extra_system_prompt: str | None = None
    _wake_word_intercept_future: asyncio.Future[str | None] | None = None
    _attr_tts_options: dict[str, Any] | None = None
    _pipeline_task: asyncio.Task | None = None
    _ask_question_future: asyncio.Future[str | None] | None = None

    __assist_satellite_state = AssistSatelliteState.IDLE

    @final
    @property
    def state(self) -> str | None:
        """Return state of the entity."""
        return self.__assist_satellite_state

    @property
    def pipeline_entity_id(self) -> str | None:
        """Entity ID of the pipeline to use for the next conversation."""
        return self._attr_pipeline_entity_id

    @property
    def vad_sensitivity_entity_id(self) -> str | None:
        """Entity ID of the VAD sensitivity to use for the next conversation."""
        return self._attr_vad_sensitivity_entity_id

    @property
    def tts_options(self) -> dict[str, Any] | None:
        """Options passed for text-to-speech."""
        return self._attr_tts_options

    @callback
    @abstractmethod
    def async_get_configuration(self) -> AssistSatelliteConfiguration:
        """Get the current satellite configuration."""

    @abstractmethod
    async def async_set_configuration(
        self, config: AssistSatelliteConfiguration
    ) -> None:
        """Set the current satellite configuration."""

    async def async_intercept_wake_word(self) -> str | None:
        """Intercept the next wake word from the satellite.

        Returns the detected wake word phrase or None.
        """
        if self._wake_word_intercept_future is not None:
            raise SatelliteBusyError("Wake word interception already in progress")

        # Will cause next wake word to be intercepted in
        # async_accept_pipeline_from_satellite
        self._wake_word_intercept_future = asyncio.Future()

        _LOGGER.debug("Next wake word will be intercepted: %s", self.entity_id)

        try:
            return await self._wake_word_intercept_future
        finally:
            self._wake_word_intercept_future = None

    async def async_internal_announce(
        self,
        message: str | None = None,
        media_id: str | None = None,
        preannounce: bool = True,
        preannounce_media_id: str = PREANNOUNCE_URL,
    ) -> None:
        """Play and show an announcement on the satellite.

        If media_id is not provided, message is synthesized to
        audio with the selected pipeline.

        If media_id is provided, it is played directly. It is possible
        to omit the message and the satellite will not show any text.

        If preannounce is True, a sound is played before the announcement.
        If preannounce_media_id is provided, it overrides the default sound.

        Calls async_announce with message and media id.
        """
        await self._cancel_running_pipeline()

        if message is None:
            message = ""

        announcement = await self._resolve_announcement_media_id(
            message,
            media_id,
            preannounce_media_id=preannounce_media_id if preannounce else None,
        )

        if self._is_announcing:
            raise SatelliteBusyError

        self._is_announcing = True
        self._set_state(AssistSatelliteState.RESPONDING)

        try:
            # Block until announcement is finished
            await self.async_announce(announcement)
        finally:
            self._is_announcing = False
            self._set_state(AssistSatelliteState.IDLE)

    async def async_announce(self, announcement: AssistSatelliteAnnouncement) -> None:
        """Announce media on the satellite.

        Should block until the announcement is done playing.
        """
        raise NotImplementedError

    async def async_internal_start_conversation(
        self,
        start_message: str | None = None,
        start_media_id: str | None = None,
        extra_system_prompt: str | None = None,
        preannounce: bool = True,
        preannounce_media_id: str = PREANNOUNCE_URL,
    ) -> None:
        """Start a conversation from the satellite.

        If start_media_id is not provided, message is synthesized to
        audio with the selected pipeline.

        If start_media_id is provided, it is played directly. It is possible
        to omit the message and the satellite will not show any text.

        If preannounce is True, a sound is played before the start message or media.
        If preannounce_media_id is provided, it overrides the default sound.

        Calls async_start_conversation.
        """
        await self._cancel_running_pipeline()

        # The Home Assistant built-in agent doesn't support conversations.
        pipeline = async_get_pipeline(self.hass, self._resolve_pipeline())
        if pipeline.conversation_engine == conversation.HOME_ASSISTANT_AGENT:
            raise HomeAssistantError(
                "Built-in conversation agent does not support starting conversations"
            )

        if start_message is None:
            start_message = ""

        announcement = await self._resolve_announcement_media_id(
            start_message,
            start_media_id,
            preannounce_media_id=preannounce_media_id if preannounce else None,
        )

        if self._is_announcing:
            raise SatelliteBusyError

        self._is_announcing = True
        self._set_state(AssistSatelliteState.RESPONDING)

        # Provide our start info to the LLM so it understands context of incoming message
        if extra_system_prompt is not None:
            self._extra_system_prompt = extra_system_prompt
        else:
            self._extra_system_prompt = start_message or None

        with (
            # Not passing in a conversation ID will force a new one to be created
            chat_session.async_get_chat_session(self.hass) as session,
            conversation.async_get_chat_log(self.hass, session) as chat_log,
        ):
            self._conversation_id = session.conversation_id

            if start_message:
                chat_log.async_add_assistant_content_without_tools(
                    conversation.AssistantContent(
                        agent_id=self.entity_id, content=start_message
                    )
                )

        try:
            await self.async_start_conversation(announcement)
        except Exception:
            # Clear prompt on error
            self._conversation_id = None
            self._extra_system_prompt = None
            raise
        finally:
            self._is_announcing = False
            self._set_state(AssistSatelliteState.IDLE)

    async def async_start_conversation(
        self, start_announcement: AssistSatelliteAnnouncement
    ) -> None:
        """Start a conversation from the satellite."""
        raise NotImplementedError

    async def async_internal_ask_question(
        self,
        question: str | None = None,
        question_media_id: str | None = None,
        preannounce: bool = True,
        preannounce_media_id: str = PREANNOUNCE_URL,
        answers: list[dict[str, Any]] | None = None,
    ) -> AssistSatelliteAnswer | None:
        """Ask a question and get a user's response from the satellite.

        If question_media_id is not provided, question is synthesized to audio
        with the selected pipeline.

        If question_media_id is provided, it is played directly. It is possible
        to omit the message and the satellite will not show any text.

        If preannounce is True, a sound is played before the start message or media.
        If preannounce_media_id is provided, it overrides the default sound.

        Calls async_start_conversation.
        """
        await self._cancel_running_pipeline()

        if question is None:
            question = ""

        announcement = await self._resolve_announcement_media_id(
            question,
            question_media_id,
            preannounce_media_id=preannounce_media_id if preannounce else None,
        )

        if self._is_announcing:
            raise SatelliteBusyError

        self._is_announcing = True
        self._set_state(AssistSatelliteState.RESPONDING)
        self._ask_question_future = asyncio.Future()

        try:
            # Wait for announcement to finish
            await self.async_start_conversation(announcement)

            # Wait for response text
            response_text = await self._ask_question_future
            if response_text is None:
                raise HomeAssistantError("No answer from question")

            if not answers:
                return AssistSatelliteAnswer(id=None, sentence=response_text)

            return self._question_response_to_answer(response_text, answers)
        finally:
            self._is_announcing = False
            self._set_state(AssistSatelliteState.IDLE)
            self._ask_question_future = None

    def _question_response_to_answer(
        self, response_text: str, answers: list[dict[str, Any]]
    ) -> AssistSatelliteAnswer:
        """Match text to a pre-defined set of answers."""

        # Build intents and match
        intents = Intents.from_dict(
            {
                "language": self.hass.config.language,
                "intents": {
                    "QuestionIntent": {
                        "data": [
                            {
                                "sentences": answer["sentences"],
                                "metadata": {"answer_id": answer["id"]},
                            }
                            for answer in answers
                        ]
                    }
                },
            }
        )

        # Assume slot list references are wildcards
        wildcard_names: set[str] = set()
        for intent in intents.intents.values():
            for intent_data in intent.data:
                for sentence in intent_data.sentences:
                    _collect_list_references(sentence, wildcard_names)

        for wildcard_name in wildcard_names:
            intents.slot_lists[wildcard_name] = WildcardSlotList(wildcard_name)

        # Match response text
        result = recognize(response_text, intents)
        if result is None:
            # No match
            return AssistSatelliteAnswer(id=None, sentence=response_text)

        assert result.intent_metadata
        return AssistSatelliteAnswer(
            id=result.intent_metadata["answer_id"],
            sentence=response_text,
            slots={
                entity_name: entity.value
                for entity_name, entity in result.entities.items()
            },
        )

    async def async_accept_pipeline_from_satellite(
        self,
        audio_stream: AsyncIterable[bytes],
        start_stage: PipelineStage = PipelineStage.STT,
        end_stage: PipelineStage = PipelineStage.TTS,
        wake_word_phrase: str | None = None,
    ) -> None:
        """Triggers an Assist pipeline in Home Assistant from a satellite."""
        await self._cancel_running_pipeline()

        # Consume system prompt in first pipeline
        extra_system_prompt = self._extra_system_prompt
        self._extra_system_prompt = None

        if self._wake_word_intercept_future and start_stage in (
            PipelineStage.WAKE_WORD,
            PipelineStage.STT,
        ):
            if start_stage == PipelineStage.WAKE_WORD:
                self._wake_word_intercept_future.set_exception(
                    AssistSatelliteError(
                        "Only on-device wake words currently supported"
                    )
                )
                return

            # Intercepting wake word and immediately end pipeline
            _LOGGER.debug(
                "Intercepted wake word: %s (entity_id=%s)",
                wake_word_phrase,
                self.entity_id,
            )

            if wake_word_phrase is None:
                self._wake_word_intercept_future.set_exception(
                    AssistSatelliteError("No wake word phrase provided")
                )
            else:
                self._wake_word_intercept_future.set_result(wake_word_phrase)
            self._internal_on_pipeline_event(PipelineEvent(PipelineEventType.RUN_END))
            return

        if (self._ask_question_future is not None) and (
            start_stage == PipelineStage.STT
        ):
            end_stage = PipelineStage.STT

        device_id = self.registry_entry.device_id if self.registry_entry else None

        # Refresh context if necessary
        if (
            (self._context is None)
            or (self._context_set is None)
            or ((time.time() - self._context_set) > entity.CONTEXT_RECENT_TIME_SECONDS)
        ):
            self.async_set_context(Context())

        assert self._context is not None

        # Set entity state based on pipeline events
        self._run_has_tts = False

        assert self.platform.config_entry is not None

        with chat_session.async_get_chat_session(
            self.hass, self._conversation_id
        ) as session:
            # Store the conversation ID. If it is no longer valid, get_chat_session will reset it
            self._conversation_id = session.conversation_id
            self._pipeline_task = (
                self.platform.config_entry.async_create_background_task(
                    self.hass,
                    async_pipeline_from_audio_stream(
                        self.hass,
                        context=self._context,
                        event_callback=self._internal_on_pipeline_event,
                        stt_metadata=stt.SpeechMetadata(
                            language="",  # set in async_pipeline_from_audio_stream
                            format=stt.AudioFormats.WAV,
                            codec=stt.AudioCodecs.PCM,
                            bit_rate=stt.AudioBitRates.BITRATE_16,
                            sample_rate=stt.AudioSampleRates.SAMPLERATE_16000,
                            channel=stt.AudioChannels.CHANNEL_MONO,
                        ),
                        stt_stream=audio_stream,
                        pipeline_id=self._resolve_pipeline(),
                        conversation_id=session.conversation_id,
                        device_id=device_id,
                        tts_audio_output=self.tts_options,
                        wake_word_phrase=wake_word_phrase,
                        audio_settings=AudioSettings(
                            silence_seconds=self._resolve_vad_sensitivity()
                        ),
                        start_stage=start_stage,
                        end_stage=end_stage,
                        conversation_extra_system_prompt=extra_system_prompt,
                    ),
                    f"{self.entity_id}_pipeline",
                )
            )

            try:
                await self._pipeline_task
            finally:
                self._pipeline_task = None

    async def _cancel_running_pipeline(self) -> None:
        """Cancel the current pipeline if it's running."""
        if self._pipeline_task is not None:
            self._pipeline_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pipeline_task

            self._pipeline_task = None

    @abstractmethod
    def on_pipeline_event(self, event: PipelineEvent) -> None:
        """Handle pipeline events."""

    @callback
    def _internal_on_pipeline_event(self, event: PipelineEvent) -> None:
        """Set state based on pipeline stage."""
        if event.type is PipelineEventType.WAKE_WORD_START:
            # Only return to idle if we're not currently responding.
            # The state will return to idle in tts_response_finished.
            if self.state != AssistSatelliteState.RESPONDING:
                self._set_state(AssistSatelliteState.IDLE)
        elif event.type is PipelineEventType.STT_START:
            self._set_state(AssistSatelliteState.LISTENING)
        elif event.type is PipelineEventType.STT_END:
            # Intercepting text for ask question
            if (
                (self._ask_question_future is not None)
                and (not self._ask_question_future.done())
                and event.data
            ):
                self._ask_question_future.set_result(
                    event.data.get("stt_output", {}).get("text")
                )
        elif event.type is PipelineEventType.INTENT_START:
            self._set_state(AssistSatelliteState.PROCESSING)
        elif event.type is PipelineEventType.TTS_START:
            # Wait until tts_response_finished is called to return to waiting state
            self._run_has_tts = True
            self._set_state(AssistSatelliteState.RESPONDING)
        elif event.type is PipelineEventType.RUN_END:
            if not self._run_has_tts:
                self._set_state(AssistSatelliteState.IDLE)

            if (self._ask_question_future is not None) and (
                not self._ask_question_future.done()
            ):
                # No text for ask question
                self._ask_question_future.set_result(None)

        self.on_pipeline_event(event)

    @callback
    def _set_state(self, state: AssistSatelliteState) -> None:
        """Set the entity's state."""
        self.__assist_satellite_state = state
        self.async_write_ha_state()

    @callback
    def tts_response_finished(self) -> None:
        """Tell entity that the text-to-speech response has finished playing."""
        self._set_state(AssistSatelliteState.IDLE)

    @callback
    def _resolve_pipeline(self) -> str | None:
        """Resolve pipeline from select entity to id.

        Return None to make async_get_pipeline look up the preferred pipeline.
        """
        if not (pipeline_entity_id := self.pipeline_entity_id):
            return None

        if (pipeline_entity_state := self.hass.states.get(pipeline_entity_id)) is None:
            raise RuntimeError("Pipeline entity not found")

        if pipeline_entity_state.state != OPTION_PREFERRED:
            # Resolve pipeline by name
            for pipeline in async_get_pipelines(self.hass):
                if pipeline.name == pipeline_entity_state.state:
                    return pipeline.id

        return None

    @callback
    def _resolve_vad_sensitivity(self) -> float:
        """Resolve VAD sensitivity from select entity to enum."""
        vad_sensitivity = vad.VadSensitivity.DEFAULT

        if vad_sensitivity_entity_id := self.vad_sensitivity_entity_id:
            if (
                vad_sensitivity_state := self.hass.states.get(vad_sensitivity_entity_id)
            ) is None:
                raise RuntimeError("VAD sensitivity entity not found")

            vad_sensitivity = vad.VadSensitivity(vad_sensitivity_state.state)

        return vad.VadSensitivity.to_seconds(vad_sensitivity)

    async def _resolve_announcement_media_id(
        self,
        message: str,
        media_id: str | None,
        preannounce_media_id: str | None = None,
    ) -> AssistSatelliteAnnouncement:
        """Resolve the media ID."""
        media_id_source: Literal["url", "media_id", "tts"] | None = None
        tts_token: str | None = None

        if media_id:
            original_media_id = media_id
        else:
            media_id_source = "tts"
            # Synthesize audio and get URL
            pipeline_id = self._resolve_pipeline()
            pipeline = async_get_pipeline(self.hass, pipeline_id)

            engine = tts.async_resolve_engine(self.hass, pipeline.tts_engine)
            if engine is None:
                raise HomeAssistantError(f"TTS engine {pipeline.tts_engine} not found")

            tts_options: dict[str, Any] = {}
            if pipeline.tts_voice is not None:
                tts_options[tts.ATTR_VOICE] = pipeline.tts_voice

            if self.tts_options is not None:
                tts_options.update(self.tts_options)

            stream = tts.async_create_stream(
                self.hass,
                engine=engine,
                language=pipeline.tts_language,
                options=tts_options,
            )
            stream.async_set_message(message)

            tts_token = stream.token
            media_id = stream.url
            original_media_id = tts.generate_media_source_id(
                self.hass,
                message,
                engine=engine,
                language=pipeline.tts_language,
                options=tts_options,
            )

        if media_source.is_media_source_id(media_id):
            if not media_id_source:
                media_id_source = "media_id"
            media = await media_source.async_resolve_media(
                self.hass,
                media_id,
                None,
            )
            media_id = media.url

        if not media_id_source:
            media_id_source = "url"

        # Resolve to full URL
        media_id = async_process_play_media_url(self.hass, media_id)

        # Resolve preannounce media id
        if preannounce_media_id:
            if media_source.is_media_source_id(preannounce_media_id):
                preannounce_media = await media_source.async_resolve_media(
                    self.hass,
                    preannounce_media_id,
                    None,
                )
                preannounce_media_id = preannounce_media.url

            # Resolve to full URL
            preannounce_media_id = async_process_play_media_url(
                self.hass, preannounce_media_id
            )

        return AssistSatelliteAnnouncement(
            message=message,
            media_id=media_id,
            original_media_id=original_media_id,
            tts_token=tts_token,
            media_id_source=media_id_source,
            preannounce_media_id=preannounce_media_id,
        )


def _collect_list_references(expression: Expression, list_names: set[str]) -> None:
    """Collect list reference names recursively."""
    if isinstance(expression, Sequence):
        seq: Sequence = expression
        for item in seq.items:
            _collect_list_references(item, list_names)
    elif isinstance(expression, ListReference):
        # {list}
        list_ref: ListReference = expression
        list_names.add(list_ref.slot_name)
