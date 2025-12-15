"""
reddit_auth.py - Reddit OAuth authentication using refresh_token flow.

Handles:
- Exchanging refresh_token for access_token
- Caching access_token until expiration
- Thread-safe token refresh
"""

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

import requests

from .config import Config, mask_secret

logger = logging.getLogger(__name__)


class AuthError(Exception):
    """Raised when authentication fails."""
    pass


@dataclass
class TokenInfo:
    """Holds access token information."""
    access_token: str
    token_type: str
    expires_at: float  # Unix timestamp when token expires
    scope: str


class RedditAuth:
    """
    Manages Reddit OAuth authentication.

    Uses the refresh_token grant type to obtain access tokens.
    Tokens are cached and automatically refreshed before expiration.
    """

    TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
    # Refresh token 5 minutes before actual expiration
    EXPIRY_BUFFER_SEC = 300

    def __init__(self, config: Config):
        self.config = config
        self._token_info: Optional[TokenInfo] = None
        self._lock = threading.Lock()

        # Auth credentials
        self._client_id = config.reddit.auth.client_id
        self._client_secret = config.reddit.auth.client_secret
        self._refresh_token = config.reddit.auth.refresh_token
        self._user_agent = config.reddit.user_agent

    def _request_token(self) -> TokenInfo:
        """
        Request a new access token using refresh_token.

        Returns:
            TokenInfo with the new access token

        Raises:
            AuthError: If token request fails
        """
        logger.info("Requesting new access token using refresh_token")

        headers = {
            "User-Agent": self._user_agent,
        }

        data = {
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
        }

        try:
            response = requests.post(
                self.TOKEN_URL,
                headers=headers,
                data=data,
                auth=(self._client_id, self._client_secret),
                timeout=30,
            )

            # Log response status (but not body which may contain tokens)
            logger.debug(f"Token request response status: {response.status_code}")

            if response.status_code == 401:
                raise AuthError(
                    "Authentication failed: Invalid client credentials. "
                    "Check client_id and client_secret."
                )

            if response.status_code == 400:
                error_data = response.json()
                error = error_data.get("error", "unknown")
                if error == "invalid_grant":
                    raise AuthError(
                        "Authentication failed: Invalid or expired refresh_token. "
                        "You may need to reauthorize the application."
                    )
                raise AuthError(f"Token request failed: {error}")

            response.raise_for_status()

            token_data = response.json()

            # Calculate expiration time
            expires_in = token_data.get("expires_in", 3600)
            expires_at = time.time() + expires_in

            token_info = TokenInfo(
                access_token=token_data["access_token"],
                token_type=token_data.get("token_type", "bearer"),
                expires_at=expires_at,
                scope=token_data.get("scope", ""),
            )

            logger.info(
                f"Successfully obtained access token "
                f"(expires in {expires_in}s, scope: {token_info.scope})"
            )

            return token_info

        except requests.exceptions.RequestException as e:
            # Don't log the full exception which might contain sensitive data
            logger.error(f"Network error during token request: {type(e).__name__}")
            raise AuthError(f"Failed to request token: {type(e).__name__}") from e

    def get_access_token(self) -> str:
        """
        Get a valid access token, refreshing if necessary.

        This method is thread-safe.

        Returns:
            Valid access token string

        Raises:
            AuthError: If unable to obtain a valid token
        """
        with self._lock:
            # Check if we have a valid cached token
            if self._token_info is not None:
                # Check if token is still valid (with buffer)
                if time.time() < (self._token_info.expires_at - self.EXPIRY_BUFFER_SEC):
                    logger.debug("Using cached access token")
                    return self._token_info.access_token
                else:
                    logger.info("Cached token expired or expiring soon, refreshing")

            # Request new token
            self._token_info = self._request_token()
            return self._token_info.access_token

    def invalidate_token(self) -> None:
        """
        Invalidate the cached token, forcing a refresh on next request.

        Call this when receiving a 401 response from the API.
        """
        with self._lock:
            logger.info("Invalidating cached access token")
            self._token_info = None

    def get_auth_header(self) -> dict[str, str]:
        """
        Get the Authorization header for API requests.

        Returns:
            Dict with Authorization header
        """
        token = self.get_access_token()
        return {"Authorization": f"bearer {token}"}

    @property
    def is_token_valid(self) -> bool:
        """Check if we have a valid cached token."""
        with self._lock:
            if self._token_info is None:
                return False
            return time.time() < (self._token_info.expires_at - self.EXPIRY_BUFFER_SEC)
