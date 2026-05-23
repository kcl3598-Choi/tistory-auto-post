import asyncio
import feedparser
import json
import os
import sys
import traceback
from playwright.async_api import async_playwright

RSS_URL = "https://rss.blog.naver.com/kcl3598.xml"
BLOG_NAME = "kcl3598"
PUBLISHED_FILE = "published.json"
MAX_POSTS = int(os.environ.get("MAX_POSTS", "5"))
FAILED_FILE = "failed.json"
MAX_RETRIES = 3       # URL당 cross-run 최대 재시도 횟수
MAX_POST_ATTEMPTS = 2  # 포스트별 단일 실행 내 즉시 재시도 횟수

# TSAL, TSESSION은 Tistory에서 제거됨 — XSRF_TOKEN + TSSESSION만 사용
REQUIRED_ENV_VARS = ["TISTORY_XSRF_TOKEN", "TISTORY_TSSESSION"]


def load_published():
    if os.path.exists(PUBLISHED_FILE):
        with open(PUBLISHED_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_published(published):
    with open(PUBLISHED_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(published), f, ensure_ascii=False, indent=2)


def load_failed():
    if os.path.exists(FAILED_FILE):
        with open(FAILED_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}  # {url: 시도횟수}


def save_failed(failed):
    with open(FAILED_FILE, "w", encoding="utf-8") as f:
        json.dump(failed, f, ensure_ascii=False, indent=2)


async def _click_submit(page):
    for sel in [
        "button[type='submit']", "input[type='submit']",
        "button.btn_g", "button.btn_confirm", "button.submit",
        "button:has-text('로그인')", "button:has-text('다음')",
    ]:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=1500):
                await el.click()
                return
        except Exception:
            continue
    await page.keyboard.press("Enter")


async def set_editor_content(page, html_content):
    await page.wait_for_timeout(2000)

    # 방법 1: TinyMCE API 직접 호출
    result = await page.evaluate("""(content) => {
        if (window.tinymce && tinymce.activeEditor) {
            tinymce.activeEditor.setContent(content);
            return 'tinymce-api';
        }
        return null;
    }""", html_content)
    if result:
        print(f"[OK] {result}")
        return

    # 방법 2: TinyMCE iframe body에 직접 입력
    for frame in page.frames:
        try:
            body = await frame.query_selector("body#tinymce")
            if body:
                await frame.evaluate("""(content) => {
                    document.body.innerHTML = content;
                    document.body.dispatchEvent(new Event('input', {bubbles: true}));
                }""", html_content)
                print("[OK] TinyMCE iframe 직접 입력")
                return
        except Exception:
            continue

    # 방법 3: HTML 모드 버튼 클릭 후 textarea 입력
    for sel in ["button:has-text('HTML')", "[data-mode='html']", ".btn-mode-html", "button[title='HTML']"]:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=2000):
                await btn.click()
                await page.wait_for_timeout(1000)
                for area_sel in [".CodeMirror textarea", "textarea.html-source", ".html-editor textarea", "textarea"]:
                    code_area = page.locator(area_sel).first
                    if await code_area.is_visible(timeout=2000):
                        await code_area.click()
                        await page.keyboard.press("Control+a")
                        await page.keyboard.type(html_content, delay=0)
                        print(f"[OK] HTML 모드 입력 ({area_sel})")
                        return
        except Exception:
            continue

    # 방법 4: 클립보드 붙여넣기
    try:
        await page.evaluate("""(html) => {
            const blob = new Blob([html], {type: 'text/html'});
            return navigator.clipboard.write([new ClipboardItem({'text/html': blob})]);
        }""", html_content)
        for sel in [".ProseMirror", "[contenteditable='true']", ".editor-content"]:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=2000):
                    await el.click()
                    await page.keyboard.press("Control+a")
                    await page.keyboard.press("Control+v")
                    await page.wait_for_timeout(500)
                    print(f"[OK] 클립보드 붙여넣기 ({sel})")
                    return
            except Exception:
                continue
    except Exception as e:
        print(f"[DEBUG] 클립보드 실패: {e}")

    # 방법 5: JavaScript 직접 주입
    method = await page.evaluate("""(content) => {
        const pm = document.querySelector('.ProseMirror');
        if (pm) {
            pm.focus();
            pm.innerHTML = content;
            pm.dispatchEvent(new InputEvent('input', {bubbles: true}));
            pm.dispatchEvent(new Event('change', {bubbles: true}));
            return 'prosemirror';
        }
        const editors = document.querySelectorAll('[contenteditable="true"]');
        for (const el of editors) {
            if (el.closest('.title-area, .title-wrap, #post-title-inp')) continue;
            if (el.getAttribute('aria-label') && el.getAttribute('aria-label').includes('제목')) continue;
            el.focus();
            el.innerHTML = content;
            el.dispatchEvent(new InputEvent('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
            return 'contenteditable:' + (el.className || el.id || 'unknown');
        }
        return null;
    }""", html_content)

    if method:
        print(f"[OK] JS 주입 ({method})")
        return

    print("[WARN] 에디터 입력 실패 - 제목만 발행됩니다")


