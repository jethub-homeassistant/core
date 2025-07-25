"""Tests for the HTTP API for the cloud component."""

from collections.abc import Callable, Coroutine
from copy import deepcopy
import datetime
from http import HTTPStatus
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, PropertyMock, patch

import aiohttp
from freezegun.api import FrozenDateTimeFactory
from hass_nabucasa import AlreadyConnectedError
from hass_nabucasa.auth import (
    InvalidTotpCode,
    MFARequired,
    Unauthenticated,
    UnknownError,
)
from hass_nabucasa.const import STATE_CONNECTED
from hass_nabucasa.payments_api import PaymentsApiError
from hass_nabucasa.remote import CertificateStatus
import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.components import system_health
from homeassistant.components.alexa import errors as alexa_errors

# pylint: disable-next=hass-component-root-import
from homeassistant.components.alexa.entities import LightCapabilities
from homeassistant.components.assist_pipeline.pipeline import STORAGE_KEY
from homeassistant.components.cloud.const import DEFAULT_EXPOSED_DOMAINS, DOMAIN
from homeassistant.components.cloud.http_api import validate_language_voice
from homeassistant.components.google_assistant.helpers import GoogleEntity
from homeassistant.components.homeassistant import exposed_entities
from homeassistant.components.websocket_api import ERR_INVALID_FORMAT
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from homeassistant.util.location import LocationInfo

from tests.common import mock_platform
from tests.components.google_assistant import MockConfig
from tests.test_util.aiohttp import AiohttpClientMocker
from tests.typing import ClientSessionGenerator, WebSocketGenerator

PIPELINE_DATA_LEGACY = {
    "items": [
        {
            "conversation_engine": "homeassistant",
            "conversation_language": "language_1",
            "id": "12345",
            "language": "language_1",
            "name": "Home Assistant Cloud",
            "stt_engine": "cloud",
            "stt_language": "language_1",
            "tts_engine": "cloud",
            "tts_language": "language_1",
            "tts_voice": "Arnold Schwarzenegger",
            "wake_word_entity": None,
            "wake_word_id": None,
        },
    ],
    "preferred_item": "12345",
}

PIPELINE_DATA = {
    "items": [
        {
            "conversation_engine": "homeassistant",
            "conversation_language": "language_1",
            "id": "12345",
            "language": "language_1",
            "name": "Home Assistant Cloud",
            "stt_engine": "stt.home_assistant_cloud",
            "stt_language": "language_1",
            "tts_engine": "cloud",
            "tts_language": "language_1",
            "tts_voice": "Arnold Schwarzenegger",
            "wake_word_entity": None,
            "wake_word_id": None,
        },
    ],
    "preferred_item": "12345",
}

PIPELINE_DATA_OTHER = {
    "items": [
        {
            "conversation_engine": "other",
            "conversation_language": "language_1",
            "id": "12345",
            "language": "language_1",
            "name": "Home Assistant",
            "stt_engine": "stt.other",
            "stt_language": "language_1",
            "tts_engine": "other",
            "tts_language": "language_1",
            "tts_voice": "Arnold Schwarzenegger",
            "wake_word_entity": None,
            "wake_word_id": None,
        },
    ],
    "preferred_item": "12345",
}

SUBSCRIPTION_INFO_URL = "https://api-test.hass.io/payments/subscription_info"


@pytest.fixture(name="setup_cloud")
async def setup_cloud_fixture(hass: HomeAssistant, cloud: MagicMock) -> None:
    """Fixture that sets up cloud."""
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(
        hass,
        DOMAIN,
        {
            DOMAIN: {
                "mode": "development",
                "cognito_client_id": "cognito_client_id",
                "user_pool_id": "user_pool_id",
                "region": "region",
                "relayer_server": "relayer",
                "acme_server": "cert-server",
                "accounts_server": "api-test.hass.io",
                "google_actions": {"filter": {"include_domains": "light"}},
                "alexa": {
                    "filter": {"include_entities": ["light.kitchen", "switch.ac"]}
                },
            },
        },
    )
    await hass.async_block_till_done()
    await cloud.login("test-user", "test-pass")
    cloud.login.reset_mock()


async def test_google_actions_sync(
    setup_cloud: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test syncing Google Actions."""
    cloud_client = await hass_client()
    with patch(
        "hass_nabucasa.cloud_api.async_google_actions_request_sync",
        return_value=Mock(status=200),
    ) as mock_request_sync:
        req = await cloud_client.post("/api/cloud/google_actions/sync")
        assert req.status == HTTPStatus.OK
        assert mock_request_sync.call_count == 1


async def test_google_actions_sync_fails(
    setup_cloud: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test syncing Google Actions gone bad."""
    cloud_client = await hass_client()
    with patch(
        "hass_nabucasa.cloud_api.async_google_actions_request_sync",
        return_value=Mock(status=HTTPStatus.INTERNAL_SERVER_ERROR),
    ) as mock_request_sync:
        req = await cloud_client.post("/api/cloud/google_actions/sync")
        assert req.status == HTTPStatus.INTERNAL_SERVER_ERROR
        assert mock_request_sync.call_count == 1


@pytest.mark.parametrize(
    "entity_id", ["stt.home_assistant_cloud", "tts.home_assistant_cloud"]
)
async def test_login_view_missing_entity(
    hass: HomeAssistant,
    setup_cloud: None,
    entity_registry: er.EntityRegistry,
    hass_client: ClientSessionGenerator,
    entity_id: str,
) -> None:
    """Test logging in when a cloud assist pipeline needed entity is missing."""
    # Make sure that the cloud entity does not exist.
    entity_registry.async_remove(entity_id)
    await hass.async_block_till_done()

    cloud_client = await hass_client()

    # We assume the user needs to login again for some reason.
    with patch(
        "homeassistant.components.cloud.assist_pipeline.async_create_default_pipeline",
    ) as create_pipeline_mock:
        req = await cloud_client.post(
            "/api/cloud/login", json={"email": "my_username", "password": "my_password"}
        )

    assert req.status == HTTPStatus.OK
    result = await req.json()
    assert result == {"success": True, "cloud_pipeline": None}
    create_pipeline_mock.assert_not_awaited()


@pytest.mark.parametrize("pipeline_data", [PIPELINE_DATA, PIPELINE_DATA_LEGACY])
async def test_login_view_existing_pipeline(
    hass: HomeAssistant,
    cloud: MagicMock,
    hass_client: ClientSessionGenerator,
    hass_storage: dict[str, Any],
    pipeline_data: dict[str, Any],
) -> None:
    """Test logging in when an assist pipeline is available."""
    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": deepcopy(pipeline_data),
    }

    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, DOMAIN, {"cloud": {}})
    await hass.async_block_till_done()

    cloud_client = await hass_client()

    with patch(
        "homeassistant.components.cloud.assist_pipeline.async_create_default_pipeline",
    ) as create_pipeline_mock:
        req = await cloud_client.post(
            "/api/cloud/login", json={"email": "my_username", "password": "my_password"}
        )

    assert req.status == HTTPStatus.OK
    result = await req.json()
    assert result == {"success": True, "cloud_pipeline": None}
    create_pipeline_mock.assert_not_awaited()


