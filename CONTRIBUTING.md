# Contributing to WayTrace

Thanks for your interest in WayTrace.

## Getting started

1. Fork the repository and clone your fork.
2. Create a feature branch from `main`:
   ```bash
   git checkout -b feature/your-feature
   ```
3. Install the backend in editable mode:
   ```bash
   cd backend
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt -r requirements-dev.txt
   ```
4. Make your change and add tests.
5. Run the full suite:
   ```bash
   python -m pytest tests/ -v
   ```
6. Open a PR targeting `main`.

## Commit messages

We follow [Conventional Commits](https://www.conventionalcommits.org/):

| Prefix     | Use for                                       |
|------------|-----------------------------------------------|
| `feat:`    | A new user-facing feature                     |
| `fix:`     | A bug fix                                     |
| `refactor:`| Code restructure without behaviour change     |
| `perf:`    | Performance work                              |
| `test:`    | Test-only changes                             |
| `docs:`    | Documentation only                            |
| `chore:`   | Tooling, config, dependency updates           |

One logical change per commit.

## Adding a new extractor

Each extraction category lives in its own module under `backend/services/extractor/`:

1. Add the regex(es) to `patterns.py`.
2. Create `<name>_extract.py` exposing `extract_<name>(...)` that writes
   into the shared `accum` dict.
3. Register it in `extract.py`'s `CATEGORY_EXTRACTORS` map.
4. Append the category name to `ALL_CATEGORIES` in `finalize.py` (and to
   `finalize_accum` if it needs post-processing).
5. Add a row in `_item_value` / `_item_severity` in `routers/analyze.py`
   so the finding is persisted.
6. Write ≥5 positive and ≥5 false-positive tests under `backend/tests/`.

Every extracted entity must include `first_seen`, `last_seen`, and
`occurrences`.

## Code style

- Python 3.12+, type hints throughout.
- `loguru` for logging (not the stdlib `logging` module).
- `selectolax` for HTML parsing (not BeautifulSoup).
- Pydantic v2 for schemas, `pydantic-settings` for config.
- All I/O via `aiohttp`. Never block in an async function.

## License

By contributing, you agree that your work is released under the MIT
License (see [LICENSE](LICENSE)).