async def write_post(page, title, html_content):
    manage_url = f"https://{BLOG_NAME}.tistory.com/manage"
    await page.goto(manage_url, wait_until="networkidle", timeout=30000)
    await page.wait_for_timeout(2000)
    print(f"[write] 관리 URL: {page.url}")

    write_navigated = False
    for sel in [
        "a:has-text('글쓰기')", "button:has-text('글쓰기')",
        "a:has-text('새 글')", "a[href*='write']", "a[href*='newpost']",
        ".btn-write", "#btn-write",
    ]:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=2000):
                await el.click()
                await page.wait_for_load_state("networkidle", timeout=15000)
                write_navigated = True
                print(f"[write] 글쓰기 버튼 클릭 ({sel}) → {page.url}")
                break
        except Exception:
            continue

    if not write_navigated:
        links = await page.evaluate("() => Array.from(document.querySelectorAll('a, button')).map(e => e.textContent.trim().substring(0,15) + '|' + (e.href||'').substring(0,40)).filter(s=>s.length>1)")
        print(f"[write] 관리 페이지 링크/버튼: {links[:20]}")

    await page.wait_for_timeout(3000)
    print(f"[write] URL: {page.url} | title: {await page.title()}")

    html = await page.content()
    print(f"[write] HTML 앞부분: {html[:200]}")

    title_filled = False
    for sel in [
        "input#post-title-inp", "textarea#post-title-inp",
        "input.title", "textarea.title",
        "input[name='title']", "textarea[name='title']",
        "input#title", "textarea#title",
        "input[placeholder*='제목']", "textarea[placeholder*='제목']",
        ".title-area input", ".title-area textarea",
        ".editor-title input", ".editor-title textarea",
        "[contenteditable='true'][data-role='title']",
    ]:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=1500):
                await el.click()
                await el.fill(title)
                title_filled = True
                print(f"[write] 제목 입력 완료 ({sel})")
                break
        except Exception:
            continue

    if not title_filled:
        result = await page.evaluate("""(t) => {
            const candidates = [
                document.querySelector('#post-title-inp'),
                document.querySelector('input[name=title]'),
                document.querySelector('textarea[name=title]'),
                ...Array.from(document.querySelectorAll('input,textarea')).filter(e =>
                    e.placeholder && e.placeholder.includes('제목')
                ),
            ].filter(Boolean);
            for (const el of candidates) {
                el.focus();
                const nativeInput = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value') ||
                                    Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value');
                if (nativeInput) nativeInput.set.call(el, t);
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                return el.tagName + '#' + el.id + '.' + el.className.substring(0,20);
            }
            return null;
        }""", title)
        if result:
            title_filled = True
            print(f"[write] 제목 JS 입력 완료 ({result})")
        else:
            inputs = await page.evaluate("""() => Array.from(document.querySelectorAll('input,textarea')).map(e =>
                e.tagName+'#'+e.id+'.'+e.className.substring(0,15)+'|ph='+e.placeholder.substring(0,20))""")
            print(f"[write] 제목 입력 실패 - 페이지 inputs: {inputs[:10]}")

    await page.wait_for_timeout(1000)
    await set_editor_content(page, html_content)
    await page.wait_for_timeout(1000)

    completed_clicked = False
    for sel in ["button:has-text('완료')", "button.btn-posting-commit"]:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=3000):
                await btn.click()
                completed_clicked = True
                print(f"[write] 완료 버튼 클릭 ({sel})")
                await page.wait_for_timeout(2000)
                break
        except Exception:
            continue
    if not completed_clicked:
        buttons = await page.evaluate("() => Array.from(document.querySelectorAll('button')).map(b => b.textContent.trim().substring(0,20) + '|' + b.className.substring(0,20))")
        raise RuntimeError(f"'완료' 버튼을 찾지 못함 - 버튼 목록: {buttons}")

    publish_clicked = False
    for sel in [
        ".publish-layer button:has-text('발행')",
        ".layer-publish button:has-text('발행')",
        ".btn-publish-confirm",
        "button.btn-publish",
        ".setting-publish button:has-text('발행')",
        "button:has-text('발행')",
    ]:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=3000):
                await btn.click()
                publish_clicked = True
                print(f"[write] 발행 확정 클릭 ({sel})")
                await page.wait_for_timeout(2000)
                break
        except Exception:
            continue

    if not publish_clicked:
        buttons = await page.evaluate("() => Array.from(document.querySelectorAll('button')).map(b => b.textContent.trim().substring(0,20) + '|' + b.className.substring(0,20))")
        raise RuntimeError(f"'발행' 버튼을 찾지 못함 - 버튼 목록: {buttons}")

    await page.wait_for_load_state("networkidle", timeout=15000)
    print(f"[OK] 발행: {title}")