async def test_login_view_create_pipeline(
    hass: HomeAssistant,
    cloud: MagicMock,
    hass_client: ClientSessionGenerator,
    hass_storage: dict[str, Any],
) -> None:
    """Test logging in when no existing cloud assist pipeline is available."""
    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": deepcopy(PIPELINE_DATA_OTHER),
    }

    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "assist_pipeline", {})
    assert await async_setup_component(hass, DOMAIN, {"cloud": {}})
    await hass.async_block_till_done()

    cloud_client = await hass_client()

    with patch(
        "homeassistant.components.cloud.assist_pipeline.async_create_default_pipeline",
        return_value=AsyncMock(id="12345"),
    ) as create_pipeline_mock:
        req = await cloud_client.post(
            "/api/cloud/login", json={"email": "my_username", "password": "my_password"}
        )

    assert req.status == HTTPStatus.OK
    result = await req.json()
    assert result == {"success": True, "cloud_pipeline": "12345"}
    create_pipeline_mock.assert_awaited_once_with(
        hass,
        stt_engine_id="stt.home_assistant_cloud",
        tts_engine_id="tts.home_assistant_cloud",
        pipeline_name="Home Assistant Cloud",
    )


async def test_login_view_create_pipeline_fail(
    hass: HomeAssistant,
    cloud: MagicMock,
    hass_client: ClientSessionGenerator,
    hass_storage: dict[str, Any],
) -> None:
    """Test logging in when no assist pipeline is available."""
    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": deepcopy(PIPELINE_DATA_OTHER),
    }

    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "assist_pipeline", {})
    assert await async_setup_component(hass, DOMAIN, {"cloud": {}})
    await hass.async_block_till_done()

    cloud_client = await hass_client()

    with patch(
        "homeassistant.components.cloud.assist_pipeline.async_create_default_pipeline",
        return_value=None,
    ) as create_pipeline_mock:
        req = await cloud_client.post(
            "/api/cloud/login", json={"email": "my_username", "password": "my_password"}
        )

    assert req.status == HTTPStatus.OK
    result = await req.json()
    assert result == {"success": True, "cloud_pipeline": None}
    create_pipeline_mock.assert_awaited_once_with(
        hass,
        stt_engine_id="stt.home_assistant_cloud",
        tts_engine_id="tts.home_assistant_cloud",
        pipeline_name="Home Assistant Cloud",
    )


