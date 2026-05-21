import asyncio
import feedparser
import json
import os
from playwright.async_api import async_playwright

RSS_URL = "https://rss.blog.naver.com/kcl3598.xml"
BLOG_NAME = "kcl3598"
PUBLISHED_FILE = "published.json"
MAX_POSTS = int(os.environ.get("MAX_POSTS", "5"))

REQUIRED_ENV_VARS = ["TISTORY_TSAL", "TISTORY_XSRF_TOKEN", "TISTORY_SESSION", "TISTORY_TSSESSION"]


def load_published():
    if os.path.exists(PUBLISHED_FILE):
        with open(PUBLISHED_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_published(published):
    with open(PUBLISHED_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(published), f, ensure_ascii=False, indent=2)


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

    # 방법 1: HTML 모드 버튼 클릭 후 textarea 입력
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

    # 방법 2: 클립보드 붙여넣기
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

    # 방법 3: JavaScript 직접 주입
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
    # 관리 대시보드 먼저 방문 후 쓰기 페이지 이동
    manage_url = f"https://{BLOG_NAME}.tistory.com/manage"
    await page.goto(manage_url, wait_until="networkidle", timeout=30000)
    await page.wait_for_timeout(2000)
    print(f"[write] 관리 URL: {page.url}")

    # 관리 페이지에서 글쓰기 버튼 클릭
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
        # 관리 페이지 버튼/링크 목록 출력
        links = await page.evaluate("() => Array.from(document.querySelectorAll('a, button')).map(e => e.textContent.trim().substring(0,15) + '|' + (e.href||'').substring(0,40)).filter(s=>s.length>1)")
        print(f"[write] 관리 페이지 링크/버튼: {links[:20]}")

    await page.wait_for_timeout(3000)
    print(f"[write] URL: {page.url} | title: {await page.title()}")

    html = await page.content()
    print(f"[write] HTML 앞부분: {html[:200]}")

    # 제목 입력
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
        # JS로 제목 입력 시도
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

    # 본문 입력
    await set_editor_content(page, html_content)
    await page.wait_for_timeout(1000)

    # 1단계: '완료' 버튼 클릭 → 발행 설정 패널 열기
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
        print(f"[write] 완료 버튼 없음 - 버튼 목록: {buttons}")

    # 2단계: 발행 설정 패널에서 '발행' 버튼 클릭 (공개 발행)
    published = False
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
                published = True
                print(f"[write] 발행 확정 클릭 ({sel})")
                await page.wait_for_timeout(2000)
                break
        except Exception:
            continue

    if not published:
        buttons = await page.evaluate("() => Array.from(document.querySelectorAll('button')).map(b => b.textContent.trim().substring(0,20) + '|' + b.className.substring(0,20))")
        print(f"[write] 발행 버튼 없음 - 버튼 목록: {buttons}")

    await page.wait_for_load_state("networkidle", timeout=15000)
    print(f"[OK] 발행: {title}")


async def main():
    missing = [v for v in REQUIRED_ENV_VARS if not os.environ.get(v)]
    if missing:
        raise SystemExit(f"필수 환경 변수 없음: {', '.join(missing)}")

    feed = feedparser.parse(RSS_URL)
    if not feed.entries:
        print("RSS 피드 항목 없음")
        return

    published = load_published()
    new_entries = [e for e in reversed(feed.entries) if e.get("link", "") not in published][:MAX_POSTS]

    if not new_entries:
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

        # 쿠키 주입으로 로그인 대체
        cookies = [
            {"name": "TSAL",           "value": os.environ["TISTORY_TSAL"],       "domain": ".tistory.com", "path": "/", "secure": True},
            {"name": "TOP-XSRF-TOKEN", "value": os.environ["TISTORY_XSRF_TOKEN"], "domain": ".tistory.com", "path": "/", "secure": True},
            {"name": "TSESSION",       "value": os.environ["TISTORY_SESSION"],    "domain": ".tistory.com", "path": "/", "secure": True, "httpOnly": True},
            {"name": "TSSESSION",      "value": os.environ["TISTORY_TSSESSION"],  "domain": ".tistory.com", "path": "/", "secure": True, "httpOnly": True},
        ]
        await context.add_cookies(cookies)
        print("쿠키 주입 완료")

        page = await context.new_page()

        # 로그인 확인
        await page.goto("https://www.tistory.com", wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        print(f"홈 URL: {page.url} | title: {await page.title()}")

        for entry in new_entries:
            link = entry.get("link", "")
            title = entry.get("title", "제목 없음")
            description = entry.get("description", "")
            content = (
                f"{description}"
                f"<br><br><hr>"
                f'<p>원문: <a href="{link}" target="_blank">{link}</a></p>'
            )
            try:
                await write_post(page, title, content)
                published.add(link)
            except Exception as e:
                print(f"[ERROR] {title}: {e}")

        await browser.close()

    save_published(published)
    print("완료")


if __name__ == "__main__":
    asyncio.run(main())
