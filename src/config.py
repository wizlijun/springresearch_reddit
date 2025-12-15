"""
config.py - Configuration loading and validation for Reddit Custom Feed Fetcher.

Handles:
- Loading config.yml with environment variable expansion
- Validating custom_feed multipath format
- Ensuring URL/owner/name/multipath consistency
- Validating required auth credentials
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import yaml


class ConfigError(Exception):
    """Raised when configuration validation fails."""
    pass


# Regex pattern for valid multipath: /user/{username}/m/{multiname}
MULTIPATH_PATTERN = re.compile(r"^/user/([^/]+)/m/([^/]+)$")

# User-Agent patterns that are considered invalid (default library UAs)
INVALID_UA_PATTERNS = [
    r"^python-requests/",
    r"^Python-urllib/",
    r"^Java/",
    r"^Apache-HttpClient/",
    r"^Go-http-client/",
    r"^curl/",
]


@dataclass
class AppConfig:
    name: str = "reddit-custom-feed-fetcher"
    version: str = "0.1.0"


@dataclass
class RedditEndpoints:
    www_base: str = "https://www.reddit.com"
    oauth_base: str = "https://oauth.reddit.com"


@dataclass
class RedditAuth:
    grant_type: str = "refresh_token"
    client_id: str = ""
    client_secret: str = ""
    refresh_token: str = ""
    redirect_uri: str = "http://localhost:8080/reddit_callback"
    scopes: list[str] = field(default_factory=lambda: ["read", "identity"])


@dataclass
class RedditConfig:
    user_agent: str = ""
    endpoints: RedditEndpoints = field(default_factory=RedditEndpoints)
    auth: RedditAuth = field(default_factory=RedditAuth)


@dataclass
class CustomFeedConfig:
    type: str = "multi"
    url: str = ""
    multipath: str = ""
    owner: str = ""
    name: str = ""


@dataclass
class IncrementalConfig:
    strategy: str = "seen_fullnames"
    max_seen_keep: int = 2000


@dataclass
class ListingConfig:
    sort: str = "new"
    limit: int = 50
    poll_interval_sec: int = 60
    incremental: IncrementalConfig = field(default_factory=IncrementalConfig)


@dataclass
class CommentsConfig:
    limit: int = 50
    depth: int = 5
    sort: str = "top"
    truncate: int = 50


@dataclass
class PerPostConfig:
    fetch_post_detail: bool = True
    fetch_comments: bool = True
    comments: CommentsConfig = field(default_factory=CommentsConfig)


@dataclass
class FetchConfig:
    listing: ListingConfig = field(default_factory=ListingConfig)
    per_post: PerPostConfig = field(default_factory=PerPostConfig)


@dataclass
class RateLimitConfig:
    max_qpm: int = 100
    respect_response_headers: bool = True
    safety_min_interval_ms: int = 700


@dataclass
class NetworkConfig:
    timeout_sec: int = 30
    retries: int = 3
    backoff_sec: float = 1.0
    proxy: str = ""


@dataclass
class ComplianceConfig:
    purge_deleted_content: bool = True
    purge_interval_hours: int = 24


@dataclass
class OutputConfig:
    format: str = "jsonl"
    posts_dir: str = "./data/posts"


@dataclass
class StorageConfig:
    data_dir: str = "./data"
    state_file: str = "./data/state.json"
    output: OutputConfig = field(default_factory=OutputConfig)
    compliance: ComplianceConfig = field(default_factory=ComplianceConfig)


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = "./logs/app.log"


@dataclass
class Config:
    app: AppConfig = field(default_factory=AppConfig)
    reddit: RedditConfig = field(default_factory=RedditConfig)
    custom_feed: CustomFeedConfig = field(default_factory=CustomFeedConfig)
    fetch: FetchConfig = field(default_factory=FetchConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


def expand_env_vars(value: Any) -> Any:
    """Recursively expand environment variables in string values."""
    if isinstance(value, str):
        # Handle ${VAR} format
        pattern = re.compile(r'\$\{([^}]+)\}')
        def replacer(match):
            env_var = match.group(1)
            return os.environ.get(env_var, match.group(0))
        return pattern.sub(replacer, value)
    elif isinstance(value, dict):
        return {k: expand_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [expand_env_vars(item) for item in value]
    return value


def parse_custom_feed_url(url: str) -> tuple[str, str]:
    """
    Parse a custom feed URL to extract owner and name.

    Args:
        url: The custom feed URL (e.g., https://www.reddit.com/user/bushacker/m/myreddit/)

    Returns:
        Tuple of (owner, name)

    Raises:
        ConfigError: If URL cannot be parsed
    """
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")

    match = MULTIPATH_PATTERN.match(path)
    if not match:
        raise ConfigError(
            f"Cannot parse custom feed URL: {url}. "
            f"Expected format: https://www.reddit.com/user/{{username}}/m/{{multiname}}/"
        )

    return match.group(1), match.group(2)


def validate_multipath(multipath: str) -> tuple[str, str]:
    """
    Validate multipath format and extract owner/name.

    Args:
        multipath: The multipath (e.g., /user/bushacker/m/myreddit)

    Returns:
        Tuple of (owner, name)

    Raises:
        ConfigError: If multipath format is invalid
    """
    match = MULTIPATH_PATTERN.match(multipath)
    if not match:
        raise ConfigError(
            f"Invalid multipath format: {multipath}. "
            f"Must match pattern: /user/{{username}}/m/{{multiname}}"
        )

    return match.group(1), match.group(2)


def validate_user_agent(user_agent: str) -> None:
    """
    Validate that user_agent is not a default library UA.

    Args:
        user_agent: The User-Agent string

    Raises:
        ConfigError: If user_agent is invalid or matches default patterns
    """
    if not user_agent or not user_agent.strip():
        raise ConfigError("reddit.user_agent is required and cannot be empty")

    for pattern in INVALID_UA_PATTERNS:
        if re.match(pattern, user_agent, re.IGNORECASE):
            raise ConfigError(
                f"Invalid user_agent: {user_agent}. "
                f"Reddit requires a unique, descriptive User-Agent. "
                f"Default library UAs are not allowed."
            )


def validate_auth_credentials(auth: RedditAuth) -> None:
    """
    Validate that required auth credentials are present.

    Args:
        auth: The RedditAuth configuration

    Raises:
        ConfigError: If required credentials are missing
    """
    missing = []

    if not auth.client_id or auth.client_id.startswith("${"):
        missing.append("client_id")
    if not auth.client_secret or auth.client_secret.startswith("${"):
        missing.append("client_secret")
    if not auth.refresh_token or auth.refresh_token.startswith("${"):
        missing.append("refresh_token")

    if missing:
        raise ConfigError(
            f"Missing required auth credentials: {', '.join(missing)}. "
            f"Set them in config.yml or as environment variables."
        )


def validate_custom_feed(custom_feed: CustomFeedConfig) -> None:
    """
    Validate custom_feed configuration for consistency.

    Checks:
    1. type must be "multi"
    2. multipath must match valid format
    3. If URL is provided, it must be consistent with owner/name
    4. multipath must match owner/name

    Args:
        custom_feed: The CustomFeedConfig

    Raises:
        ConfigError: If validation fails
    """
    # Check type
    if custom_feed.type != "multi":
        raise ConfigError(
            f"custom_feed.type must be 'multi', got: {custom_feed.type}"
        )

    # Validate and parse multipath
    mp_owner, mp_name = validate_multipath(custom_feed.multipath)

    # If URL is provided, parse and validate consistency
    if custom_feed.url:
        url_owner, url_name = parse_custom_feed_url(custom_feed.url)

        if custom_feed.owner and custom_feed.owner != url_owner:
            raise ConfigError(
                f"custom_feed.owner ({custom_feed.owner}) does not match URL owner ({url_owner})"
            )
        if custom_feed.name and custom_feed.name != url_name:
            raise ConfigError(
                f"custom_feed.name ({custom_feed.name}) does not match URL name ({url_name})"
            )

    # Validate multipath matches owner/name
    expected_multipath = f"/user/{custom_feed.owner}/m/{custom_feed.name}"
    if custom_feed.multipath != expected_multipath:
        raise ConfigError(
            f"custom_feed.multipath ({custom_feed.multipath}) does not match "
            f"expected path from owner/name: {expected_multipath}"
        )

    # Validate multipath parsed values match explicit owner/name
    if mp_owner != custom_feed.owner:
        raise ConfigError(
            f"Multipath owner ({mp_owner}) does not match custom_feed.owner ({custom_feed.owner})"
        )
    if mp_name != custom_feed.name:
        raise ConfigError(
            f"Multipath name ({mp_name}) does not match custom_feed.name ({custom_feed.name})"
        )


def dict_to_dataclass(data: dict, dataclass_type: type) -> Any:
    """Convert a dictionary to a nested dataclass instance."""
    if data is None:
        return dataclass_type()

    field_types = {f.name: f.type for f in dataclass_type.__dataclass_fields__.values()}
    kwargs = {}

    for key, value in data.items():
        if key in field_types:
            field_type = field_types[key]
            # Handle nested dataclasses
            if hasattr(field_type, '__dataclass_fields__'):
                kwargs[key] = dict_to_dataclass(value, field_type)
            else:
                kwargs[key] = value

    return dataclass_type(**kwargs)


def load_config(config_path: str | Path) -> Config:
    """
    Load and validate configuration from a YAML file.

    Args:
        config_path: Path to the config.yml file

    Returns:
        Validated Config instance

    Raises:
        ConfigError: If loading or validation fails
        FileNotFoundError: If config file doesn't exist
    """
    config_path = Path(config_path)

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        raw_config = yaml.safe_load(f)

    if not raw_config:
        raise ConfigError("Config file is empty")

    # Expand environment variables
    expanded_config = expand_env_vars(raw_config)

    # Build Config dataclass
    config = Config(
        app=dict_to_dataclass(expanded_config.get("app", {}), AppConfig),
        reddit=RedditConfig(
            user_agent=expanded_config.get("reddit", {}).get("user_agent", ""),
            endpoints=dict_to_dataclass(
                expanded_config.get("reddit", {}).get("endpoints", {}),
                RedditEndpoints
            ),
            auth=dict_to_dataclass(
                expanded_config.get("reddit", {}).get("auth", {}),
                RedditAuth
            ),
        ),
        custom_feed=dict_to_dataclass(
            expanded_config.get("custom_feed", {}),
            CustomFeedConfig
        ),
        fetch=FetchConfig(
            listing=ListingConfig(
                sort=expanded_config.get("fetch", {}).get("listing", {}).get("sort", "new"),
                limit=expanded_config.get("fetch", {}).get("listing", {}).get("limit", 50),
                poll_interval_sec=expanded_config.get("fetch", {}).get("listing", {}).get("poll_interval_sec", 60),
                incremental=dict_to_dataclass(
                    expanded_config.get("fetch", {}).get("listing", {}).get("incremental", {}),
                    IncrementalConfig
                ),
            ),
            per_post=PerPostConfig(
                fetch_post_detail=expanded_config.get("fetch", {}).get("per_post", {}).get("fetch_post_detail", True),
                fetch_comments=expanded_config.get("fetch", {}).get("per_post", {}).get("fetch_comments", True),
                comments=dict_to_dataclass(
                    expanded_config.get("fetch", {}).get("per_post", {}).get("comments", {}),
                    CommentsConfig
                ),
            ),
        ),
        rate_limit=dict_to_dataclass(
            expanded_config.get("rate_limit", {}),
            RateLimitConfig
        ),
        network=dict_to_dataclass(
            expanded_config.get("network", {}),
            NetworkConfig
        ),
        storage=StorageConfig(
            data_dir=expanded_config.get("storage", {}).get("data_dir", "./data"),
            state_file=expanded_config.get("storage", {}).get("state_file", "./data/state.json"),
            output=dict_to_dataclass(
                expanded_config.get("storage", {}).get("output", {}),
                OutputConfig
            ),
            compliance=dict_to_dataclass(
                expanded_config.get("storage", {}).get("compliance", {}),
                ComplianceConfig
            ),
        ),
        logging=dict_to_dataclass(
            expanded_config.get("logging", {}),
            LoggingConfig
        ),
    )

    return config


def validate_config(config: Config) -> None:
    """
    Run all validation checks on the configuration.

    Args:
        config: The Config instance to validate

    Raises:
        ConfigError: If any validation fails
    """
    # Validate User-Agent
    validate_user_agent(config.reddit.user_agent)

    # Validate auth credentials
    validate_auth_credentials(config.reddit.auth)

    # Validate custom feed configuration
    validate_custom_feed(config.custom_feed)

    # Validate listing limit
    if config.fetch.listing.limit > 100:
        raise ConfigError(
            f"fetch.listing.limit cannot exceed 100, got: {config.fetch.listing.limit}"
        )


def mask_secret(secret: str, visible_chars: int = 4) -> str:
    """Mask a secret string for safe logging."""
    if not secret or len(secret) <= visible_chars:
        return "***"
    return secret[:visible_chars] + "***"
