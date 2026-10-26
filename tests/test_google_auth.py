"""Tests for Google OAuth verification and fallback."""
from unittest.mock import patch, MagicMock
import pytest
import httpx
from app.config import settings, Settings
from app.models.user import User


def test_clean_google_client_id_validator():
    """Verify google_client_id validator trims quotes and whitespace."""
    s = Settings(google_client_id='  "apps.googleusercontent.com"  \n')
    assert s.google_client_id == "apps.googleusercontent.com"

    s2 = Settings(google_client_id="'apps.googleusercontent.com'")
    assert s2.google_client_id == "apps.googleusercontent.com"

    s3 = Settings(google_client_id=None)
    assert s3.google_client_id == ""


@pytest.mark.asyncio
async def test_google_auth_success_direct(client: httpx.AsyncClient):
    """Verify successful Google authentication using verify_oauth2_token."""
    fake_idinfo = {
        "sub": "google-12345",
        "email": "testgoogle@taskflow.dev",
        "name": "Google User",
        "aud": settings.google_client_id or "client-id-123",
    }

    with patch("google.oauth2.id_token.verify_oauth2_token", return_value=fake_idinfo) as mock_verify:
        response = await client.post(
            "/api/v1/auth/google",
            json={"credential": "valid_mock_token"},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert "access_token" in data
        assert data["user"]["email"] == "testgoogle@taskflow.dev"
        assert data["user"]["name"] == "Google User"
        mock_verify.assert_called_once()
        # Verify clock_skew_in_seconds=10 passed
        assert mock_verify.call_args.kwargs.get("clock_skew_in_seconds") == 10


@pytest.mark.asyncio
async def test_google_auth_fallback_tokeninfo_success(client: httpx.AsyncClient):
    """Verify fallback to Google tokeninfo REST API when library fails."""
    fake_tokeninfo = {
        "sub": "google-fallback-999",
        "email": "fallback@taskflow.dev",
        "name": "Fallback User",
        "aud": settings.google_client_id or "mock-client-id",
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = fake_tokeninfo

    with patch("google.oauth2.id_token.verify_oauth2_token", side_effect=Exception("Clock skew / verification failed")), \
         patch("httpx.AsyncClient.get", return_value=mock_resp):
        response = await client.post(
            "/api/v1/auth/google",
            json={"credential": "fallback_token"},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert "access_token" in data
        assert data["user"]["email"] == "fallback@taskflow.dev"


@pytest.mark.asyncio
async def test_google_auth_failure_both(client: httpx.AsyncClient):
    """Verify 401 Unauthorized with detailed message when both methods fail."""
    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.text = "Invalid Value"

    with patch("google.oauth2.id_token.verify_oauth2_token", side_effect=Exception("Expired token")), \
         patch("httpx.AsyncClient.get", return_value=mock_resp):
        response = await client.post(
            "/api/v1/auth/google",
            json={"credential": "invalid_token"},
        )
        assert response.status_code == 401
        assert "Invalid Google ID token" in response.json()["detail"]
