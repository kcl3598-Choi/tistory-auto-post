import asyncio
import feedparser
import json
import os
import re
from playwright.async_api import async_playwright

RSS_URL = "https://rss.blog.naver.com/kcl3598.xml"
BLOG_NAME = "kcl3598"
WRITE_URL = f"https://{BLOG_NAME}.tistory.com/manage/post/write"
PUBLISHED_FILE = "published.json"


def load_published():
    if os.path.exists(PUBLISHED_FILE):
        with open(PUBLISHED_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_published(published):
    with open(PUBLISHED_FILE, "w", encoding="utf-8") as f:
        json.dump(published, f, ensure_ascii=False, indent=2)


async def login(page):
    email = os.environ["TISTORY_EMAIL"]
    password = os.environ["TISTORY_PASSWORD"]

    await page.goto("https://www.tistory.com/auth/login", wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)

    # Tistory → Kakao 로그인 페이지로 리다이렉트됨
    # 이메일/아이디로 로그인 옵션이 있으면 클릭
    try:
        alt_login = page.locator("a:has-text('이메일'), button:has-text('아이디'), a.link-connect-kakao").first
        if await alt_login.is_visible(timeout=3000):
            await alt_login.click()
            await page.wait_for_timeout(1000)
    except Exception:
        pass

    # Kakao 로그인 폼
    await page.fill("input#loginKey, input[name='loginKey']", email)
    await page.fill("input#password, input[name='password']", password)
    await page.click("button[type='submit']")
    await page.wait_for_load_state("networkidle", timeout=20000)
    print("로그인 완료")


async def set_editor_content(page, html_content):
    # 방법 1: HTML 모드 버튼 클릭 후 입력
    for sel in ["button:has-text('HTML')", "[data-mode='html']", ".btn-mode-html", "button[title='HTML']"]:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=2000):
                await btn.click()
                await page.wait_for_timeout(1000)

                code_area = page.locator(".CodeMirror textarea, textarea.html-source, .html-editor textarea").first
                if await code_area.is_visible(timeout=2000):
                    await code_area.click()
                    await page.keyboard.press("Control+a")
                    await page.keyboard.type(html_content, delay=0)
                    print("[OK] HTML 모드 입력")
                    return
        except Exception:
            continue

    # 방법 2: JavaScript로 에디터에 직접 주입
    method = await page.evaluate("""(content) => {
        const pm = document.querySelector('.ProseMirror');
        if (pm) {
            pm.innerHTML = content;
            pm.dispatchEvent(new Event('input', {bubbles: true}));
            return 'prosemirror';
        }
        const editors = document.querySelectorAll('[contenteditable="true"]');
        for (const el of editors) {
            if (el.closest('.title-area, .title-wrap')) continue;
            el.innerHTML = content;
            el.dispatchEvent(new Event('input', {bubbles: true}));
            return 'contenteditable';
        }
        return null;
    }""", html_content)

    if method:
        print(f"[OK] JS 주입 ({method})")
        return

    print("[WARN] 에디터 입력 실패 - 제목만 발행됩니다")


async def write_post(page, title, html_content):
    await page.goto(WRITE_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)

    # 제목 입력
    for sel in ["input#post-title-inp", "input.title", "input[placeholder*='제목']", ".title-area input"]:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=2000):
                await el.click()
                await el.fill(title)
                break
        except Exception:
            continue

    await page.wait_for_timeout(1000)

    # 본문 입력
    await set_editor_content(page, html_content)
    await page.wait_for_timeout(1000)

    # 발행 버튼 클릭
    for sel in ["button:has-text('완료')", "button:has-text('발행')", ".btn-publish", "button.btn-posting-commit"]:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=2000):
                await btn.click()
                await page.wait_for_timeout(2000)
                break
        except Exception:
            continue

    # 발행 확인 팝업 처리
    try:
        confirm = page.locator(".layer-popup button:has-text('발행'), .modal button:has-text('확인'), .btn-confirm").first
        if await confirm.is_visible(timeout=3000):
            await confirm.click()
    except Exception:
        pass

    await page.wait_for_load_state("networkidle", timeout=15000)
    print(f"[OK] 발행: {title}")


async def main():
    feed = feedparser.parse(RSS_URL)
    if not feed.entries:
        print("RSS 피드 항목 없음")
        return

    published = load_published()
    new_entries = [e for e in reversed(feed.entries) if e.get("link", "") not in published]

    if not new_entries:
        print("새 글 없음")
        return

    print(f"새 글 {len(new_entries)}개 발행 시작...")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        await login(page)

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
                published.append(link)
            except Exception as e:
                print(f"[ERROR] {title}: {e}")

        await browser.close()

    save_published(published)
    print("완료")


if __name__ == "__main__":
    asyncio.run(main())
