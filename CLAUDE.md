# CLAUDE.md

This file documents the codebase structure, development workflows, and conventions for AI assistants working in this repository.

## Project Overview

**tistory-auto-post** is a GitHub Actions-powered automation that cross-posts articles from a Naver Blog RSS feed to a Tistory blog. It runs hourly, fetches new posts, and publishes them to Tistory using a headless Chromium browser via Playwright.

## Repository Structure

```
tistory-auto-post/
├── post_to_tistory.py          # Main automation script (single file)
├── published.json              # State file: list of already-published Naver post URLs
├── requirements.txt            # Python dependencies
└── .github/
    └── workflows/
        ├── auto-post.yml       # Core workflow: runs hourly, posts to Tistory
        ├── greetings.yml       # First-interaction greeting for new issues/PRs
        └── summary.yml         # AI-powered issue summarization on open
```

## Key Files

### `post_to_tistory.py`

The sole Python script; contains all logic in four async functions:

| Function | Purpose |
|---|---|
| `load_published()` | Reads `published.json`; returns a list of Naver post URLs already posted |
| `save_published(published)` | Writes the updated list back to `published.json` (UTF-8, indented) |
| `_click_submit(page)` | Helper: tries multiple selectors to click submit/confirm buttons |
| `login(page)` | Interactive Kakao login flow (fallback; normally unused — see cookie injection below) |
| `set_editor_content(page, html_content)` | Sets body content in the Tistory editor via multiple fallback methods |
| `write_post(page, title, html_content)` | Navigates to the Tistory write page, fills title and body, then publishes |
| `main()` | Entry point: fetches RSS, filters new entries, launches browser, iterates posts |

**Top-level constants** (edit to retarget):
```python
RSS_URL    = "https://rss.blog.naver.com/kcl3598.xml"
BLOG_NAME  = "kcl3598"
WRITE_URL  = f"https://{BLOG_NAME}.tistory.com/manage/newpost/?type=post"
PUBLISHED_FILE = "published.json"
```

### `published.json`

A flat JSON array of Naver post URLs (strings). Every successful post appends the source URL here. The workflow bot commits this file after each run with `[skip ci]` in the message to prevent infinite loops.

### `requirements.txt`

```
feedparser==6.0.11
playwright==1.44.0
```

No lock file exists; dependency versions are pinned directly here.

## Authentication

Login is done via **cookie injection**, not interactive credentials. Four Tistory session cookies are stored as GitHub Actions secrets and injected into the Playwright browser context before any navigation:

| Cookie name | Secret name | Scope |
|---|---|---|
| `TSAL` | `TISTORY_TSAL` | `.tistory.com` |
| `TOP-XSRF-TOKEN` | `TISTORY_XSRF_TOKEN` | `.tistory.com` |
| `TSESSION` | `TISTORY_SESSION` | `.tistory.com` (httpOnly) |
| `TSSESSION` | `TISTORY_TSSESSION` | `.tistory.com` (httpOnly) |

The `login()` function (interactive Kakao email/password flow) exists as a fallback but is **not called** in the current `main()` — the cookie injection replaces it. When cookies expire, they must be manually recaptured from a browser session and updated in the repository secrets.

**Environment variables required at runtime:**
```
TISTORY_TSAL
TISTORY_XSRF_TOKEN
TISTORY_SESSION
TISTORY_TSSESSION
```

The script raises a `KeyError` if any of these are missing.

## Posting Workflow

1. Parse the Naver Blog RSS feed with `feedparser`.
2. Filter out URLs already in `published.json`; take up to **5** new entries (oldest first via `reversed()`).
3. Launch headless Chromium with anti-bot flags:
   - `--disable-blink-features=AutomationControlled`
   - `navigator.webdriver` overridden to `undefined` via `add_init_script`
   - Desktop user-agent string (Chrome 124)
4. Inject session cookies.
5. For each new entry:
   - Navigate to `/{BLOG_NAME}.tistory.com/manage`, then click "글쓰기".
   - Fill the title field (tries ~12 CSS selectors, then falls back to JS via `nativeInputValueSetter`).
   - Set editor body (`set_editor_content`) — tries in order: TinyMCE API, TinyMCE iframe DOM, HTML-mode textarea, clipboard paste, ProseMirror/contenteditable JS injection.
   - Click "완료" (done) to open the publish settings panel.
   - Click "발행" (publish) to make the post public.
   - Append the source URL to `published`.
6. Save `published.json` after all posts are processed.

**Post content format:**
```
{naver post description HTML}
<br><br><hr>
<p>원문: <a href="{link}">{link}</a></p>
```

## GitHub Actions Workflows

### `auto-post.yml` (primary)

- **Trigger:** `schedule` (every hour at `:00 UTC`) + `workflow_dispatch` (manual)
- **Runner:** `ubuntu-22.04`
- **Steps:** checkout → Python 3.11 → `pip install` → `playwright install chromium --with-deps` → run script → commit `published.json`
- **Permissions:** `contents: write` (needed for the auto-commit step)
- **Commit pattern:** `chore: update published posts [skip ci]`

### `greetings.yml`

- Fires on first-ever issue or PR from a new contributor.
- Uses `actions/first-interaction@v1`; customize `issue-message` and `pr-message` as needed.

### `summary.yml`

- Fires when a new issue is opened.
- Uses `actions/ai-inference@v1` to generate a one-paragraph summary and posts it as a comment.
- Prompt is hardened against prompt-injection from issue content.

## Development Conventions

### Language
Comments and print statements are in **Korean** throughout `post_to_tistory.py`. Keep this convention when adding new log output.

### Selector strategy
All UI interactions use a **try-multiple-selectors** pattern. Always add new selectors to the existing lists rather than replacing them — Tistory's editor HTML changes over time and historical selectors may become relevant again.

### Deduplication
`published.json` is the only source of truth for "what has been posted." Never delete entries from this file. If re-posting is needed, remove the specific URL(s) manually.

### Batch size
The `[:5]` slice in `main()` limits runs to 5 posts. Increase with caution — Tistory may rate-limit or flag rapid sequential posts.

### No test suite
There are no automated tests. Validate changes by running the script locally with real credentials, or trigger `workflow_dispatch` on a branch.

### Dependency updates
Update `requirements.txt` pins only when necessary. After changing `playwright` version, verify the matching Chromium build is available via `playwright install chromium`.

## Local Development Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium --with-deps

# Export required secrets
export TISTORY_TSAL=...
export TISTORY_XSRF_TOKEN=...
export TISTORY_SESSION=...
export TISTORY_TSSESSION=...

python post_to_tistory.py
```

## Extending the Script

- **Different source blog:** Change `RSS_URL` and update the `BLOG_NAME` constant.
- **Different target blog:** Change `BLOG_NAME`; no other changes needed.
- **More posts per run:** Change the `[:5]` slice in `main()`.
- **Different schedule:** Edit the `cron` expression in `auto-post.yml`.
- **Category or tag support:** Add selector logic in `write_post()` after the editor content is set, before the "완료" button click.
