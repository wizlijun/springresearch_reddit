"""Setup script for Reddit Custom Feed Fetcher."""

from setuptools import setup, find_packages

setup(
    name="reddit-custom-feed-fetcher",
    version="0.1.0",
    description="Reddit Custom Feed (Multi/Multireddit) Fetcher",
    author="Your Name",
    python_requires=">=3.11",
    packages=find_packages(),
    package_dir={"": "."},
    install_requires=[
        "requests>=2.31.0",
        "PyYAML>=6.0.1",
    ],
    entry_points={
        "console_scripts": [
            "redditfeed=src.cli:main",
        ],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
)
