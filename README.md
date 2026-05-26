# tistory-auto-post

네이버 블로그 RSS → 티스토리 자동 크로스포스팅 (GitHub Actions)

## 동작 방식

1. 네이버 블로그 RSS 피드 파싱 (`feedparser`)
2. `published.json`과 비교해 미발행 글 필터링 (최대 5개)
3. Playwright 헤드리스 Chromium으로 티스토리 에디터 자동 입력
4. 발행 완료 후 `published.json` 업데이트 → 자동 커밋

매시간 자동 실행됩니다 (GitHub Actions Cron).

## 인증

티스토리 세션 쿠키 4개를 GitHub Actions Secrets에 등록해야 합니다.

| Secret 이름 | 설명 |
|-------------|------|
| `TISTORY_TSAL` | TSAL 쿠키 |
| `TISTORY_XSRF_TOKEN` | TOP-XSRF-TOKEN 쿠키 |
| `TISTORY_SESSION` | TSESSION 쿠키 |
| `TISTORY_TSSESSION` | TSSESSION 쿠키 |

쿠키는 브라우저 개발자 도구에서 로그인 후 수동 추출합니다.

## 로컬 실행

```bash
pip install -r requirements.txt
playwright install chromium --with-deps

# 환경 변수 설정 후
python post_to_tistory.py
```

## 설정 변경

`post_to_tistory.py` 상단 상수 수정:

```python
RSS_URL   = "https://rss.blog.naver.com/your_id.xml"
BLOG_NAME = "your_tistory_id"
```

## 주요 파일

| 파일 | 설명 |
|------|------|
| `post_to_tistory.py` | 메인 스크립트 |
| `published.json` | 발행 완료 URL 목록 (삭제 금지) |
| `.github/workflows/auto-post.yml` | 매시간 실행 워크플로우 |
