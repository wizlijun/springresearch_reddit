"""
reddit_client.py - Reddit API client with rate limiting and retry logic.

Handles:
- HTTP request execution with proper headers
- Rate limiting (QPM control + X-Ratelimit-* headers)
- Automatic retry with exponential backoff
- Token refresh on 401 errors
"""

import logging
import threading
import time
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import Config
from .reddit_auth import RedditAuth, AuthError

logger = logging.getLogger(__name__)


class RateLimitExceeded(Exception):
    """Raised when rate limit is exceeded and cannot be resolved."""
    pass


class APIError(Exception):
    """Raised for API errors."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class RedditClient:
    """
    Reddit API client with built-in rate limiting and retry logic.

    Features:
    - Automatic OAuth token management
    - Rate limiting based on config and response headers
    - Exponential backoff retry for transient errors
    - Thread-safe request execution
    """

    def __init__(self, config: Config, auth: RedditAuth):
        self.config = config
        self.auth = auth

        self._oauth_base = config.reddit.endpoints.oauth_base
        self._user_agent = config.reddit.user_agent
        self._timeout = config.network.timeout_sec
        self._max_retries = config.network.retries
        self._backoff_sec = config.network.backoff_sec
        self._proxy = config.network.proxy

        # Rate limiting
        self._max_qpm = config.rate_limit.max_qpm
        self._respect_headers = config.rate_limit.respect_response_headers
        self._safety_interval_ms = config.rate_limit.safety_min_interval_ms

        # Rate limit state
        self._rate_limit_lock = threading.Lock()
        self._last_request_time: float = 0
        self._remaining_requests: Optional[int] = None
        self._reset_time: Optional[float] = None

        # Request counter for QPM tracking
        self._request_times: list[float] = []

        # Session setup
        self._session = self._create_session()

    def _create_session(self) -> requests.Session:
        """Create a requests session with retry configuration."""
        session = requests.Session()

        # Configure proxies if specified
        if self._proxy:
            session.proxies = {
                "http": self._proxy,
                "https": self._proxy,
            }
            logger.info(f"Using proxy: {self._proxy}")

        return session

    def _wait_for_rate_limit(self) -> None:
        """
        Wait if necessary to respect rate limits.

        Handles:
        - Safety minimum interval between requests
        - QPM (queries per minute) limit
        - X-Ratelimit-* header-based limits
        """
        with self._rate_limit_lock:
            now = time.time()

            # 1. Enforce safety minimum interval
            elapsed_ms = (now - self._last_request_time) * 1000
            if elapsed_ms < self._safety_interval_ms:
                sleep_ms = self._safety_interval_ms - elapsed_ms
                logger.debug(f"Rate limit: sleeping {sleep_ms:.0f}ms (safety interval)")
                time.sleep(sleep_ms / 1000)
                now = time.time()

            # 2. Check QPM limit
            # Remove request times older than 1 minute
            one_minute_ago = now - 60
            self._request_times = [t for t in self._request_times if t > one_minute_ago]

            if len(self._request_times) >= self._max_qpm:
                # Wait until oldest request falls out of the window
                wait_until = self._request_times[0] + 60
                sleep_sec = wait_until - now
                if sleep_sec > 0:
                    logger.info(f"Rate limit: sleeping {sleep_sec:.1f}s (QPM limit reached)")
                    time.sleep(sleep_sec)
                    now = time.time()
                    # Clean up again after sleeping
                    one_minute_ago = now - 60
                    self._request_times = [t for t in self._request_times if t > one_minute_ago]

            # 3. Check X-Ratelimit headers (if enabled and available)
            if self._respect_headers and self._remaining_requests is not None:
                if self._remaining_requests <= 1 and self._reset_time is not None:
                    sleep_sec = self._reset_time - now
                    if sleep_sec > 0:
                        logger.info(
                            f"Rate limit: sleeping {sleep_sec:.1f}s "
                            f"(X-Ratelimit-Remaining: {self._remaining_requests})"
                        )
                        time.sleep(sleep_sec)
                        now = time.time()

            # Record this request time
            self._request_times.append(now)
            self._last_request_time = now

    def _update_rate_limit_from_headers(self, response: requests.Response) -> None:
        """Update rate limit state from response headers."""
        if not self._respect_headers:
            return

        with self._rate_limit_lock:
            # Reddit uses X-Ratelimit-Remaining and X-Ratelimit-Reset
            remaining = response.headers.get("X-Ratelimit-Remaining")
            reset = response.headers.get("X-Ratelimit-Reset")

            if remaining is not None:
                try:
                    self._remaining_requests = int(float(remaining))
                except ValueError:
                    pass

            if reset is not None:
                try:
                    # Reset is seconds until reset
                    self._reset_time = time.time() + int(float(reset))
                except ValueError:
                    pass

            if self._remaining_requests is not None:
                logger.debug(
                    f"Rate limit headers: remaining={self._remaining_requests}, "
                    f"reset_in={reset}s"
                )

    def _should_retry(self, status_code: int, attempt: int) -> bool:
        """Determine if request should be retried based on status code."""
        if attempt >= self._max_retries:
            return False

        # Retry on server errors and rate limit
        return status_code in (429, 500, 502, 503, 504)

    def _calculate_backoff(self, attempt: int, response: Optional[requests.Response] = None) -> float:
        """Calculate backoff time for retry."""
        # Check for Retry-After header
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    return float(retry_after)
                except ValueError:
                    pass

        # Exponential backoff
        return self._backoff_sec * (2 ** attempt)

    def request(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict] = None,
        data: Optional[dict] = None,
        json_data: Optional[dict] = None,
    ) -> dict[str, Any]:
        """
        Make an authenticated request to the Reddit OAuth API.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (e.g., /api/multi/user/xxx/m/yyy)
            params: Query parameters
            data: Form data
            json_data: JSON body data

        Returns:
            Parsed JSON response

        Raises:
            APIError: For API errors
            AuthError: For authentication errors
            RateLimitExceeded: If rate limit cannot be resolved
        """
        url = f"{self._oauth_base}{endpoint}"

        attempt = 0
        token_refreshed = False

        while True:
            # Wait for rate limit
            self._wait_for_rate_limit()

            # Get auth header
            headers = self.auth.get_auth_header()
            headers["User-Agent"] = self._user_agent

            try:
                logger.debug(f"Request: {method} {endpoint}")

                response = self._session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params,
                    data=data,
                    json=json_data,
                    timeout=self._timeout,
                )

                # Update rate limit state from headers
                self._update_rate_limit_from_headers(response)

                # Handle response
                if response.status_code == 200:
                    return response.json()

                if response.status_code == 401:
                    # Token expired - try to refresh once
                    if not token_refreshed:
                        logger.info("Received 401, attempting token refresh")
                        self.auth.invalidate_token()
                        token_refreshed = True
                        continue
                    else:
                        raise AuthError("Authentication failed after token refresh")

                if response.status_code == 403:
                    raise APIError(
                        f"Access forbidden: {response.text}",
                        status_code=403
                    )

                if response.status_code == 404:
                    raise APIError(
                        f"Not found: {endpoint}",
                        status_code=404
                    )

                if response.status_code == 429:
                    if self._should_retry(response.status_code, attempt):
                        backoff = self._calculate_backoff(attempt, response)
                        logger.warning(
                            f"Rate limited (429), retrying in {backoff:.1f}s "
                            f"(attempt {attempt + 1}/{self._max_retries})"
                        )
                        time.sleep(backoff)
                        attempt += 1
                        continue
                    else:
                        raise RateLimitExceeded(
                            "Rate limit exceeded and max retries reached"
                        )

                if self._should_retry(response.status_code, attempt):
                    backoff = self._calculate_backoff(attempt, response)
                    logger.warning(
                        f"Request failed with {response.status_code}, "
                        f"retrying in {backoff:.1f}s "
                        f"(attempt {attempt + 1}/{self._max_retries})"
                    )
                    time.sleep(backoff)
                    attempt += 1
                    continue

                # Non-retryable error
                raise APIError(
                    f"API request failed: {response.status_code} - {response.text}",
                    status_code=response.status_code
                )

            except requests.exceptions.Timeout:
                if attempt < self._max_retries:
                    backoff = self._calculate_backoff(attempt)
                    logger.warning(
                        f"Request timeout, retrying in {backoff:.1f}s "
                        f"(attempt {attempt + 1}/{self._max_retries})"
                    )
                    time.sleep(backoff)
                    attempt += 1
                    continue
                raise APIError("Request timed out after all retries")

            except requests.exceptions.ConnectionError as e:
                if attempt < self._max_retries:
                    backoff = self._calculate_backoff(attempt)
                    logger.warning(
                        f"Connection error, retrying in {backoff:.1f}s "
                        f"(attempt {attempt + 1}/{self._max_retries})"
                    )
                    time.sleep(backoff)
                    attempt += 1
                    continue
                raise APIError(f"Connection failed: {e}")

    def get(self, endpoint: str, params: Optional[dict] = None) -> dict[str, Any]:
        """Make a GET request."""
        return self.request("GET", endpoint, params=params)

    def post(
        self,
        endpoint: str,
        data: Optional[dict] = None,
        json_data: Optional[dict] = None,
    ) -> dict[str, Any]:
        """Make a POST request."""
        return self.request("POST", endpoint, data=data, json_data=json_data)

    def close(self) -> None:
        """Close the session."""
        self._session.close()
