"""
storage.py - State management and data storage.

Handles:
- State file (seen_fullnames) read/write
- JSONL output writing
- Compliance purge for deleted content
"""

import json
import logging
import os
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .config import Config
from .fetcher import PostData

logger = logging.getLogger(__name__)


class StorageError(Exception):
    """Raised when storage operations fail."""
    pass


class State:
    """
    Manages application state (seen fullnames, last run time).

    State is persisted to a JSON file and survives restarts.
    """

    def __init__(self, state_file: str, max_seen_keep: int = 2000):
        self.state_file = Path(state_file)
        self.max_seen_keep = max_seen_keep

        self._seen_fullnames: list[str] = []
        self._last_run_utc: float = 0

        self._load()

    def _load(self) -> None:
        """Load state from file."""
        if not self.state_file.exists():
            logger.info(f"State file not found, starting fresh: {self.state_file}")
            return

        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            self._seen_fullnames = data.get("seen_fullnames", [])
            self._last_run_utc = data.get("last_run_utc", 0)

            logger.info(
                f"Loaded state: {len(self._seen_fullnames)} seen fullnames, "
                f"last run: {datetime.utcfromtimestamp(self._last_run_utc).isoformat() if self._last_run_utc else 'never'}"
            )

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse state file: {e}")
            raise StorageError(f"Invalid state file: {e}") from e
        except Exception as e:
            logger.error(f"Failed to load state: {e}")
            raise StorageError(f"Failed to load state: {e}") from e

    def save(self) -> None:
        """Save state to file."""
        # Ensure directory exists
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "seen_fullnames": self._seen_fullnames,
            "last_run_utc": self._last_run_utc,
        }

        try:
            # Write to temp file first, then rename for atomicity
            temp_file = self.state_file.with_suffix(".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

            temp_file.replace(self.state_file)

            logger.debug(f"Saved state: {len(self._seen_fullnames)} seen fullnames")

        except Exception as e:
            logger.error(f"Failed to save state: {e}")
            raise StorageError(f"Failed to save state: {e}") from e

    @property
    def seen_fullnames(self) -> set[str]:
        """Get set of seen fullnames."""
        return set(self._seen_fullnames)

    def add_seen(self, fullname: str) -> None:
        """Add a fullname to seen list."""
        if fullname not in self._seen_fullnames:
            self._seen_fullnames.append(fullname)
            self._trim_seen()

    def add_seen_batch(self, fullnames: list[str]) -> None:
        """Add multiple fullnames to seen list."""
        for fn in fullnames:
            if fn not in self._seen_fullnames:
                self._seen_fullnames.append(fn)
        self._trim_seen()

    def _trim_seen(self) -> None:
        """Trim seen list to max_seen_keep (FIFO)."""
        if len(self._seen_fullnames) > self.max_seen_keep:
            excess = len(self._seen_fullnames) - self.max_seen_keep
            self._seen_fullnames = self._seen_fullnames[excess:]
            logger.debug(f"Trimmed {excess} old entries from seen list")

    def update_last_run(self) -> None:
        """Update last run timestamp to now."""
        self._last_run_utc = time.time()

    @property
    def last_run_utc(self) -> float:
        """Get last run timestamp."""
        return self._last_run_utc


class PostWriter:
    """
    Writes posts to JSONL files.

    Creates one file per day by default.
    """

    def __init__(self, posts_dir: str, output_format: str = "jsonl"):
        self.posts_dir = Path(posts_dir)
        self.output_format = output_format

        # Ensure directory exists
        self.posts_dir.mkdir(parents=True, exist_ok=True)

    def _get_output_file(self) -> Path:
        """Get the output file path for today."""
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
        return self.posts_dir / f"posts_{date_str}.jsonl"

    def _post_to_dict(self, post: PostData) -> dict[str, Any]:
        """Convert PostData to a JSON-serializable dict."""
        return asdict(post)

    def write_post(self, post: PostData) -> None:
        """Write a single post to the output file."""
        output_file = self._get_output_file()

        post_dict = self._post_to_dict(post)

        try:
            with open(output_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(post_dict, ensure_ascii=False) + "\n")

            logger.debug(f"Wrote post {post.fullname} to {output_file}")

        except Exception as e:
            logger.error(f"Failed to write post {post.fullname}: {e}")
            raise StorageError(f"Failed to write post: {e}") from e

    def write_posts(self, posts: list[PostData]) -> int:
        """
        Write multiple posts to the output file.

        Returns:
            Number of posts written
        """
        if not posts:
            return 0

        output_file = self._get_output_file()

        try:
            with open(output_file, "a", encoding="utf-8") as f:
                for post in posts:
                    post_dict = self._post_to_dict(post)
                    f.write(json.dumps(post_dict, ensure_ascii=False) + "\n")

            logger.info(f"Wrote {len(posts)} posts to {output_file}")
            return len(posts)

        except Exception as e:
            logger.error(f"Failed to write posts: {e}")
            raise StorageError(f"Failed to write posts: {e}") from e


class CompliancePurger:
    """
    Handles compliance purging of deleted/removed content.

    Scans existing data files and removes entries marked as deleted/removed.
    """

    def __init__(self, config: Config):
        self.config = config
        self.posts_dir = Path(config.storage.output.posts_dir)
        self.purge_enabled = config.storage.compliance.purge_deleted_content
        self.purge_interval_hours = config.storage.compliance.purge_interval_hours

    def _should_purge(self, last_purge_utc: float) -> bool:
        """Check if purge should run based on interval."""
        if not self.purge_enabled:
            return False

        hours_since_purge = (time.time() - last_purge_utc) / 3600
        return hours_since_purge >= self.purge_interval_hours

    def purge(self) -> int:
        """
        Purge deleted/removed content from data files.

        Returns:
            Number of entries purged
        """
        if not self.purge_enabled:
            logger.debug("Purge disabled in config")
            return 0

        if not self.posts_dir.exists():
            logger.debug("Posts directory does not exist, nothing to purge")
            return 0

        total_purged = 0

        for jsonl_file in self.posts_dir.glob("*.jsonl"):
            purged = self._purge_file(jsonl_file)
            total_purged += purged

        if total_purged > 0:
            logger.info(f"Purged {total_purged} deleted/removed entries")
        else:
            logger.debug("No entries to purge")

        return total_purged

    def _purge_file(self, file_path: Path) -> int:
        """
        Purge deleted entries from a single file.

        Returns:
            Number of entries purged
        """
        purged_count = 0
        kept_lines = []

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        data = json.loads(line)
                        if data.get("is_deleted_or_removed", False):
                            purged_count += 1
                            logger.debug(
                                f"Purging {data.get('fullname', 'unknown')}: "
                                f"{data.get('removed_hint', 'unknown reason')}"
                            )
                        else:
                            kept_lines.append(line)
                    except json.JSONDecodeError:
                        # Keep malformed lines for manual inspection
                        kept_lines.append(line)

            if purged_count > 0:
                # Rewrite file without purged entries
                temp_file = file_path.with_suffix(".tmp")
                with open(temp_file, "w", encoding="utf-8") as f:
                    for line in kept_lines:
                        f.write(line + "\n")
                temp_file.replace(file_path)

                logger.info(f"Purged {purged_count} entries from {file_path.name}")

        except Exception as e:
            logger.error(f"Error purging {file_path}: {e}")

        return purged_count
