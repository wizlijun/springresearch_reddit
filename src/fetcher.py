"""
fetcher.py - Fetches posts and comments from Reddit multireddit.

Handles:
- Fetching listing from custom feed
- Filtering new posts based on seen_fullnames
- Batch fetching post details
- Fetching comment trees
- Detecting deleted/removed content
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .config import Config
from .reddit_client import RedditClient
from .multi_validator import get_multi_listing_endpoint

logger = logging.getLogger(__name__)


@dataclass
class PostData:
    """Processed post data ready for storage."""
    id: str
    fullname: str
    created_utc: float
    subreddit: str
    author: str
    title: str
    selftext: str
    url: str
    permalink: str
    is_self: bool
    over_18: bool
    score: int
    num_comments: int

    # Optional fields
    raw_listing_item: Optional[dict] = None
    detail: Optional[dict] = None
    comments: Optional[list] = None

    # Metadata
    fetched_at_utc: float = field(default_factory=time.time)

    # Deleted/removed detection
    is_deleted_or_removed: bool = False
    removed_hint: Optional[str] = None


def detect_deleted_removed(data: dict) -> tuple[bool, Optional[str]]:
    """
    Detect if a post or its content has been deleted/removed.

    Args:
        data: Post data dict

    Returns:
        Tuple of (is_deleted_or_removed, removed_hint)
    """
    author = data.get("author", "")
    selftext = data.get("selftext", "")

    hints = []

    if author == "[deleted]":
        hints.append("author_deleted")
    if selftext == "[deleted]":
        hints.append("text_deleted")
    if selftext == "[removed]":
        hints.append("text_removed")
    if data.get("removed_by_category"):
        hints.append(f"removed_by_{data['removed_by_category']}")

    if hints:
        return True, "|".join(hints)

    return False, None


def extract_post_data(listing_item: dict, detail: Optional[dict] = None) -> PostData:
    """
    Extract PostData from a listing item.

    Args:
        listing_item: Raw listing item from Reddit API
        detail: Optional detail data from /api/info

    Returns:
        PostData instance
    """
    data = listing_item.get("data", {})

    # Use detail data if available (more complete)
    if detail:
        data = {**data, **detail.get("data", {})}

    is_deleted, removed_hint = detect_deleted_removed(data)

    return PostData(
        id=data.get("id", ""),
        fullname=data.get("name", ""),
        created_utc=data.get("created_utc", 0),
        subreddit=data.get("subreddit", ""),
        author=data.get("author", ""),
        title=data.get("title", ""),
        selftext=data.get("selftext", ""),
        url=data.get("url", ""),
        permalink=data.get("permalink", ""),
        is_self=data.get("is_self", False),
        over_18=data.get("over_18", False),
        score=data.get("score", 0),
        num_comments=data.get("num_comments", 0),
        raw_listing_item=listing_item,
        detail=detail,
        is_deleted_or_removed=is_deleted,
        removed_hint=removed_hint,
    )


class Fetcher:
    """
    Fetches posts and comments from Reddit.

    Provides methods for:
    - Fetching the custom feed listing
    - Filtering new posts
    - Batch fetching post details
    - Fetching comment trees
    """

    # Maximum IDs per /api/info request
    MAX_IDS_PER_INFO_REQUEST = 100

    def __init__(self, client: RedditClient, config: Config):
        self.client = client
        self.config = config

    def fetch_listing(self) -> list[dict]:
        """
        Fetch the listing from the configured custom feed.

        Returns:
            List of listing items (each with 'kind' and 'data')
        """
        endpoint = get_multi_listing_endpoint(self.config)
        limit = self.config.fetch.listing.limit

        logger.info(f"Fetching listing from {endpoint} (limit={limit})")

        params = {"limit": limit}

        response = self.client.get(endpoint, params=params)

        children = response.get("data", {}).get("children", [])
        logger.info(f"Fetched {len(children)} items from listing")

        return children

    def filter_new_posts(
        self,
        listing_items: list[dict],
        seen_fullnames: set[str],
    ) -> list[dict]:
        """
        Filter listing items to only include new (unseen) posts.

        Args:
            listing_items: List of listing items from fetch_listing()
            seen_fullnames: Set of already-seen fullnames

        Returns:
            List of new listing items
        """
        new_posts = []

        for item in listing_items:
            fullname = item.get("data", {}).get("name", "")
            if fullname and fullname not in seen_fullnames:
                new_posts.append(item)
            else:
                logger.debug(f"Skipping already seen post: {fullname}")

        logger.info(f"Found {len(new_posts)} new posts out of {len(listing_items)}")

        return new_posts

    def fetch_details_batch(self, fullnames: list[str]) -> dict[str, dict]:
        """
        Batch fetch post details using /api/info.

        Args:
            fullnames: List of post fullnames (e.g., ["t3_abc123", "t3_def456"])

        Returns:
            Dict mapping fullname to detail data
        """
        if not fullnames:
            return {}

        logger.info(f"Fetching details for {len(fullnames)} posts")

        details = {}

        # Split into batches
        for i in range(0, len(fullnames), self.MAX_IDS_PER_INFO_REQUEST):
            batch = fullnames[i:i + self.MAX_IDS_PER_INFO_REQUEST]
            ids_str = ",".join(batch)

            logger.debug(f"Fetching batch of {len(batch)} post details")

            response = self.client.get("/api/info", params={"id": ids_str})

            for item in response.get("data", {}).get("children", []):
                item_fullname = item.get("data", {}).get("name", "")
                if item_fullname:
                    details[item_fullname] = item

        logger.info(f"Fetched details for {len(details)} posts")

        return details

    def fetch_comments(self, post_id: str) -> list[dict]:
        """
        Fetch comment tree for a post.

        Args:
            post_id: Post ID (id36 without t3_ prefix)

        Returns:
            List of comment listings (usually [link_listing, comments_listing])
        """
        comments_config = self.config.fetch.per_post.comments

        params = {
            "limit": comments_config.limit,
            "depth": comments_config.depth,
            "sort": comments_config.sort,
            "truncate": comments_config.truncate,
        }

        logger.debug(f"Fetching comments for post {post_id}")

        endpoint = f"/comments/{post_id}"
        response = self.client.get(endpoint, params=params)

        # Response is an array: [link_listing, comment_listing]
        if isinstance(response, list) and len(response) >= 2:
            comment_listing = response[1]
            comments = comment_listing.get("data", {}).get("children", [])
            logger.debug(f"Fetched {len(comments)} top-level comments for {post_id}")
            return response
        else:
            logger.warning(f"Unexpected comments response format for {post_id}")
            return response if isinstance(response, list) else []

    def process_new_posts(
        self,
        listing_items: list[dict],
        seen_fullnames: set[str],
    ) -> list[PostData]:
        """
        Process new posts: filter, fetch details, fetch comments.

        Args:
            listing_items: Raw listing items
            seen_fullnames: Set of already-seen fullnames

        Returns:
            List of fully processed PostData objects
        """
        # Filter to new posts only
        new_items = self.filter_new_posts(listing_items, seen_fullnames)

        if not new_items:
            logger.info("No new posts to process")
            return []

        # Extract fullnames for batch detail fetch
        fullnames = [item.get("data", {}).get("name", "") for item in new_items]
        fullnames = [fn for fn in fullnames if fn]

        # Fetch post details (if enabled)
        details = {}
        if self.config.fetch.per_post.fetch_post_detail:
            details = self.fetch_details_batch(fullnames)

        # Process each post
        processed_posts = []

        for item in new_items:
            fullname = item.get("data", {}).get("name", "")
            post_id = item.get("data", {}).get("id", "")

            # Get detail if available
            detail = details.get(fullname)

            # Create PostData
            post_data = extract_post_data(item, detail)

            # Fetch comments (if enabled)
            if self.config.fetch.per_post.fetch_comments and post_id:
                try:
                    comments = self.fetch_comments(post_id)
                    post_data.comments = comments
                except Exception as e:
                    logger.warning(f"Failed to fetch comments for {post_id}: {e}")
                    post_data.comments = None

            # Update fetched timestamp
            post_data.fetched_at_utc = time.time()

            processed_posts.append(post_data)
            logger.info(f"Processed post: {fullname} - {post_data.title[:50]}...")

        return processed_posts
