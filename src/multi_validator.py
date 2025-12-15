"""
multi_validator.py - Validates multireddit (custom feed) existence and accessibility.

Handles:
- Fetching multi definition from Reddit API
- Extracting subreddit list from multi
- Validating multi accessibility
"""

import logging
from dataclasses import dataclass
from typing import Any

from .config import Config
from .reddit_client import RedditClient, APIError

logger = logging.getLogger(__name__)


class MultiValidationError(Exception):
    """Raised when multi validation fails."""
    pass


@dataclass
class MultiInfo:
    """Information about a validated multireddit."""
    name: str
    display_name: str
    path: str
    owner: str
    description: str
    subreddits: list[str]
    visibility: str
    created_utc: float
    num_subscribers: int


def validate_multi(client: RedditClient, config: Config) -> MultiInfo:
    """
    Validate that the configured multireddit exists and is accessible.

    Makes a request to GET /api/multi{multipath} to verify the multi
    exists and retrieve its metadata.

    Args:
        client: RedditClient instance
        config: Configuration

    Returns:
        MultiInfo with multi metadata

    Raises:
        MultiValidationError: If multi doesn't exist or is not accessible
    """
    multipath = config.custom_feed.multipath

    logger.info(f"Validating multi: {multipath}")

    endpoint = f"/api/multi{multipath}"

    try:
        response = client.get(endpoint)

        # Extract multi data
        data = response.get("data", {})

        # Extract subreddit names
        subreddits = []
        for sub in data.get("subreddits", []):
            sub_name = sub.get("name", "")
            if sub_name:
                subreddits.append(sub_name)

        multi_info = MultiInfo(
            name=data.get("name", ""),
            display_name=data.get("display_name", ""),
            path=data.get("path", ""),
            owner=data.get("owner", ""),
            description=data.get("description_md", ""),
            subreddits=subreddits,
            visibility=data.get("visibility", ""),
            created_utc=data.get("created_utc", 0),
            num_subscribers=data.get("num_subscribers", 0),
        )

        logger.info(
            f"Multi validated successfully: {multi_info.display_name} "
            f"({len(subreddits)} subreddits)"
        )

        if subreddits:
            logger.info(f"Subreddits: {', '.join(subreddits[:10])}"
                       + (f"... and {len(subreddits) - 10} more" if len(subreddits) > 10 else ""))

        return multi_info

    except APIError as e:
        if e.status_code == 404:
            raise MultiValidationError(
                f"Multireddit not found: {multipath}. "
                f"Please check that the multi exists and the path is correct."
            ) from e
        elif e.status_code == 403:
            raise MultiValidationError(
                f"Access denied to multireddit: {multipath}. "
                f"The multi may be private or you may not have permission to access it."
            ) from e
        else:
            raise MultiValidationError(
                f"Failed to validate multireddit: {e}"
            ) from e


def get_multi_listing_endpoint(config: Config) -> str:
    """
    Get the endpoint for fetching multi listing.

    Args:
        config: Configuration

    Returns:
        Endpoint string (e.g., /user/xxx/m/yyy/new)
    """
    multipath = config.custom_feed.multipath
    sort = config.fetch.listing.sort

    return f"{multipath}/{sort}"
