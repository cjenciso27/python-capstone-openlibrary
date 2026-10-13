# Open Library Intelligence & Catalog Automation System

**Capstone Project - Python Software Engineering**

This project is a comprehensive Command-Line Interface (CLI) automation tool designed to interact with the [Open Library REST API](https://openlibrary.org/developers/api). It provides a robust, asynchronous engine to ingest, validate, deduplicate, and analyze bibliographic data, generating executive-ready reports and metrics.

## Features

- **Interactive CLI:** A user-friendly, menu-driven terminal interface.
- **Asynchronous API Client:** High-performance, concurrent data retrieval with automatic retries, rate-limit handling, and backoff strategies using `httpx` and `asyncio`.
- **Data Ingestion & Cleaning:** Robust parsing and normalization of messy data (including ISBN validation, title casing, and text sanitization).
- **Local Database (SQLite):** Efficient storage of works, authors, and an integrated API cache system to minimize redundant network calls.
- **Batch Processing:** Ability to process local catalog files (`.csv`, `.json`, `.jsonl`) with deduplication.
- **Analytics & Reporting:** Generates comprehensive KPIs and data summaries exported to Excel (via `openpyxl`), CSV, and JSON formats using `pandas`.
- **Quality Assurance:** Built-in commands to run code formatting checks and the complete `pytest` diagnostic suite.

## Prerequisites

- **Python:** Version 3.12 or higher.
- **Package Manager:** [`uv`](https://docs.astral.sh/uv/) is recommended for dependency management, but standard `pip` can also be used.

## Installation & Setup

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/cjenciso27/python-capstone-openlibrary.git](https://github.com/cjenciso27/python-capstone-openlibrary.git)
   cd python-capstone-openlibrary