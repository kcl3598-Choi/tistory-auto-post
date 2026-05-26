"""
네이버 블로그 전체 글 → 티스토리 마이그레이션
실행: python migrate_all.py

.env 파일 필요:
  TISTORY_EMAIL=카카오계정이메일
  TISTORY_PASSWORD=카카오계정비밀번호
"""
import asyncio
import json
import os
import re
import sys

from playwright.async_api import async_playwright

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

NAVER_BLOG_ID = "kcl3598"
TISTORY_BLOG_NAME = "kcl3598"
PUBLISHED_FILE = "published.json"


# ── 발행 이력 관리 ────────────────────────────────────────────────────────────────

def load_published_ids():
    """기존 published.json에서 포스트 ID 집합 반환 (URL·순수ID 혼재 대응)"""
    if not os.path.exists(PUBLISHED_FILE):
        return set()
    with open(PUBLISHED_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    ids = set()
    for entry in data:
        m = re.search(r"/(\d{9,})", str(entry))
        if m:
            ids.add(m.group(1))
        elif re.match(r"^\d+$", str(entry)):
            ids.add(str(entry))
    return ids


def save_published_ids(ids: set):
    with open(PUBLISHED_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(ids), f, ensure_ascii=False, indent=2)


# ── 네이버 블로그 스크래핑 ────────────────────────────────────────────────────────

async def get_all_naver_post_ids(page):
    """포스트 목록 페이지를 순회하며 전체 포스트 ID 수집"""
    all_ids = []
    page_no = 1

    while True:
        url = (
            f"https://blog.naver.com/PostList.naver"
            f"?blogId={NAVER_BLOG_ID}&categoryNo=0"
            f"&currentPage={page_no}&postListType=ALL"
        )
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
        except Exception:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(1500)

        found = await page.evaluate(
            """(blogId) => {
                const ids = new Set();
                document.querySelectorAll('a').forEach(a => {
                    const m1 = a.href.match(new RegExp('\\/' + blogId + '\\/(\\\\d{9,})'));
                    if (m1) ids.add(m1[1]);
                    const m2 = a.href.match(/[?&]logNo=(\\d{9,})/);
                    if (m2) ids.add(m2[1]);
                });
                return [...ids];
            }""",
            NAVER_BLOG_ID,
        )

        new_ids = [i for i in found if i not in all_ids]
        if not new_ids:
            print(f"  페이지 {page_no}: 새 포스트 없음 → 수집 완료")
            break

        all_ids.extend(new_ids)
        print(f"  페이지 {page_no}: {len(new_ids)}개 발견 (누적 {len(all_ids)}개)")
        page_no += 1

    return all_ids


async def fetch_naver_post(page, post_id):
    """모바일 네이버 블로그에서 제목 + HTML 본문 추출"""
    url = f"https://m.blog.naver.com/{NAVER_BLOG_ID}/{post_id}"
    try:
        await page.goto(url, wait_until="networkidle", timeout=30000)
    except Exception:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_timeout(2000)

    data = await page.evaluate("""() => {
        const TITLE_SELS = [
            '.se-title-text', '.se-module-title .se-text-paragraph',
            '.tit_h3', '.post_title', 'h2.se-title', 'h2', 'h3'
        ];
        let title = '';
        for (const s of TITLE_SELS) {
            const el = document.querySelector(s);
            if (el && el.textContent.trim()) { title = el.textContent.trim(); break; }
        }
        if (!title)
            title = document.title.replace(/\\s*[：:·|]\\s*네이버 블로그.*/, '').trim();

        const CONTENT_SELS = [
            '.se-main-container', '.se_component_wrap',
            '#postViewArea', '.post_ct', '.view_wrap'
        ];
        let content = '';
        for (const s of CONTENT_SELS) {
            const el = document.querySelector(s);
            if (el) { content = el.innerHTML; break; }
        }
        return { title, content };
    }""")

    if not data["content"]:
        return None, None

    orig_url = f"https://blog.naver.com/{NAVER_BLOG_ID}/{post_id}"
    html = (
        data["content"]
        + "\n<br><hr>\n"
        + f'<p style="font-size:12px;color:#999">'
        + f'원문: <a href="{orig_url}" target="_blank">{orig_url}</a></p>'
    )
    return data["title"] or f"포스트 {post_id}", html


# ── 티스토리 자동화 ───────────────────────────────────────────────────────────────

async def _submit(page):
    for sel in [
        "button[type='submit']", "input[type='submit']",
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


async def tistory_login(page):
    email = os.environ["TISTORY_EMAIL"]
    password = os.environ["TISTORY_PASSWORD"]

    await page.goto("https://www.tistory.com/auth/login", wait_until="networkidle", timeout=30000)
    await page.wait_for_timeout(3000)

    for sel in ["a[href*='kakao']", ".btn_login_kakao", "a:has-text('카카오')", "button:has-text('카카오')"]:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=1500):
                await btn.click()
                await page.wait_for_timeout(2000)
                break
        except Exception:
            continue

    for sel in ["input#loginKey", "input[name='loginKey']", "input[type='email']",
                "input[placeholder*='이메일']", "input[placeholder*='아이디']"]:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=2000):
                await el.fill(email)
                break
        except Exception:
            continue

    await _submit(page)
    await page.wait_for_timeout(2000)

    for sel in ["input#password", "input[name='password']", "input[type='password']"]:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=3000):
                await el.fill(password)
                break
        except Exception:
            continue

    await _submit(page)
    try:
        await page.wait_for_url("**/tistory.com/**", timeout=15000)
    except Exception:
        pass
    await page.wait_for_load_state("networkidle", timeout=10000)

    if "tistory.com" not in page.url:
        raise Exception(f"로그인 실패: {page.url}")
    print("[로그인 완료]")


