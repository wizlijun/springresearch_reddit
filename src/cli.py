"""
cli.py - Command-line interface for Reddit Custom Feed Fetcher.

Commands:
- validate: Validate configuration and multi accessibility
- once: Run a single fetch cycle
- run: Run continuous polling
"""

import argparse
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Optional

from .config import load_config, validate_config, Config, ConfigError, mask_secret
from .reddit_auth import RedditAuth, AuthError
from .reddit_client import RedditClient, APIError
from .multi_validator import validate_multi, MultiValidationError
from .fetcher import Fetcher
from .storage import State, PostWriter, CompliancePurger, StorageError


# Global flag for graceful shutdown
_shutdown_requested = False


def setup_logging(config: Config) -> None:
    """Configure logging based on config."""
    log_level = getattr(logging, config.logging.level.upper(), logging.INFO)

    # Create logs directory if needed
    log_file = Path(config.logging.file)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    # Configure root logger
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )

    # Reduce noise from requests/urllib3
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def signal_handler(signum, frame):
    """Handle shutdown signals."""
    global _shutdown_requested
    logger = logging.getLogger(__name__)
    logger.info(f"Received signal {signum}, requesting shutdown...")
    _shutdown_requested = True


def cmd_validate(args: argparse.Namespace) -> int:
    """
    Validate configuration and multi accessibility.

    Returns:
        0 on success, non-zero on failure
    """
    logger = logging.getLogger(__name__)

    print(f"Loading configuration from: {args.config}")

    try:
        # Load and validate config
        config = load_config(args.config)
        validate_config(config)

        print("Configuration validation passed")
        print(f"  - Custom feed type: {config.custom_feed.type}")
        print(f"  - Owner: {config.custom_feed.owner}")
        print(f"  - Name: {config.custom_feed.name}")
        print(f"  - Multipath: {config.custom_feed.multipath}")

        # Setup logging (minimal for validate)
        setup_logging(config)

        # Create auth and client
        print("\nAuthenticating with Reddit...")
        auth = RedditAuth(config)
        client = RedditClient(config, auth)

        # Validate multi exists and is accessible
        print(f"\nValidating multi: {config.custom_feed.multipath}")
        multi_info = validate_multi(client, config)

        print("\nMulti validation passed!")
        print(f"  - Display name: {multi_info.display_name}")
        print(f"  - Visibility: {multi_info.visibility}")
        print(f"  - Subreddits ({len(multi_info.subreddits)}):")
        for sub in multi_info.subreddits[:10]:
            print(f"    - r/{sub}")
        if len(multi_info.subreddits) > 10:
            print(f"    ... and {len(multi_info.subreddits) - 10} more")

        client.close()
        return 0

    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except ConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 1
    except AuthError as e:
        print(f"Authentication error: {e}", file=sys.stderr)
        return 1
    except MultiValidationError as e:
        print(f"Multi validation error: {e}", file=sys.stderr)
        return 1
    except APIError as e:
        print(f"API error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        logger.exception("Validation failed")
        return 1


def run_once(config: Config, client: RedditClient, fetcher: Fetcher,
             state: State, writer: PostWriter) -> int:
    """
    Run a single fetch cycle.

    Returns:
        Number of new posts processed
    """
    logger = logging.getLogger(__name__)

    logger.info("Starting fetch cycle")

    # Fetch listing
    listing_items = fetcher.fetch_listing()

    # Process new posts
    new_posts = fetcher.process_new_posts(listing_items, state.seen_fullnames)

    if not new_posts:
        logger.info("No new posts found")
        state.update_last_run()
        state.save()
        return 0

    # Write posts to storage
    written = writer.write_posts(new_posts)

    # Update state with new fullnames
    new_fullnames = [post.fullname for post in new_posts]
    state.add_seen_batch(new_fullnames)
    state.update_last_run()
    state.save()

    logger.info(f"Cycle complete: processed {len(new_posts)} new posts")

    return written


def cmd_once(args: argparse.Namespace) -> int:
    """
    Run a single fetch cycle.

    Returns:
        0 on success, non-zero on failure
    """
    logger = logging.getLogger(__name__)

    try:
        # Load and validate config
        config = load_config(args.config)
        validate_config(config)

        setup_logging(config)
        logger.info("Starting single fetch cycle")

        # Initialize components
        auth = RedditAuth(config)
        client = RedditClient(config, auth)

        # Validate multi first
        logger.info("Validating multi...")
        validate_multi(client, config)

        # Initialize storage and fetcher
        state = State(
            config.storage.state_file,
            max_seen_keep=config.fetch.listing.incremental.max_seen_keep,
        )
        writer = PostWriter(
            config.storage.output.posts_dir,
            config.storage.output.format,
        )
        fetcher = Fetcher(client, config)

        # Run once
        processed = run_once(config, client, fetcher, state, writer)

        client.close()

        logger.info(f"Completed: {processed} posts processed")
        return 0

    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except ConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 1
    except AuthError as e:
        logger.error(f"Authentication error: {e}")
        return 1
    except MultiValidationError as e:
        logger.error(f"Multi validation error: {e}")
        return 1
    except APIError as e:
        logger.error(f"API error: {e}")
        return 1
    except StorageError as e:
        logger.error(f"Storage error: {e}")
        return 1
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return 1


def cmd_run(args: argparse.Namespace) -> int:
    """
    Run continuous polling.

    Returns:
        0 on normal shutdown, non-zero on error
    """
    global _shutdown_requested
    logger = logging.getLogger(__name__)

    try:
        # Load and validate config
        config = load_config(args.config)
        validate_config(config)

        setup_logging(config)
        logger.info("Starting continuous polling")

        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Initialize components
        auth = RedditAuth(config)
        client = RedditClient(config, auth)

        # Validate multi first
        logger.info("Validating multi...")
        multi_info = validate_multi(client, config)
        logger.info(f"Multi validated: {multi_info.display_name} ({len(multi_info.subreddits)} subreddits)")

        # Initialize storage and fetcher
        state = State(
            config.storage.state_file,
            max_seen_keep=config.fetch.listing.incremental.max_seen_keep,
        )
        writer = PostWriter(
            config.storage.output.posts_dir,
            config.storage.output.format,
        )
        fetcher = Fetcher(client, config)
        purger = CompliancePurger(config)

        poll_interval = config.fetch.listing.poll_interval_sec
        last_purge_utc = 0.0

        logger.info(f"Polling every {poll_interval} seconds. Press Ctrl+C to stop.")

        cycle_count = 0
        total_posts = 0

        while not _shutdown_requested:
            cycle_count += 1
            logger.info(f"=== Cycle {cycle_count} ===")

            try:
                processed = run_once(config, client, fetcher, state, writer)
                total_posts += processed

                # Check if purge is due
                hours_since_purge = (time.time() - last_purge_utc) / 3600
                if config.storage.compliance.purge_deleted_content:
                    if hours_since_purge >= config.storage.compliance.purge_interval_hours:
                        logger.info("Running compliance purge...")
                        purged = purger.purge()
                        last_purge_utc = time.time()

            except AuthError as e:
                logger.error(f"Authentication error: {e}")
                logger.info("Will retry next cycle")
            except APIError as e:
                logger.error(f"API error: {e}")
                logger.info("Will retry next cycle")
            except StorageError as e:
                logger.error(f"Storage error: {e}")
                logger.info("Will retry next cycle")

            # Wait for next cycle (with interrupt check)
            if not _shutdown_requested:
                logger.debug(f"Sleeping for {poll_interval} seconds...")
                for _ in range(poll_interval):
                    if _shutdown_requested:
                        break
                    time.sleep(1)

        # Graceful shutdown
        logger.info("Shutting down...")
        logger.info(f"Total: {cycle_count} cycles, {total_posts} posts processed")

        # Final state save
        state.save()
        client.close()

        return 0

    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except ConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.exception(f"Unexpected error: {e}")
        return 1


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="redditfeed",
        description="Reddit Custom Feed (Multi/Multireddit) Fetcher",
    )

    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 0.1.0",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # validate command
    validate_parser = subparsers.add_parser(
        "validate",
        help="Validate configuration and multi accessibility",
    )
    validate_parser.add_argument(
        "--config", "-c",
        default="config.yml",
        help="Path to config file (default: config.yml)",
    )

    # once command
    once_parser = subparsers.add_parser(
        "once",
        help="Run a single fetch cycle",
    )
    once_parser.add_argument(
        "--config", "-c",
        default="config.yml",
        help="Path to config file (default: config.yml)",
    )

    # run command
    run_parser = subparsers.add_parser(
        "run",
        help="Run continuous polling",
    )
    run_parser.add_argument(
        "--config", "-c",
        default="config.yml",
        help="Path to config file (default: config.yml)",
    )

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 1

    if args.command == "validate":
        return cmd_validate(args)
    elif args.command == "once":
        return cmd_once(args)
    elif args.command == "run":
        return cmd_run(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
