# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

Automates cross-posting from a Naver Blog RSS feed to Tistory. A GitHub Actions workflow runs hourly, parses the RSS feed, and uses Playwright (headless Chromium) to log in to Tistory and publish any new posts. Each published post URL is tracked in `published.json` to prevent duplicates, and that file is automatically committed back to the repo after each run.

## Running the Script Locally

```bash
pip install -r requirements.txt
playwright install chromium --with-deps

# Required environment variables (from GitHub Secrets in CI)
export TISTORY_TSAL=...
export TISTORY_XSRF_TOKEN=...
export TISTORY_SESSION=...
export TISTORY_TSSESSION=...

python post_to_tistory.py
```

There is no test suite. Manual testing means running the script with valid session cookies and checking that posts appear on Tistory.

## Architecture

Everything lives in `post_to_tistory.py`. The flow is:

1. **RSS fetch** — `feedparser` reads `https://rss.blog.naver.com/kcl3598.xml`
2. **Dedup** — `load_published()` reads `published.json` (list of post URLs already processed); new entries are those whose `link` is not in that list, limited to 5 per run, processed oldest-first via `reversed(feed.entries)`
3. **Browser session** — Playwright launches headless Chromium with `--disable-blink-features=AutomationControlled` and a real `user-agent`. **Authentication is cookie injection**, not form login: four Tistory session cookies (`TSAL`, `TOP-XSRF-TOKEN`, `TSESSION`, `TSSESSION`) from env vars are added to the context before any navigation
4. **Post writing** — `write_post()` navigates to the Tistory manage dashboard, clicks through to the new-post editor, fills the title, injects HTML content, then clicks "완료" followed by "발행"
5. **Content injection** — `set_editor_content()` tries four strategies in order: TinyMCE JS API, TinyMCE iframe body, HTML-mode textarea, clipboard paste, and finally direct JS DOM injection into ProseMirror/contenteditable. The first one that works returns early
6. **Persist** — `save_published()` writes the updated URL list back to `published.json`; CI then commits this file with `[skip ci]`

The `login()` function (form-based Kakao login) exists in the code but is **not called** in `main()` — cookie injection replaced it. The env vars `TISTORY_EMAIL` / `TISTORY_PASSWORD` are referenced in `login()` but are not set as GitHub Secrets.

## Key Constants

| Name | Value | Purpose |
|---|---|---|
| `RSS_URL` | `https://rss.blog.naver.com/kcl3598.xml` | Source feed |
| `BLOG_NAME` | `kcl3598` | Tistory subdomain |
| `PUBLISHED_FILE` | `published.json` | Dedup state file |
| Batch size | `[:5]` in `main()` | Max posts per hourly run |

## GitHub Actions Workflows

- **`auto-post.yml`** — Runs on `schedule` (every hour, UTC) and `workflow_dispatch`. Installs deps, runs `post_to_tistory.py`, then commits any changes to `published.json`.
- **`summary.yml`** — On new issues, uses `actions/ai-inference` to comment with a one-paragraph AI summary.
- **`greetings.yml`** — Uses `actions/first-interaction` to post welcome messages on first PRs/issues.

## Session Cookies

The four Tistory cookies injected at runtime must be kept fresh in GitHub Secrets (`TISTORY_TSAL`, `TISTORY_XSRF_TOKEN`, `TISTORY_SESSION`, `TISTORY_TSSESSION`). When posts stop publishing, expired cookies are the most likely cause.