async def set_editor_content(page, html_content):
    await page.wait_for_timeout(2000)

    result = await page.evaluate("""(c) => {
        if (window.tinymce && tinymce.activeEditor) {
            tinymce.activeEditor.setContent(c); return 'tinymce';
        }
        return null;
    }""", html_content)
    if result:
        return

    for frame in page.frames:
        try:
            body = await frame.query_selector("body#tinymce")
            if body:
                await frame.evaluate("""(c) => {
                    document.body.innerHTML = c;
                    document.body.dispatchEvent(new Event('input', {bubbles: true}));
                }""", html_content)
                return
        except Exception:
            continue

    method = await page.evaluate("""(c) => {
        const pm = document.querySelector('.ProseMirror');
        if (pm) {
            pm.focus(); pm.innerHTML = c;
            pm.dispatchEvent(new InputEvent('input', {bubbles: true}));
            return 'prosemirror';
        }
        for (const el of document.querySelectorAll('[contenteditable="true"]')) {
            const label = el.getAttribute('aria-label') || '';
            if (el.closest('.title-area,.title-wrap') || label.includes('제목')) continue;
            el.focus(); el.innerHTML = c;
            el.dispatchEvent(new InputEvent('input', {bubbles: true}));
            return 'contenteditable';
        }
        return null;
    }""", html_content)
    if not method:
        print("[WARN] 에디터 입력 실패")


async def write_tistory_post(page, title, html_content):
    await page.goto(
        f"https://{TISTORY_BLOG_NAME}.tistory.com/manage",
        wait_until="networkidle", timeout=30000,
    )
    await page.wait_for_timeout(2000)

    for sel in ["a:has-text('글쓰기')", "a[href*='newpost']", "a[href*='write']", ".btn-write"]:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=2000):
                await el.click()
                await page.wait_for_load_state("networkidle", timeout=15000)
                break
        except Exception:
            continue
    await page.wait_for_timeout(3000)

    for sel in ["input#post-title-inp", "textarea#post-title-inp",
                "input[placeholder*='제목']", "textarea[placeholder*='제목']"]:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=1500):
                await el.click()
                await el.fill(title)
                break
        except Exception:
            continue

    await page.wait_for_timeout(1000)
    await set_editor_content(page, html_content)
    await page.wait_for_timeout(1000)

    for sel in ["button:has-text('완료')", "button.btn-posting-commit"]:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=3000):
                await btn.click()
                await page.wait_for_timeout(2000)
                break
        except Exception:
            continue

    for sel in [
        ".publish-layer button:has-text('발행')",
        ".layer-publish button:has-text('발행')",
        "button.btn-publish",
        "button:has-text('발행')",
    ]:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=3000):
                await btn.click()
                await page.wait_for_timeout(2000)
                break
        except Exception:
            continue

    await page.wait_for_load_state("networkidle", timeout=15000)
    print(f"  발행 완료: {title}")


# ── 메인 ──────────────────────────────────────────────────────────────────────────

async def main():
    if not os.environ.get("TISTORY_EMAIL") or not os.environ.get("TISTORY_PASSWORD"):
        print("ERROR: .env 파일에 TISTORY_EMAIL 과 TISTORY_PASSWORD 를 설정해주세요.")
        sys.exit(1)

    published_ids = load_published_ids()
    print(f"기존 마이그레이션 완료: {len(published_ids)}개")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,  # 로그인 확인을 위해 창 표시
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
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
        page = await context.new_page()

        # 1단계: 네이버 블로그 포스트 목록 수집
        print("\n[1단계] 네이버 블로그 포스트 목록 수집 중...")
        all_ids = await get_all_naver_post_ids(page)
        print(f"총 {len(all_ids)}개 포스트 발견")

        todo = [i for i in all_ids if i not in published_ids]
        skip = len(all_ids) - len(todo)
        print(f"마이그레이션 대상: {len(todo)}개 (기존 완료: {skip}개 건너뜀)\n")

        if not todo:
            print("모든 포스트가 이미 마이그레이션되었습니다.")
            await browser.close()
            return

        # 2단계: 티스토리 로그인
        print("[2단계] 티스토리 로그인...")
        await tistory_login(page)

        # 3단계: 마이그레이션
        print(f"\n[3단계] {len(todo)}개 포스트 마이그레이션 시작\n")
        success = fail = 0
        for idx, post_id in enumerate(todo, 1):
            print(f"[{idx}/{len(todo)}] 포스트 {post_id} 처리 중...")
            try:
                title, html = await fetch_naver_post(page, post_id)
                if not title or not html:
                    print(f"  → 내용 없음, 건너뜀")
                    fail += 1
                    continue
                await write_tistory_post(page, title, html)
                published_ids.add(post_id)
                save_published_ids(published_ids)  # 매 발행 후 즉시 저장
                success += 1
            except Exception as e:
                print(f"  [ERROR] {e}")
                fail += 1

            await asyncio.sleep(3)  # 서버 부하 방지

        await browser.close()

    print(f"\n완료: {success}개 성공 / {fail}개 실패 (대상 {len(todo)}개)")


if __name__ == "__main__":
    asyncio.run(main())