async def test_login_view_random_exception(
    cloud: MagicMock,
    setup_cloud: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Try logging in with random exception."""
    cloud_client = await hass_client()
    cloud.login.side_effect = ValueError("Boom")

    req = await cloud_client.post(
        "/api/cloud/login", json={"email": "my_username", "password": "my_password"}
    )

    assert req.status == HTTPStatus.BAD_GATEWAY
    resp = await req.json()
    assert resp == {"code": "valueerror", "message": "Unexpected error: Boom"}


async def test_login_view_invalid_json(
    cloud: MagicMock,
    setup_cloud: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Try logging in with invalid JSON."""
    cloud_client = await hass_client()
    mock_login = cloud.login

    req = await cloud_client.post("/api/cloud/login", data="Not JSON")

    assert req.status == HTTPStatus.BAD_REQUEST
    assert mock_login.call_count == 0


async def test_login_view_invalid_schema(
    cloud: MagicMock,
    setup_cloud: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Try logging in with invalid schema."""
    cloud_client = await hass_client()
    mock_login = cloud.login

    req = await cloud_client.post("/api/cloud/login", json={"invalid": "schema"})

    assert req.status == HTTPStatus.BAD_REQUEST
    assert mock_login.call_count == 0


async def test_login_view_request_timeout(
    cloud: MagicMock,
    setup_cloud: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test request timeout while trying to log in."""
    cloud_client = await hass_client()
    cloud.login.side_effect = TimeoutError

    req = await cloud_client.post(
        "/api/cloud/login", json={"email": "my_username", "password": "my_password"}
    )

    assert cloud.login.call_args[1]["check_connection"] is False

    assert req.status == HTTPStatus.BAD_GATEWAY


async def test_login_view_with_already_existing_connection(
    cloud: MagicMock,
    setup_cloud: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test request timeout while trying to log in."""
    cloud_client = await hass_client()
    cloud.login.side_effect = AlreadyConnectedError(
        details={"remote_ip_address": "127.0.0.1", "connected_at": "1"}
    )

    req = await cloud_client.post(
        "/api/cloud/login",
        json={
            "email": "my_username",
            "password": "my_password",
            "check_connection": True,
        },
    )

    assert cloud.login.call_args[1]["check_connection"] is True
    assert req.status == HTTPStatus.CONFLICT
    resp = await req.json()
    assert resp == {
        "code": "alreadyconnectederror",
        "message": '{"remote_ip_address": "127.0.0.1", "connected_at": "1"}',
    }


async def test_login_view_invalid_credentials(
    cloud: MagicMock,
    setup_cloud: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test logging in with invalid credentials."""
    cloud_client = await hass_client()
    cloud.login.side_effect = Unauthenticated

    req = await cloud_client.post(
        "/api/cloud/login", json={"email": "my_username", "password": "my_password"}
    )

    assert req.status == HTTPStatus.UNAUTHORIZED


async def test_login_view_mfa_required(
    cloud: MagicMock,
    setup_cloud: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test logging in when MFA is required."""
    cloud_client = await hass_client()
    cloud.login.side_effect = MFARequired(mfa_tokens={"session": "tokens"})

    req = await cloud_client.post(
        "/api/cloud/login", json={"email": "my_username", "password": "my_password"}
    )

    assert req.status == HTTPStatus.UNAUTHORIZED
    res = await req.json()
    assert res["code"] == "mfarequired"


async def test_login_view_mfa_required_tokens_missing(
    cloud: MagicMock,
    setup_cloud: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test logging in when MFA is required, code is provided, but session tokens are missing."""
    cloud_client = await hass_client()
    cloud.login.side_effect = MFARequired(mfa_tokens={})

    # Login with password and get MFA required error
    req = await cloud_client.post(
        "/api/cloud/login", json={"email": "my_username", "password": "my_password"}
    )

    assert req.status == HTTPStatus.UNAUTHORIZED
    res = await req.json()
    assert res["code"] == "mfarequired"

    # Login with TOTP code and get MFA expired error
    req = await cloud_client.post(
        "/api/cloud/login",
        json={"email": "my_username", "code": "123346"},
    )

    assert req.status == HTTPStatus.BAD_REQUEST
    res = await req.json()
    assert res["code"] == "mfaexpiredornotstarted"


async def test_login_view_mfa_password_and_totp_provided(
    cloud: MagicMock,
    setup_cloud: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test logging in when password and TOTP code provided at once."""
    cloud_client = await hass_client()

    req = await cloud_client.post(
        "/api/cloud/login",
        json={"email": "my_username", "password": "my_password", "code": "123346"},
    )

    assert req.status == HTTPStatus.BAD_REQUEST


async def test_login_view_invalid_totp_code(
    cloud: MagicMock,
    setup_cloud: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test logging in when MFA is required and invalid code is provided."""
    cloud_client = await hass_client()
    cloud.login.side_effect = MFARequired(mfa_tokens={"session": "tokens"})
    cloud.login_verify_totp.side_effect = InvalidTotpCode

    # Login with password and get MFA required error
    req = await cloud_client.post(
        "/api/cloud/login", json={"email": "my_username", "password": "my_password"}
    )

    assert req.status == HTTPStatus.UNAUTHORIZED
    res = await req.json()
    assert res["code"] == "mfarequired"

    # Login with TOTP code and get invalid TOTP code error
    req = await cloud_client.post(
        "/api/cloud/login",
        json={"email": "my_username", "code": "123346"},
    )

    assert req.status == HTTPStatus.BAD_REQUEST
    res = await req.json()
    assert res["code"] == "invalidtotpcode"


async def test_login_view_valid_totp_provided(
    cloud: MagicMock,
    setup_cloud: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test logging in with valid TOTP code."""
    cloud_client = await hass_client()
    cloud.login.side_effect = MFARequired(mfa_tokens={"session": "tokens"})

    # Login with password and get MFA required error
    req = await cloud_client.post(
        "/api/cloud/login", json={"email": "my_username", "password": "my_password"}
    )

    assert req.status == HTTPStatus.UNAUTHORIZED
    res = await req.json()
    assert res["code"] == "mfarequired"

    # Login with TOTP code and get success response
    req = await cloud_client.post(
        "/api/cloud/login",
        json={"email": "my_username", "code": "123346"},
    )

    assert req.status == HTTPStatus.OK
    result = await req.json()
    assert result == {"success": True, "cloud_pipeline": None}


async def test_login_view_unknown_error(
    cloud: MagicMock,
    setup_cloud: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test unknown error while logging in."""
    cloud_client = await hass_client()
    cloud.login.side_effect = UnknownError

    req = await cloud_client.post(
        "/api/cloud/login", json={"email": "my_username", "password": "my_password"}
    )

    assert req.status == HTTPStatus.BAD_GATEWAY


async def test_logout_view(
    cloud: MagicMock,
    setup_cloud: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test logging out."""
    cloud_client = await hass_client()
    req = await cloud_client.post("/api/cloud/logout")

    assert req.status == HTTPStatus.OK
    data = await req.json()
    assert data == {"message": "ok"}
    assert cloud.logout.call_count == 1


async def test_logout_view_request_timeout(
    cloud: MagicMock,
    setup_cloud: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test timeout while logging out."""
    cloud_client = await hass_client()
    cloud.logout.side_effect = TimeoutError

    req = await cloud_client.post("/api/cloud/logout")

    assert req.status == HTTPStatus.BAD_GATEWAY


async def test_logout_view_unknown_error(
    cloud: MagicMock,
    setup_cloud: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test unknown error while logging out."""
    cloud_client = await hass_client()
    cloud.logout.side_effect = UnknownError

    req = await cloud_client.post("/api/cloud/logout")

    assert req.status == HTTPStatus.BAD_GATEWAY


async def test_register_view_no_location(
    cloud: MagicMock,
    setup_cloud: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test register without location."""
    cloud_client = await hass_client()
    mock_cognito = cloud.auth
    with patch(
        "homeassistant.components.cloud.http_api.async_detect_location_info",
        return_value=None,
    ):
        req = await cloud_client.post(
            "/api/cloud/register",
            json={"email": "hello@bla.com", "password": "falcon42"},
        )

    assert req.status == HTTPStatus.OK
    assert mock_cognito.async_register.call_count == 1
    call = mock_cognito.async_register.mock_calls[0]
    result_email, result_pass = call.args
    assert result_email == "hello@bla.com"
    assert result_pass == "falcon42"
    assert call.kwargs["client_metadata"] is None


async def test_register_view_with_location(
    cloud: MagicMock,
    setup_cloud: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test register with location."""
    cloud_client = await hass_client()
    mock_cognito = cloud.auth
    with patch(
        "homeassistant.components.cloud.http_api.async_detect_location_info",
        return_value=LocationInfo(
            country_code="XX",
            zip_code="12345",
            region_code="GH",
            ip="1.2.3.4",
            city="Gotham",
            region_name="Gotham",
            time_zone="Earth/Gotham",
            currency="XXX",
            latitude="12.34567",
            longitude="12.34567",
            use_metric=True,
        ),
    ):
        req = await cloud_client.post(
            "/api/cloud/register",
            json={"email": "hello@bla.com", "password": "falcon42"},
        )

    assert req.status == HTTPStatus.OK
    assert mock_cognito.async_register.call_count == 1
    call = mock_cognito.async_register.mock_calls[0]
    result_email, result_pass = call.args
    assert result_email == "hello@bla.com"
    assert result_pass == "falcon42"
    assert call.kwargs["client_metadata"] == {
        "NC_COUNTRY_CODE": "XX",
        "NC_REGION_CODE": "GH",
        "NC_ZIP_CODE": "12345",
    }


async def test_register_view_bad_data(
    cloud: MagicMock,
    setup_cloud: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test register bad data."""
    cloud_client = await hass_client()
    mock_cognito = cloud.auth

    req = await cloud_client.post(
        "/api/cloud/register", json={"email": "hello@bla.com", "not_password": "falcon"}
    )

    assert req.status == HTTPStatus.BAD_REQUEST
    assert mock_cognito.async_register.call_count == 0


async def test_register_view_request_timeout(
    cloud: MagicMock,
    setup_cloud: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test timeout while registering."""
    cloud_client = await hass_client()
    cloud.auth.async_register.side_effect = TimeoutError

    req = await cloud_client.post(
        "/api/cloud/register", json={"email": "hello@bla.com", "password": "falcon42"}
    )

    assert req.status == HTTPStatus.BAD_GATEWAY


async def test_register_view_unknown_error(
    cloud: MagicMock,
    setup_cloud: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test unknown error while registering."""
    cloud_client = await hass_client()
    cloud.auth.async_register.side_effect = UnknownError

    req = await cloud_client.post(
        "/api/cloud/register", json={"email": "hello@bla.com", "password": "falcon42"}
    )

    assert req.status == HTTPStatus.BAD_GATEWAY


async def test_forgot_password_view(
    cloud: MagicMock,
    setup_cloud: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test forgot password."""
    cloud_client = await hass_client()
    mock_cognito = cloud.auth

    req = await cloud_client.post(
        "/api/cloud/forgot_password", json={"email": "hello@bla.com"}
    )

    assert req.status == HTTPStatus.OK
    assert mock_cognito.async_forgot_password.call_count == 1


async def test_forgot_password_view_bad_data(
    cloud: MagicMock,
    setup_cloud: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test forgot password bad data."""
    cloud_client = await hass_client()
    mock_cognito = cloud.auth

    req = await cloud_client.post(
        "/api/cloud/forgot_password", json={"not_email": "hello@bla.com"}
    )

    assert req.status == HTTPStatus.BAD_REQUEST
    assert mock_cognito.async_forgot_password.call_count == 0


async def test_forgot_password_view_request_timeout(
    cloud: MagicMock,
    setup_cloud: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test timeout while forgot password."""
    cloud_client = await hass_client()
    cloud.auth.async_forgot_password.side_effect = TimeoutError

    req = await cloud_client.post(
        "/api/cloud/forgot_password", json={"email": "hello@bla.com"}
    )

    assert req.status == HTTPStatus.BAD_GATEWAY


async def test_forgot_password_view_unknown_error(
    cloud: MagicMock,
    setup_cloud: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test unknown error while forgot password."""
    cloud_client = await hass_client()
    cloud.auth.async_forgot_password.side_effect = UnknownError

    req = await cloud_client.post(
        "/api/cloud/forgot_password", json={"email": "hello@bla.com"}
    )

    assert req.status == HTTPStatus.BAD_GATEWAY


async def test_forgot_password_view_aiohttp_error(
    cloud: MagicMock,
    setup_cloud: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test unknown error while forgot password."""
    cloud_client = await hass_client()
    cloud.auth.async_forgot_password.side_effect = aiohttp.ClientResponseError(
        Mock(), Mock()
    )

    req = await cloud_client.post(
        "/api/cloud/forgot_password", json={"email": "hello@bla.com"}
    )

    assert req.status == HTTPStatus.INTERNAL_SERVER_ERROR


async def test_resend_confirm_view(
    cloud: MagicMock,
    setup_cloud: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test resend confirm."""
    cloud_client = await hass_client()
    mock_cognito = cloud.auth

    req = await cloud_client.post(
        "/api/cloud/resend_confirm", json={"email": "hello@bla.com"}
    )

    assert req.status == HTTPStatus.OK
    assert mock_cognito.async_resend_email_confirm.call_count == 1


async def test_resend_confirm_view_bad_data(
    cloud: MagicMock,
    setup_cloud: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test resend confirm bad data."""
    cloud_client = await hass_client()
    mock_cognito = cloud.auth

    req = await cloud_client.post(
        "/api/cloud/resend_confirm", json={"not_email": "hello@bla.com"}
    )

    assert req.status == HTTPStatus.BAD_REQUEST
    assert mock_cognito.async_resend_email_confirm.call_count == 0


async def test_resend_confirm_view_request_timeout(
    cloud: MagicMock,
    setup_cloud: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test timeout while resend confirm."""
    cloud_client = await hass_client()
    cloud.auth.async_resend_email_confirm.side_effect = TimeoutError

    req = await cloud_client.post(
        "/api/cloud/resend_confirm", json={"email": "hello@bla.com"}
    )

    assert req.status == HTTPStatus.BAD_GATEWAY


async def test_resend_confirm_view_unknown_error(
    cloud: MagicMock,
    setup_cloud: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test unknown error while resend confirm."""
    cloud_client = await hass_client()
    cloud.auth.async_resend_email_confirm.side_effect = UnknownError

    req = await cloud_client.post(
        "/api/cloud/resend_confirm", json={"email": "hello@bla.com"}
    )

    assert req.status == HTTPStatus.BAD_GATEWAY


async def test_websocket_remove_data(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    cloud: MagicMock,
    setup_cloud: None,
) -> None:
    """Test removing cloud data."""
    cloud.id_token = None
    client = await hass_ws_client(hass)

    with patch.object(cloud.client.prefs, "async_erase_config") as mock_erase_config:
        await client.send_json_auto_id({"type": "cloud/remove_data"})
        response = await client.receive_json()

        assert response["success"]
        cloud.remove_data.assert_awaited_once_with()
        mock_erase_config.assert_awaited_once_with()


async def test_websocket_remove_data_logged_in(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    cloud: MagicMock,
    setup_cloud: None,
) -> None:
    """Test removing cloud data."""
    cloud.iot.state = STATE_CONNECTED
    client = await hass_ws_client(hass)

    await client.send_json_auto_id({"type": "cloud/remove_data"})
    response = await client.receive_json()

    assert not response["success"]
    assert response["error"] == {
        "code": "logged_in",
        "message": "Can't remove data when logged in.",
    }


async def test_websocket_status(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    cloud: MagicMock,
    setup_cloud: None,
) -> None:
    """Test querying the status."""
    cloud.iot.state = STATE_CONNECTED
    client = await hass_ws_client(hass)

    with (
        patch.dict(
            "homeassistant.components.google_assistant.const.DOMAIN_TO_GOOGLE_TYPES",
            {"light": None},
            clear=True,
        ),
        patch.dict(
            "homeassistant.components.alexa.entities.ENTITY_ADAPTERS",
            {"switch": None},
            clear=True,
        ),
    ):
        await client.send_json({"id": 5, "type": "cloud/status"})
        response = await client.receive_json()

    assert response["result"] == {
        "logged_in": True,
        "email": "hello@home-assistant.io",
        "cloud": "connected",
        "cloud_last_disconnect_reason": None,
        "prefs": {
            "alexa_enabled": True,
            "cloudhooks": {},
            "google_enabled": True,
            "google_secure_devices_pin": None,
            "google_default_expose": DEFAULT_EXPOSED_DOMAINS,
            "alexa_default_expose": DEFAULT_EXPOSED_DOMAINS,
            "alexa_report_state": True,
            "google_report_state": True,
            "remote_allow_remote_enable": True,
            "remote_enabled": False,
            "cloud_ice_servers_enabled": True,
            "tts_default_voice": ["en-US", "JennyNeural"],
        },
        "alexa_entities": {
            "include_domains": [],
            "include_entity_globs": [],
            "include_entities": ["light.kitchen", "switch.ac"],
            "exclude_domains": [],
            "exclude_entity_globs": [],
            "exclude_entities": [],
        },
        "alexa_registered": False,
        "google_entities": {
            "include_domains": ["light"],
            "include_entity_globs": [],
            "include_entities": [],
            "exclude_domains": [],
            "exclude_entity_globs": [],
            "exclude_entities": [],
        },
        "google_registered": False,
        "google_local_connected": False,
        "remote_domain": None,
        "remote_connected": False,
        "remote_certificate_status": None,
        "remote_certificate": None,
        "http_use_ssl": False,
        "active_subscription": True,
    }


async def test_websocket_status_not_logged_in(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    cloud: MagicMock,
    setup_cloud: None,
) -> None:
    """Test querying the status not logged in."""
    cloud.id_token = None
    client = await hass_ws_client(hass)

    await client.send_json({"id": 5, "type": "cloud/status"})
    response = await client.receive_json()

    assert response["result"] == {
        "logged_in": False,
        "cloud": "disconnected",
        "http_use_ssl": False,
    }


async def test_websocket_subscription_info(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    aioclient_mock: AiohttpClientMocker,
    cloud: MagicMock,
    setup_cloud: None,
) -> None:
    """Test subscription info."""
    cloud.payments.subscription_info.return_value = {"provider": "stripe"}
    client = await hass_ws_client(hass)

    await client.send_json({"id": 5, "type": "cloud/subscription"})
    response = await client.receive_json()

    assert response["result"] == {"provider": "stripe"}


async def test_websocket_subscription_fail(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    aioclient_mock: AiohttpClientMocker,
    cloud: MagicMock,
    setup_cloud: None,
) -> None:
    """Test subscription info fail."""
    cloud.payments.subscription_info.side_effect = PaymentsApiError(
        "Failed to fetch subscription information"
    )
    client = await hass_ws_client(hass)

    await client.send_json({"id": 5, "type": "cloud/subscription"})
    response = await client.receive_json()

    assert not response["success"]
    assert response["error"]["code"] == "request_failed"


async def test_websocket_subscription_not_logged_in(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    cloud: MagicMock,
    setup_cloud: None,
) -> None:
    """Test subscription info not logged in."""
    cloud.id_token = None
    client = await hass_ws_client(hass)

    with patch(
        "hass_nabucasa.payments_api.PaymentsApi.subscription_info",
        return_value={"return": "value"},
    ):
        await client.send_json({"id": 5, "type": "cloud/subscription"})
        response = await client.receive_json()

    assert not response["success"]
    assert response["error"]["code"] == "not_logged_in"


async def test_websocket_update_preferences(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    cloud: MagicMock,
    setup_cloud: None,
) -> None:
    """Test updating preference."""
    assert cloud.client.prefs.google_enabled
    assert cloud.client.prefs.alexa_enabled
    assert cloud.client.prefs.google_secure_devices_pin is None
    assert cloud.client.prefs.remote_allow_remote_enable is True
    assert cloud.client.prefs.cloud_ice_servers_enabled is True

    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {
            "type": "cloud/update_prefs",
            "alexa_enabled": False,
            "google_enabled": False,
            "google_secure_devices_pin": "1234",
            "tts_default_voice": ["en-GB", "RyanNeural"],
            "remote_allow_remote_enable": False,
            "cloud_ice_servers_enabled": False,
        }
    )
    response = await client.receive_json()

    assert response["success"]
    assert not cloud.client.prefs.google_enabled
    assert not cloud.client.prefs.alexa_enabled
    assert cloud.client.prefs.google_secure_devices_pin == "1234"
    assert cloud.client.prefs.remote_allow_remote_enable is False
    assert cloud.client.prefs.cloud_ice_servers_enabled is False
    assert cloud.client.prefs.tts_default_voice == ("en-GB", "RyanNeural")


@pytest.mark.parametrize(
    ("language", "voice"), [("en-GB", "bad_voice"), ("bad_language", "RyanNeural")]
)
async def test_websocket_update_preferences_bad_voice(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    cloud: MagicMock,
    setup_cloud: None,
    language: str,
    voice: str,
) -> None:
    """Test updating preference."""
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {
            "type": "cloud/update_prefs",
            "tts_default_voice": [language, voice],
        }
    )
    response = await client.receive_json()

    assert not response["success"]
    assert response["error"]["code"] == ERR_INVALID_FORMAT
    assert cloud.client.prefs.tts_default_voice == ("en-US", "JennyNeural")


async def test_websocket_update_preferences_alexa_report_state(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    setup_cloud: None,
) -> None:
    """Test updating alexa_report_state sets alexa authorized."""
    client = await hass_ws_client(hass)

    with (
        patch(
            "homeassistant.components.cloud.alexa_config.CloudAlexaConfig.async_sync_entities"
        ),
        patch(
            (
                "homeassistant.components.cloud.alexa_config.CloudAlexaConfig"
                ".async_get_access_token"
            ),
        ),
        patch(
            "homeassistant.components.cloud.alexa_config.CloudAlexaConfig.set_authorized"
        ) as set_authorized_mock,
    ):
        set_authorized_mock.assert_not_called()

        await client.send_json(
            {"id": 5, "type": "cloud/update_prefs", "alexa_report_state": True}
        )
        response = await client.receive_json()

        set_authorized_mock.assert_called_once_with(True)
        await hass.async_block_till_done()

    assert response["success"]


async def test_websocket_update_preferences_require_relink(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    setup_cloud: None,
) -> None:
    """Test updating preference requires relink."""
    client = await hass_ws_client(hass)

    with (
        patch(
            (
                "homeassistant.components.cloud.alexa_config.CloudAlexaConfig"
                ".async_get_access_token"
            ),
            side_effect=alexa_errors.RequireRelink,
        ),
        patch(
            "homeassistant.components.cloud.alexa_config.CloudAlexaConfig.set_authorized"
        ) as set_authorized_mock,
    ):
        set_authorized_mock.assert_not_called()

        await client.send_json(
            {"id": 5, "type": "cloud/update_prefs", "alexa_report_state": True}
        )
        response = await client.receive_json()

        set_authorized_mock.assert_called_once_with(False)

    assert not response["success"]
    assert response["error"]["code"] == "alexa_relink"


async def test_websocket_update_preferences_no_token(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    setup_cloud: None,
) -> None:
    """Test updating preference no token available."""
    client = await hass_ws_client(hass)

    with (
        patch(
            (
                "homeassistant.components.cloud.alexa_config.CloudAlexaConfig"
                ".async_get_access_token"
            ),
            side_effect=alexa_errors.NoTokenAvailable,
        ),
        patch(
            "homeassistant.components.cloud.alexa_config.CloudAlexaConfig.set_authorized"
        ) as set_authorized_mock,
    ):
        set_authorized_mock.assert_not_called()

        await client.send_json(
            {"id": 5, "type": "cloud/update_prefs", "alexa_report_state": True}
        )
        response = await client.receive_json()

        set_authorized_mock.assert_called_once_with(False)

    assert not response["success"]
    assert response["error"]["code"] == "alexa_relink"


async def test_enabling_webhook(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    cloud: MagicMock,
    setup_cloud: None,
) -> None:
    """Test we call right code to enable webhooks."""
    client = await hass_ws_client(hass)
    mock_enable = cloud.cloudhooks.async_create
    mock_enable.return_value = {}

    await client.send_json(
        {"id": 5, "type": "cloud/cloudhook/create", "webhook_id": "mock-webhook-id"}
    )
    response = await client.receive_json()

    assert response["success"]
    assert mock_enable.call_count == 1
    assert mock_enable.mock_calls[0][1][0] == "mock-webhook-id"


async def test_disabling_webhook(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    cloud: MagicMock,
    setup_cloud: None,
) -> None:
    """Test we call right code to disable webhooks."""
    client = await hass_ws_client(hass)
    mock_disable = cloud.cloudhooks.async_delete

    await client.send_json(
        {"id": 5, "type": "cloud/cloudhook/delete", "webhook_id": "mock-webhook-id"}
    )
    response = await client.receive_json()

    assert response["success"]
    assert mock_disable.call_count == 1
    assert mock_disable.mock_calls[0][1][0] == "mock-webhook-id"


async def test_enabling_remote(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    cloud: MagicMock,
    setup_cloud: None,
) -> None:
    """Test we call right code to enable remote UI."""
    client = await hass_ws_client(hass)
    mock_connect = cloud.remote.connect
    assert not cloud.client.remote_autostart

    await client.send_json({"id": 5, "type": "cloud/remote/connect"})
    response = await client.receive_json()

    assert response["success"]
    assert cloud.client.remote_autostart
    assert mock_connect.call_count == 1

    mock_disconnect = cloud.remote.disconnect

    await client.send_json({"id": 6, "type": "cloud/remote/disconnect"})
    response = await client.receive_json()

    assert response["success"]
    assert not cloud.client.remote_autostart
    assert mock_disconnect.call_count == 1


async def test_enabling_remote_remote_activation_not_allowed(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    cloud: MagicMock,
    setup_cloud: None,
) -> None:
    """Test we can enable remote UI locally when blocked remotely."""
    client = await hass_ws_client(hass)
    mock_connect = cloud.remote.connect
    assert not cloud.client.remote_autostart
    await cloud.client.prefs.async_update(remote_allow_remote_enable=False)

    await client.send_json({"id": 5, "type": "cloud/remote/connect"})
    response = await client.receive_json()

    assert response["success"]
    assert cloud.client.remote_autostart
    assert mock_connect.call_count == 1

    mock_disconnect = cloud.remote.disconnect

    await client.send_json({"id": 6, "type": "cloud/remote/disconnect"})
    response = await client.receive_json()

    assert response["success"]
    assert not cloud.client.remote_autostart
    assert mock_disconnect.call_count == 1


async def test_list_google_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    hass_ws_client: WebSocketGenerator,
    setup_cloud: None,
) -> None:
    """Test that we can list Google entities."""
    client = await hass_ws_client(hass)
    entity = GoogleEntity(
        hass, MockConfig(should_expose=lambda *_: False), State("light.kitchen", "on")
    )
    entity2 = GoogleEntity(
        hass,
        MockConfig(should_expose=lambda *_: True, should_2fa=lambda *_: False),
        State("cover.garage", "open", {"device_class": "garage"}),
    )
    with patch(
        "homeassistant.components.google_assistant.helpers.async_get_entities",
        return_value=[entity, entity2],
    ):
        await client.send_json_auto_id({"type": "cloud/google_assistant/entities"})
        response = await client.receive_json()

    assert response["success"]
    assert len(response["result"]) == 2
    assert response["result"][0] == {
        "entity_id": "light.kitchen",
        "might_2fa": False,
        "traits": ["action.devices.traits.OnOff"],
    }
    assert response["result"][1] == {
        "entity_id": "cover.garage",
        "might_2fa": True,
        "traits": ["action.devices.traits.OpenClose"],
    }

    # Add the entities to the entity registry
    entity_registry.async_get_or_create(
        "light", "test", "unique", suggested_object_id="kitchen"
    )
    entity_registry.async_get_or_create(
        "cover", "test", "unique", suggested_object_id="garage"
    )

    with patch(
        "homeassistant.components.google_assistant.helpers.async_get_entities",
        return_value=[entity, entity2],
    ):
        await client.send_json_auto_id({"type": "cloud/google_assistant/entities"})
        response = await client.receive_json()

    assert response["success"]
    assert len(response["result"]) == 2
    assert response["result"][0] == {
        "entity_id": "light.kitchen",
        "might_2fa": False,
        "traits": ["action.devices.traits.OnOff"],
    }
    assert response["result"][1] == {
        "entity_id": "cover.garage",
        "might_2fa": True,
        "traits": ["action.devices.traits.OpenClose"],
    }


async def test_get_google_entity(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    hass_ws_client: WebSocketGenerator,
    setup_cloud: None,
) -> None:
    """Test that we can get a Google entity."""
    client = await hass_ws_client(hass)

    # Test getting an unknown entity
    await client.send_json_auto_id(
        {"type": "cloud/google_assistant/entities/get", "entity_id": "light.kitchen"}
    )
    response = await client.receive_json()

    assert not response["success"]
    assert response["error"] == {
        "code": "not_found",
        "message": "light.kitchen unknown",
    }

    # Test getting a blocked entity
    entity_registry.async_get_or_create(
        "group", "test", "unique", suggested_object_id="all_locks"
    )
    hass.states.async_set("group.all_locks", "bla")

    await client.send_json_auto_id(
        {"type": "cloud/google_assistant/entities/get", "entity_id": "group.all_locks"}
    )
    response = await client.receive_json()

    assert not response["success"]
    assert response["error"] == {
        "code": "not_supported",
        "message": "group.all_locks not supported by Google assistant",
    }

    entity_registry.async_get_or_create(
        "light", "test", "unique", suggested_object_id="kitchen"
    )
    hass.states.async_set("light.kitchen", "on")
    hass.states.async_set("cover.garage", "open", {"device_class": "garage"})

    await client.send_json_auto_id(
        {"type": "cloud/google_assistant/entities/get", "entity_id": "light.kitchen"}
    )
    response = await client.receive_json()

    assert response["success"]
    assert response["result"] == {
        "disable_2fa": None,
        "entity_id": "light.kitchen",
        "might_2fa": False,
        "traits": ["action.devices.traits.OnOff"],
    }

    await client.send_json_auto_id(
        {"type": "cloud/google_assistant/entities/get", "entity_id": "cover.garage"}
    )
    response = await client.receive_json()

    assert response["success"]
    assert response["result"] == {
        "disable_2fa": None,
        "entity_id": "cover.garage",
        "might_2fa": True,
        "traits": ["action.devices.traits.OpenClose"],
    }

    # Set the disable 2fa flag
    await client.send_json_auto_id(
        {
            "type": "cloud/google_assistant/entities/update",
            "entity_id": "cover.garage",
            "disable_2fa": True,
        }
    )
    response = await client.receive_json()

    assert response["success"]

    await client.send_json_auto_id(
        {"type": "cloud/google_assistant/entities/get", "entity_id": "cover.garage"}
    )
    response = await client.receive_json()

    assert response["success"]
    assert response["result"] == {
        "disable_2fa": True,
        "entity_id": "cover.garage",
        "might_2fa": True,
        "traits": ["action.devices.traits.OpenClose"],
    }


async def test_update_google_entity(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    setup_cloud: None,
) -> None:
    """Test that we can update config of a Google entity."""
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {
            "type": "cloud/google_assistant/entities/update",
            "entity_id": "light.kitchen",
            "disable_2fa": False,
        }
    )
    response = await client.receive_json()

    assert response["success"]

    await client.send_json_auto_id(
        {
            "type": "homeassistant/expose_entity",
            "assistants": ["cloud.google_assistant"],
            "entity_ids": ["light.kitchen"],
            "should_expose": False,
        }
    )
    response = await client.receive_json()

    assert response["success"]
    assert exposed_entities.async_get_entity_settings(hass, "light.kitchen") == {
        "cloud.google_assistant": {"disable_2fa": False, "should_expose": False}
    }


async def test_list_alexa_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    hass_ws_client: WebSocketGenerator,
    setup_cloud: None,
) -> None:
    """Test that we can list Alexa entities."""
    client = await hass_ws_client(hass)
    entity = LightCapabilities(
        hass, MagicMock(entity_config={}), State("light.kitchen", "on")
    )
    with patch(
        "homeassistant.components.alexa.entities.async_get_entities",
        return_value=[entity],
    ):
        await client.send_json_auto_id({"id": 5, "type": "cloud/alexa/entities"})
        response = await client.receive_json()

    assert response["success"]
    assert len(response["result"]) == 1
    assert response["result"][0] == {
        "entity_id": "light.kitchen",
        "display_categories": ["LIGHT"],
        "interfaces": ["Alexa.PowerController", "Alexa.EndpointHealth", "Alexa"],
    }

    with (
        patch(
            (
                "homeassistant.components.cloud.alexa_config.CloudAlexaConfig"
                ".async_get_access_token"
            ),
        ),
        patch(
            "homeassistant.components.cloud.alexa_config.alexa_state_report.async_send_add_or_update_message"
        ),
    ):
        # Add the entity to the entity registry
        entity_registry.async_get_or_create(
            "light", "test", "unique", suggested_object_id="kitchen"
        )
        await hass.async_block_till_done()

    with patch(
        "homeassistant.components.alexa.entities.async_get_entities",
        return_value=[entity],
    ):
        await client.send_json_auto_id({"type": "cloud/alexa/entities"})
        response = await client.receive_json()

    assert response["success"]
    assert len(response["result"]) == 1
    assert response["result"][0] == {
        "entity_id": "light.kitchen",
        "display_categories": ["LIGHT"],
        "interfaces": ["Alexa.PowerController", "Alexa.EndpointHealth", "Alexa"],
    }


async def test_get_alexa_entity(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    hass_ws_client: WebSocketGenerator,
    setup_cloud: None,
) -> None:
    """Test that we can get an Alexa entity."""
    client = await hass_ws_client(hass)

    # Test getting an unknown entity
    await client.send_json_auto_id(
        {"type": "cloud/alexa/entities/get", "entity_id": "light.kitchen"}
    )
    response = await client.receive_json()

    assert response["success"]
    assert response["result"] is None

    # Test getting an unknown sensor
    await client.send_json_auto_id(
        {"type": "cloud/alexa/entities/get", "entity_id": "sensor.temperature"}
    )
    response = await client.receive_json()

    assert not response["success"]
    assert response["error"] == {
        "code": "not_supported",
        "message": "sensor.temperature not supported by Alexa",
    }

    # Test getting a blocked entity
    entity_registry.async_get_or_create(
        "group", "test", "unique", suggested_object_id="all_locks"
    )
    hass.states.async_set("group.all_locks", "bla")

    await client.send_json_auto_id(
        {"type": "cloud/alexa/entities/get", "entity_id": "group.all_locks"}
    )
    response = await client.receive_json()

    assert not response["success"]
    assert response["error"] == {
        "code": "not_supported",
        "message": "group.all_locks not supported by Alexa",
    }

    entity_registry.async_get_or_create(
        "light", "test", "unique", suggested_object_id="kitchen"
    )
    entity_registry.async_get_or_create(
        "water_heater", "test", "unique", suggested_object_id="basement"
    )

    await client.send_json_auto_id(
        {"type": "cloud/alexa/entities/get", "entity_id": "light.kitchen"}
    )
    response = await client.receive_json()

    assert response["success"]
    assert response["result"] is None

    await client.send_json_auto_id(
        {"type": "cloud/alexa/entities/get", "entity_id": "water_heater.basement"}
    )
    response = await client.receive_json()

    assert not response["success"]
    assert response["error"] == {
        "code": "not_supported",
        "message": "water_heater.basement not supported by Alexa",
    }


async def test_update_alexa_entity(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    hass_ws_client: WebSocketGenerator,
    setup_cloud: None,
) -> None:
    """Test that we can update config of an Alexa entity."""
    entry = entity_registry.async_get_or_create(
        "light", "test", "unique", suggested_object_id="kitchen"
    )
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {
            "type": "homeassistant/expose_entity",
            "assistants": ["cloud.alexa"],
            "entity_ids": [entry.entity_id],
            "should_expose": False,
        }
    )
    response = await client.receive_json()

    assert response["success"]
    assert exposed_entities.async_get_entity_settings(hass, entry.entity_id) == {
        "cloud.alexa": {"should_expose": False}
    }


async def test_sync_alexa_entities_timeout(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    setup_cloud: None,
) -> None:
    """Test that timeout syncing Alexa entities."""
    client = await hass_ws_client(hass)

    with patch(
        (
            "homeassistant.components.cloud.alexa_config.CloudAlexaConfig"
            ".async_sync_entities"
        ),
        side_effect=TimeoutError,
    ):
        await client.send_json({"id": 5, "type": "cloud/alexa/sync"})
        response = await client.receive_json()

    assert not response["success"]
    assert response["error"]["code"] == "timeout"


async def test_sync_alexa_entities_no_token(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    setup_cloud: None,
) -> None:
    """Test sync Alexa entities when we have no token."""
    client = await hass_ws_client(hass)

    with patch(
        (
            "homeassistant.components.cloud.alexa_config.CloudAlexaConfig"
            ".async_sync_entities"
        ),
        side_effect=alexa_errors.NoTokenAvailable,
    ):
        await client.send_json({"id": 5, "type": "cloud/alexa/sync"})
        response = await client.receive_json()

    assert not response["success"]
    assert response["error"]["code"] == "alexa_relink"


async def test_enable_alexa_state_report_fail(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    setup_cloud: None,
) -> None:
    """Test enable Alexa entities state reporting when no token available."""
    client = await hass_ws_client(hass)

    with patch(
        (
            "homeassistant.components.cloud.alexa_config.CloudAlexaConfig"
            ".async_sync_entities"
        ),
        side_effect=alexa_errors.NoTokenAvailable,
    ):
        await client.send_json({"id": 5, "type": "cloud/alexa/sync"})
        response = await client.receive_json()

    assert not response["success"]
    assert response["error"]["code"] == "alexa_relink"


async def test_tts_info(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    setup_cloud: None,
) -> None:
    """Test that we can get TTS info."""
    client = await hass_ws_client(hass)

    await client.send_json_auto_id({"type": "cloud/tts/info"})
    response = await client.receive_json()

    assert response["success"]
    assert "languages" in response["result"]
    assert all(len(lang) for lang in response["result"]["languages"])
    assert len(response["result"]["languages"]) > 300
    assert (
        len([lang for lang in response["result"]["languages"] if "||" in lang[1]]) > 100
    )
    for lang in response["result"]["languages"]:
        assert validate_language_voice(lang[:2])


@pytest.mark.parametrize(
    ("endpoint", "data"),
    [
        ("/api/cloud/forgot_password", {"email": "fake@example.com"}),
        ("/api/cloud/google_actions/sync", None),
        ("/api/cloud/login", {"email": "fake@example.com", "password": "secret"}),
        ("/api/cloud/logout", None),
        ("/api/cloud/register", {"email": "fake@example.com", "password": "secret"}),
        ("/api/cloud/resend_confirm", {"email": "fake@example.com"}),
    ],
)
async def test_api_calls_require_admin(
    hass: HomeAssistant,
    setup_cloud: None,
    hass_client: ClientSessionGenerator,
    hass_read_only_access_token: str,
    endpoint: str,
    data: dict[str, Any] | None,
) -> None:
    """Test cloud APIs endpoints do not work as a normal user."""
    client = await hass_client(hass_read_only_access_token)
    resp = await client.post(endpoint, json=data)

    assert resp.status == HTTPStatus.UNAUTHORIZED


async def test_login_view_dispatch_event(
    hass: HomeAssistant,
    cloud: MagicMock,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test dispatching event while logging in."""
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, DOMAIN, {"cloud": {}})
    await hass.async_block_till_done()

    cloud_client = await hass_client()

    with patch(
        "homeassistant.components.cloud.http_api.async_dispatcher_send"
    ) as async_dispatcher_send_mock:
        await cloud_client.post(
            "/api/cloud/login", json={"email": "my_username", "password": "my_password"}
        )

    assert async_dispatcher_send_mock.call_count == 1
    assert async_dispatcher_send_mock.mock_calls[0][1][1] == "cloud_event"
    assert async_dispatcher_send_mock.mock_calls[0][1][2] == {"type": "login"}


async def test_logout_view_dispatch_event(
    cloud: MagicMock,
    setup_cloud: None,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test dispatching event while logging out."""
    cloud_client = await hass_client()

    with patch(
        "homeassistant.components.cloud.http_api.async_dispatcher_send"
    ) as async_dispatcher_send_mock:
        await cloud_client.post("/api/cloud/logout")

    assert async_dispatcher_send_mock.call_count == 1
    assert async_dispatcher_send_mock.mock_calls[0][1][1] == "cloud_event"
    assert async_dispatcher_send_mock.mock_calls[0][1][2] == {"type": "logout"}


@patch("homeassistant.components.cloud.helpers.FixedSizeQueueLogHandler.MAX_RECORDS", 3)
async def test_download_support_package(
    hass: HomeAssistant,
    cloud: MagicMock,
    set_cloud_prefs: Callable[[dict[str, Any]], Coroutine[Any, Any, None]],
    hass_client: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    snapshot: SnapshotAssertion,
) -> None:
    """Test downloading a support package file."""

    aioclient_mock.get("https://cloud.bla.com/status", text="")
    aioclient_mock.get(
        "https://cert-server/directory", exc=Exception("Unexpected exception")
    )
    aioclient_mock.get(
        "https://cognito-idp.us-east-1.amazonaws.com/AAAA/.well-known/jwks.json",
        exc=aiohttp.ClientError,
    )

    def async_register_mock_platform(
        hass: HomeAssistant, register: system_health.SystemHealthRegistration
    ) -> None:
        async def mock_empty_info(hass: HomeAssistant) -> dict[str, Any]:
            return {}

        register.async_register_info(mock_empty_info, "/config/mock_integration")

    mock_platform(
        hass,
        "mock_no_info_integration.system_health",
        MagicMock(async_register=async_register_mock_platform),
    )
    hass.config.components.add("mock_no_info_integration")

    assert await async_setup_component(hass, "system_health", {})

    with patch("uuid.UUID.hex", new_callable=PropertyMock) as hexmock:
        hexmock.return_value = "12345678901234567890"
        assert await async_setup_component(
            hass,
            DOMAIN,
            {
                DOMAIN: {
                    "user_pool_id": "AAAA",
                    "region": "us-east-1",
                    "acme_server": "cert-server",
                    "relayer_server": "cloud.bla.com",
                },
            },
        )
        await hass.async_block_till_done()

    await cloud.login("test-user", "test-pass")

    cloud.remote.snitun_server = "us-west-1"
    cloud.remote.certificate_status = CertificateStatus.READY
    cloud.expiration_date = dt_util.parse_datetime("2025-01-17T11:19:31.0+00:00")

    await cloud.client.async_system_message({"region": "xx-earth-616"})
    await set_cloud_prefs(
        {
            "alexa_enabled": True,
            "google_enabled": False,
            "remote_enabled": True,
            "cloud_ice_servers_enabled": True,
        }
    )

    now = dt_util.utcnow()
    # The logging is done with local time according to the system timezone. Set the
    # fake time to 12:00 local time
    tz = now.astimezone().tzinfo
    freezer.move_to(datetime.datetime(2025, 2, 10, 12, 0, 0, tzinfo=tz))
    logging.getLogger("hass_nabucasa.iot").info(
        "This message will be dropped since this test patches MAX_RECORDS"
    )
    logging.getLogger("hass_nabucasa.iot").info("Hass nabucasa log")
    logging.getLogger("snitun.utils.aiohttp_client").warning("Snitun log")
    logging.getLogger("homeassistant.components.cloud.client").error("Cloud log")
    freezer.move_to(now)  # Reset time otherwise hass_client auth fails

    cloud_client = await hass_client()
    with (
        patch.object(hass.config, "config_dir", new="config"),
        patch(
            "homeassistant.components.homeassistant.system_health.system_info.async_get_system_info",
            return_value={
                "installation_type": "Home Assistant Core",
                "version": "2025.2.0",
                "dev": False,
                "hassio": False,
                "virtualenv": False,
                "python_version": "3.13.1",
                "docker": False,
                "container_arch": None,
                "arch": "x86_64",
                "timezone": "US/Pacific",
                "os_name": "Linux",
                "os_version": "6.12.9",
                "user": "hass",
            },
        ),
    ):
        req = await cloud_client.get("/api/cloud/support_package")
    assert req.status == HTTPStatus.OK
    assert await req.text() == snapshot