async def main():
    missing = [v for v in REQUIRED_ENV_VARS if not os.environ.get(v)]
    if missing:
        raise SystemExit(f"필수 환경 변수 없음: {', '.join(missing)}")

    feed = feedparser.parse(RSS_URL)
    if feed.get("bozo") and not feed.entries:
        print(f"RSS 피드 파싱 오류: {feed.get('bozo_exception', '알 수 없음')}")
        sys.exit(1)
    if not feed.entries:
        print("RSS 피드 항목 없음")
        return

    published = load_published()
    failed = load_failed()

    new_entries = [
        e for e in reversed(feed.entries)
        if e.get("link", "") not in published
        and failed.get(e.get("link", ""), 0) < MAX_RETRIES
    ][:MAX_POSTS]

    if not new_entries:
        skipped = sum(1 for e in feed.entries if failed.get(e.get("link", ""), 0) >= MAX_RETRIES)
        if skipped:
            print(f"새 글 없음 (재시도 한도 초과로 건너뜀: {skipped}건)")
        else:
            print("새 글 없음")
        return

    print(f"새 글 {len(new_entries)}개 발행 시작...")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        await context.grant_permissions(["clipboard-read", "clipboard-write"])

        cookies = [
            {"name": "TOP-XSRF-TOKEN", "value": os.environ["TISTORY_XSRF_TOKEN"], "domain": ".tistory.com", "path": "/", "secure": True},
            {"name": "TSSESSION",      "value": os.environ["TISTORY_TSSESSION"],  "domain": ".tistory.com", "path": "/", "secure": True, "httpOnly": True},
        ]
        await context.add_cookies(cookies)
        print(f"쿠키 주입 완료: {[c['name'] for c in cookies]}")

        page = await context.new_page()

        await page.goto("https://www.tistory.com", wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        page_title = await page.title()
        print(f"홈 URL: {page.url} | title: {page_title}")

        # 브라우저에 실제 설정된 쿠키 확인
        ctx_cookies = await context.cookies(["https://www.tistory.com"])
        print(f"[DEBUG] 브라우저 쿠키: {[c['name'] for c in ctx_cookies]}")

        if "login" in page.url or "auth" in page.url or "로그인" in page_title:
            print("[COOKIE_EXPIRED] 세션 쿠키 만료 — GitHub Secrets 갱신 필요")
            with open(".error_reason", "w") as f:
                f.write("COOKIE_EXPIRED")
            await browser.close()
            sys.exit(2)

        errors = []
        for entry in new_entries:
            link = entry.get("link", "")
            title = entry.get("title", "제목 없음")
            description = entry.get("description", "")
            content = (
                f"{description}"
                f"<br><br><hr>"
                f'<p>원문: <a href="{link}" target="_blank">{link}</a></p>'
            )
            last_error = None
            for attempt in range(1, MAX_POST_ATTEMPTS + 1):
                try:
                    await write_post(page, title, content)
                    published.add(link)
                    failed.pop(link, None)
                    print(f"[성공] {title}" + (f" (재시도 {attempt}회차)" if attempt > 1 else ""))
                    last_error = None
                    break
                except Exception as e:
                    last_error = e
                    if attempt < MAX_POST_ATTEMPTS:
                        print(f"[재시도] {title} ({attempt}/{MAX_POST_ATTEMPTS}): {e}")
                        await page.wait_for_timeout(15000)
            if last_error is not None:
                failed[link] = failed.get(link, 0) + 1
                attempts = failed[link]
                errors.append((title, link, str(last_error), attempts))
                print(f"[실패] {title} (시도 {attempts}/{MAX_RETRIES}): {last_error}")

        await browser.close()

    save_published(published)
    save_failed(failed)

    if errors:
        print(f"\n===== 발행 실패 요약 ({len(errors)}건) =====")
        for title, link, err, attempts in errors:
            if attempts >= MAX_RETRIES:
                status = f"재시도 한도 초과 ({MAX_RETRIES}회) — 이후 건너뜀"
            else:
                status = f"다음 실행 시 재시도 ({attempts}/{MAX_RETRIES})"
            print(f"  제목: {title}")
            print(f"  링크: {link}")
            print(f"  오류: {err}")
            print(f"  상태: {status}")
        sys.exit(1)

    print("완료")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        sys.exit(1)
