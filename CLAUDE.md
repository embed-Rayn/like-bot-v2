# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`like-bot-v2` is a rewrite of a legacy Naver Blog engagement tool that lives in `legacy/`.

**Purpose — read this before any design decision:**

This tool exists to **drive traffic to the operator's own blog**. The mechanism is
reciprocity, not volume:

> The operator visits other people's blogs and shows interest (presses 좋아요/공감).
> Those blog owners notice the visit and come back to look at the operator's blog.

Everything else in the design follows from that one sentence:

- **The account pressing 좋아요 must be the operator's own blog account.** Reciprocal
  visits land on whoever pressed the like. Splitting the work across several accounts
  scatters the return traffic across several blogs and defeats the purpose.
- **Topic + date targeting exists to raise the reply-visit rate**, not to find "any blog".
  Recent posts in a matching niche mean an active blogger who is currently paying
  attention and shares the operator's subject matter — the population most likely to
  visit back.
- **Breadth beats depth.** A few likes on many blogs produces more return visits than
  many likes on few blogs, and looks less like spam.
- **Account safety is a first-class requirement.** A suspended account produces zero
  traffic. Anything that increases per-account like velocity must be paid for with
  rate limiting.

## Current Status

The engine and the PyQt6 desktop app are implemented and were exercised end to end against
the live site on 2026-08-31 — search → RSS → session reuse → real 공감 clicks — from both
`tools/dryrun.py` and the desktop UI, single- and multi-keyword. Web/Linux is still deferred
(build order 7).

Logging in is done by a person, never by the app: type the 네이버 ID and press 실행, and if
the saved session is gone a browser window opens for you to log in yourself. The app does not
type credentials — that is what triggered Naver's 추가 확인 and the account protection
(2026-09-11, see "Live-site facts"). `tools/login.py <네이버ID>` does the same from the CLI.

```
engine/    search · posts(RSS) · session · like · runner · ratelimit · history · safety · runlog
desktop/   app.py (MainWindow) · bridge.py (asyncio↔Qt) · widgets.py (KeywordPanel)
tools/     login.py (수동 로그인 부트스트랩) · dryrun.py · refresh_fixtures.py
tests/     unit + `contract` (live Naver, no login) + `browser` (needs chromium)
```

Run the app with `python -m desktop.app`.

Do not treat anything in `legacy/` as the target architecture. It is reference material
for behavior and intent only.

## Legacy Reference (`legacy/`)

Python + PyQt6 + Selenium (Chrome) + pyperclip, shipped via PyInstaller.

```
ui_like_search_post_multi.py   Entry point. NaverLikeApp(QMainWindow) — input + 4 threads
search_post_multi.py(.ui)      pyuic6-generated UI, absolute coordinates, 1323x890
core/like_thread.py            LikeThread(QThread) — the actual algorithm
utils/login.py                 Chrome launch + Naver login
utils/blog.py                  Search / post list / like / sympathizer scraping (XPath)
dialogs/window_dialog.py       Account picker + date-range dialogs
dialogs/dialog_keyword.ui      UNUSED — abandoned per-keyword parallel UI
accounts.csv                   Naver credentials, PLAINTEXT
exclude.csv                    Always-on exclusion terms (2nd column)
```

Legacy flow: login → paginate `section.blog.naver.com` search (period + `orderBy=recentdate`)
→ collect blog IDs → for each blog, open `PostList.naver`, take the newest 5 posts, like
the top N → **BFS-expand into everyone who liked that post** (`SympathyHistoryList.naver`)
→ repeat until a like goal is met.

### Known legacy defects (context for why v2 exists)

1. **Result-count parser dead, page cap accidentally correct.** `core/like_thread.py:53-60`
   discards the parsed result count and hardcodes `count = 1000`, then `p_count = count // 7`
   → page 142. A comment (`# 260210 파악 불가`) marks the date Naver's DOM change broke the
   parser, and `get_total_post_count()` is still called with its result thrown away.
   **However — measured 2026-08-30 — 1000 is Naver's own cap and page 143 really is the
   last page, so the hardcoded cap matches reality.** The UI's "572 pages" was never
   reachable. The real cost is the dead parser and the lost progress indicator: narrow
   keywords do return a true count (751 for one tested phrase), which v2 uses. See
   `docs/superpowers/specs/2026-08-30-like-bot-v2-design.md` §3.1.
2. **Absolute/index-based XPath everywhere** (e.g. `//*[@id="content"]/section/div[2]/div/div/div[1]/div[1]/a[1]`).
   Already broke once — see defect 1.
3. **Login failure is not detected.** `run()` logs the exception and continues; if a driver
   exists the whole session runs logged out and every like fails.
4. Multi-account parallelism is impossible — ID/PW are global, so all 4 threads use one account.
5. Sympathizer scraping runs once per liked post instead of once per blog (`like_thread.py:118-122`).
6. Stopping during collection returns `0, 0, 0`, discarding the count so far.
7. Only the running account is excluded; other accounts in `accounts.csv` can like each other.
8. UI defaults disagree with the `except ValueError` fallbacks (likes 3 vs 2, depth 1 vs 2,
   goal 9999 vs 300, end page 572 vs 400), and one bad field silently resets all eight.
9. Broad `except Exception` + messages like `"공감 없음 or 이미 함"` make failures undiagnosable.
10. Plaintext credentials; `detach=True` negated by `driver.quit()`; dead code
    (`is_exist_like_button_and_push`, empty `utils/file_io.py`, duplicate import in `utils/__init__.py`).

## v2 Design Decisions

Settled with the user:

1. **No BFS.** Drop sympathizer-graph expansion entirely. Traverse the search results and
   like N posts per blog, and that is all. Removes `get_buddy_ids`, `extract_buddy_info`,
   the `Depth` input, the `except bot` checkbox, and all `SympathyHistoryList` access.
2. **Traversal scope: run to the end of the results.** Increment the page number until a
   page yields zero blog links, then stop. Do not parse a total result count — that is the
   parser that broke (defect 1). The user-facing page-range inputs go away.
3. **Playwright replaces Selenium.** Chosen for: intercepting Naver's JSON responses instead
   of scraping absolute XPath (fixes defect 2 structurally), `frame_locator("#mainFrame")`
   instead of `switch_to.frame`, auto-waiting instead of scattered `time.sleep(0.3 * latency)`,
   `storage_state` session reuse so the login page (the most heavily defended and most
   dangerous step) is rarely visited, and bundled browsers instead of chromedriver version
   drift.
4. **Parallelism is per-keyword, never per-account.** One account (the operator's own blog)
   works several keywords concurrently. Required companions:
   - **Account-level global rate limiting** — one shared like budget across all workers, not
     per worker. Four workers on one account is a 4x velocity increase and must be capped.
   - **One browser context, several tabs** (`new_context(storage_state=...)` + `new_page()`),
     so the workers share one cookie session instead of opening several logins for the
     same account.
   - **A shared visited-blog set across workers**, since keywords overlap.

5. **Stop condition: visited-blog count cap, or search results exhausted** — whichever comes
   first. The cap is a global per-account budget, not per worker. Blogs, not likes, is the
   right unit: one blog owner is one chance at a reply visit regardless of how many of their
   posts got liked.
6. **Visit history is persisted, scoped to the logged-in account** — `visits` is keyed on
   `(account, blog_id)`, so a cap of N means N *new* blogs every run, and switching accounts
   never makes account B skip blogs only account A has visited. Sessions are stored per
   account for the same reason.
7. **Build order: engine → PyQt6 desktop → (later) web/Linux.** Scope of the current work is
   engine + desktop. Web is deferred for verification order, not effort: selector strategy
   and safe pacing must be confirmed headful before anything runs headless and unattended.
8. **asyncio core + PyQt bridge.** Playwright's sync API is not thread-safe, so `QThread`
   workers would each need their own browser context — four logins for one account. A single
   event loop also means the rate-limit tokens and shared visit set need no locks.

**Measured against the live site on 2026-08-30** (see the spec for full detail):
`https://section.blog.naver.com/ajax/SearchList.naver` serves search results as JSON without
login (`)]}',` prefix, `pagePerCount=7`, `totalCount` capped at 1000, page 143 last), and
`https://rss.blog.naver.com/{blogId}.xml` gives recent post lists (7/7 blogs tested). So
**the only step that needs a browser is the like click** — target discovery is plain HTTP.
Parallelism therefore lives in discovery; likes are one rate-limited stream on a single tab.

Full design: `docs/superpowers/specs/2026-08-30-like-bot-v2-design.md`.

## Live-site facts (verified 2026-08-31)

Naver changes its markup without notice — that is legacy defect 2, and it recurred. Every
selector below is measured, and each is watched by a `browser`+`contract` test so the next
change fails a test instead of a run.

- **Login page.** `#id` / `#pw` unchanged. The submit button is `#loginBtn_column` /
  `#loginBtn_row` — rendered twice for the responsive layout, so click the visible one. The
  old `.btn_login` no longer exists.
- **앱은 자격증명을 입력하지 않는다 — 자동 로그인 폐지 (운영자 결정, 2026-09-11).**
  Playwright로 아이디/비밀번호를 타이핑하면 네이버가 "보안을 위해 추가 확인"(이미지
  문제)을 띄운다. 그것을 프로그램으로 뚫으려 드는 것은 봇 탐지 회피이고 계정을 건다.
  그런데 어차피 그 화면은 사람이 푼다 — 즉 **자동 입력이 계정에 남기는 것은 "실패한
  자동 로그인 시도" 기록뿐이고, 그게 쌓이면 보호 조치가 된다.** 2026-09-11 실행 로그가
  3분 간격 연속 자동 로그인을 보여 준다. 그래서 시도 자체를 하지 않는다.
  | 상황 | 동작 |
  | --- | --- |
  | 세션 유효 | 로그인 페이지를 아예 거치지 않는다 (결정 3) |
  | 세션 없음/만료 | 로그인 창만 열고 사람을 기다린다 (아래 "대기의 끝") |

  UI에 비밀번호 칸이 없고 `keyring`도 쓰지 않는다 — 쓰지 않는 자격증명 저장소를 남겨
  두면 운영자는 그 칸이 쓰인다고 믿고 입력한다. `_login()`은 막힌 화면을 여전히
  분류해 `on_challenge`로 알린다(캡차인지 2차 인증인지에 따라 창에서 할 일이 다르다).
  타임아웃만 예외를 올린다. `tools/login.py`는 앱을 열지 않고 같은 일을 하는 CLI다.
- **로그인 여부는 로그인 페이지에 물어서는 안 된다 (2026-09-11 사고).** `_is_logged_in()`은
  `LOGIN_PROBE_URL`(`https://blog.naver.com/MyBlog.naver` — 로그인해야만 볼 수 있는
  페이지)을 열어 보고, nid 로그인 폼으로 튕기면 로그아웃으로 판정한다. 예전에는
  로그인 페이지 자체를 열고 "아직 nid인가"로 판정했는데, 그 질문에는 답이 없다 —
  로그인 폼이 보이는 것은 로그인/로그아웃 양쪽에서 다 일어난다. 실측(2026-09-11,
  로그아웃)에서 두 주소는 로그아웃 상태에서 구분되지 않았고, 차이는 로그인 상태에서만
  드러난다. 대가는 두 겹이었다: **멀쩡한 세션까지 로그아웃으로 몰아 버려
  `_discard_stale_session()`이 지우고 매 실행 자동 로그인으로 갔고(→ 계정 보호 조치),
  사람이 손으로 로그인을 끝낸 직후에도 같은 판정이 False라서
  `SessionExpired("로그인 직후 세션이 확인되지 않았습니다")`로 터졌다**
  (run-20260911-231421). 판정 불가(타임아웃)는 로그아웃으로 친다 — 반대로 틀리면
  실행 전체가 로그아웃으로 돌며 공감이 전부 401로 거부된다(2026-09-10 사고).
  `tests/test_login_probe_contract.py`가 이 주소가 아직 로그인 필수인지 감시한다.
- **기기 식별 쿠키를 버리면 매번 "처음 보는 기기"가 된다 (2026-09-11).** 자동 입력을
  없앤 뒤에도 보호조치가 떴고, 원인은 입력이 아니라 `_discard_stale_session()`이었다.
  그것이 `clear_cookies()`를 인자 없이 불러 쿠키를 통째로 비웠는데, 저장된 세션에는
  인증 쿠키만 있는 게 아니다 — 실측하면 14개 중 대부분이 `NNB`·`NAC`·`NACT`·`BUC`·
  `nid_inf` 같은 **기기/방문자 식별** 쿠키다. 그것까지 지우면 다음에 열리는 로그인
  창에 네이버가 이 브라우저를 알아볼 단서가 하나도 없고, 새 기기 로그인은 곧 추가
  확인이다. 지울 것은 `AUTH_COOKIES`(`NID_AUT`, `NID_SES`)뿐이다 — 낡은 NID_AUT를
  남기면 `wait_for_manual_login()`이 사람이 손대기도 전에 "완료"로 판정하므로 그 둘은
  반드시 지워야 하고, 나머지는 반드시 남겨야 한다. `navigator.webdriver`를 감추는 식의
  탐지 회피는 하지 않는다 — 그건 계정을 거는 일이다. 기기 신원을 **버리지 않는 것**은
  회피가 아니라 평범한 브라우저처럼 구는 것이다.
- **대기의 끝은 타이머가 아니라 사람이다 (2026-09-11).** `MANUAL_LOGIN_TIMEOUT_S`가
  300초였고, 운영자가 보호조치 본인확인을 하는 도중에 그 타이머가 창을 닫았다
  (`bootstrap_manual`의 `finally: close()`). 지금은 1800초이고, 그보다 중요한 것은
  `wait_for_manual_login()`이 `page.is_closed()`를 함께 본다는 점이다 — 사람이 창을
  닫으면 즉시 포기로 끝난다. 그래서 타이머를 길게 잡아도 매달리지 않는다. 60초마다
  남은 시간을 `notify`로 알린다(말없이 멈춘 창은 죽은 창과 구별되지 않는다).
- **Login-failure text.** The plain login form always carries a "일회용 번호 로그인" link, so
  that phrase must never be used as a two-factor hint — it makes every failure look like 2FA.
- **공감 실패는 단계까지 남는다.** `TIMEOUT` 하나가 페이지 로딩 · 버튼 탐색 · 버튼 상태
  읽기 · 스크롤 · 클릭 · 클릭 후 `on` 확인 여섯 군데에서 나온다. 어디였는지 모르면 대응이
  정반대다 — 로딩이면 타임아웃 문제이고, 클릭 후 확인이면 네이버가 공감을 받지 않는다는
  뜻이라 멈추는 것이 맞다. `LikeResult(outcome, detail)`이 단계를 싣고 화면과 실행
  로그(JSONL)까지 간다. `detail`에 예외 *메시지*는 절대 싣지 않는다 (`stage_detail`) —
  storage_state가 예외에 실린 적이 있고 이 값은 파일로 남는다.
  측정 2026-09-07 (비로그인 21건): 클릭 가능 16 · 클릭 타임아웃 0 · NO_BUTTON 5.
  본문 버튼은 y=7,000~22,000px 아래에 있지만 `scroll_into_view_if_needed` 후 안정되고,
  폴백 셀렉터는 플로팅 버튼을 잡지 않는다 — 즉 클릭 단계는 용의자가 아니다.
- **연속 실패 상한은 1000이다 — 운영자 결정 (2026-09-07).** 기본값 5를 올려 달라는 요청이었고,
  사실상 이 신호 하나로는 멈추지 않는다. 남은 안전장치는 셋: 최근 20건 성공률 30% 미만,
  네이버의 직접 차단 신호(BLOCKED), 로그인 풀림(러너가 즉시 끊는다). 계속 실패하는 실행은
  5회가 아니라 20회 남짓에서 성공률 창에 걸린다. 계정 안전은 여전히 1급 요구사항이므로
  이 값을 올렸다는 이유로 속도 제한까지 함께 풀지 말 것.
- **중단은 터진 키워드를 함께 알린다.** `Aborted(reason, keyword)`. 실행은 계정 단위로
  하나라(결정 4) 어디서 터지든 전부 멈추는 것은 맞지만, 빨간 배너를 4개 패널 전부에
  띄우면 원인이 어디였는지가 사라지고 참여하지도 않은 빈 패널까지 중단으로 보인다.
  배너는 원인 패널에만, 나머지 참여 패널은 상태 줄로.
- **공감 button.** `a.u_likeit_button._face` inside `frame_locator("#mainFrame")`. A post
  renders **two** of them: a floating one that trails the scroll and sits permanently just
  below the fold (never clickable — click() times out with "element is outside of the
  viewport"), and the in-post one under `#area_sympathy{logNo}`. Always scope to the post
  number, and `scroll_into_view_if_needed()` before clicking.

- **공감 위젯은 지연 초기화된다 — 상태를 읽기 전에 반드시 기다릴 것 (실측 2026-09-10).**
  정적 마크업에는 언제나 `off` · `aria-pressed="false"` · 카운트 0인 **껍데기**가 들어
  있다. 스크롤해서 화면에 들어온 뒤에야 스크립트가 붙어 `.u_likeit_list_module`에
  `data-loaded="1"`이 생기고 서버의 진짜 상태가 채워진다 (스크롤 후 약 1.5초). 그전에
  읽으면 이미 공감한 글도 "안 눌림"으로 보이고, 그전에 클릭하면 `onclick="return false"`인
  맨 `<a>`를 누르는 셈이라 아무 일도 일어나지 않는다.

- **`on`/`off` class 토큰은 상태가 아니다 — `aria-pressed`를 읽을 것.** 이 버튼은
  `aria-haspopup="true"`인 리액션 레이어(`ul.u_likeit_layer._faceLayer`) 열기 버튼이라,
  레이어가 열리거나 아이콘 애니메이션이 도는 동안에도 class에 `on`이 붙는다. 2026-09-10
  실행(run-20260910-195029)에서 **거부된 공감 21건이 전부 그 `on`을 보고 SUCCESS로
  기록됐다.** 실제로는 0건이 눌렸다.

- **공감 성공 판정의 권위는 API 응답에 있다.** 클릭은 이 요청을 만든다:
  `https://apis.naver.com/blogserver/like/v1/services/BLOG/contents/{blogId}_{logNo}?suppress_response_codes=true&_method=POST&pool=blogid&callback=…`
  `suppress_response_codes=true` 때문에 **HTTP 상태는 실패해도 200**이고, 진짜 결과는
  JSONP 본문의 `statusCode`다. 로그아웃 상태의 답:
  `{"statusCode":401,"errorCode":4010,"message":"로그인 하신 후 이용해 주시기 바랍니다."}`
  `engine/like.py`가 클릭 전에 응답을 붙잡아 401 → `NOT_LOGGED_IN`, 403/429 → `BLOCKED`으로
  옮긴다. 성공 응답의 형태는 **일부러 가정하지 않는다** — 거부가 없으면 그때 DOM
  (`aria-pressed="true"`)으로 확인한다. 성공까지 본문 모양으로 판정하려 들면 그 추측이
  빗나가는 날 다시 거짓 성공이 된다.
  참고: `_face` 클릭 하나로 공감 POST가 나간다. 레이어의 항목을 따로 누를 필요는 없다.

- **401은 두 가지 뜻이다 — 구분하지 않으면 헛수고를 시킨다 (2026-09-12).** 공감 API가
  401을 돌려주면 예전에는 무조건 `NOT_LOGGED_IN`이었고 화면에 "로그인 풀림 — 중단"이
  떴다. 그런데 그 안내가 맞는 경우는 절반뿐이다: **우리 세션이 죽었으면** 다시
  로그인하면 되지만, **세션은 멀쩡한데 공감만 거부됐으면** 다시 로그인해도 아무 소용이
  없다(계정 쪽 제한이라 사람이 네이버에서 풀어야 한다). 구분하지 않으면 운영자가 효과
  없는 재로그인을 반복하게 된다. 지금은 401을 받으면 `session_still_alive(page)`가
  `_is_logged_in()`과 **같은 신호**로 되물어 본 뒤 `classify_rejection()`이 가른다:
  죽었으면 `NOT_LOGGED_IN` + "세션 만료 — 다시 로그인", 살아 있으면 `BLOCKED` +
  "세션은 살아 있음 — 계정 제한 의심". 둘 다 실행을 멈추는 것은 같고, 달라지는 것은
  운영자가 다음에 할 일이다. 확신이 없을 때(프로브 타임아웃)는 "살아 있다"로 친다 —
  멀쩡한 세션을 죽었다고 단정해 재로그인을 시키는 것이 바로 보호조치를 부르는 행동이다.
- **로그인 판정은 네이버에게 묻는다 (2026-09-10 수정).** 예전 `_is_logged_in()`은
  `NID_AUT` 쿠키가 있으면 로그인으로 봤다. 그런데 그 쿠키는 **우리가 저장된 세션에서
  매번 다시 주입하는 값**이라, 네이버가 서버에서 세션을 만료시켜도 로컬에는 그대로
  남는다 — 이 검사는 구조적으로 만료를 탐지할 수 없었다. 결과는 두 겹의 사고였다:
  로그아웃 상태로 실행이 끝까지 돌았고(공감 21건 전부 401), 앱도 `tools/login.py`도
  "이미 로그인되어 있습니다"라고 답해 **재로그인 자체가 막혔다**. 레거시 결함 3이
  다른 얼굴로 돌아온 것이다. 지금은:
  1. 쿠키가 아예 없으면 로그인 페이지를 볼 것도 없이 로그아웃. 그 페이지는 가장
     방어가 심한 화면이므로(결정 3) 갈 이유가 없으면 가지 않는다.
  2. 있으면 로그인 페이지를 열어 본다 — 세션이 살아 있으면 네이버가 `url`
     파라미터로 우리를 돌려보낸다. `_login()`이 로그인 성공을 판정할 때 쓰는 것과
     **같은 신호**다. 폼은 건드리지 않는다.
  3. 로그아웃으로 판정되면 `_discard_stale_session()`이 쿠키를 비운다. 남겨 두면
     `wait_for_manual_login()`이 그 낡은 NID_AUT를 보고 사람이 손대기도 전에
     "완료"로 판정한다.

## Testing

```
python -m pytest -q                              # 층 1 — unit, no network, no browser
python -m pytest -q -m contract                  # 층 2 — live Naver, not logged in, safe
python -m pytest -q -m browser                   # 층 3 — needs `playwright install chromium`
python tools/dryrun.py <ID> "<키워드>" --blogs 5   # 층 4 — logs in, finds the button, never clicks
```

Layers 2 and 3 are the early-warning system for Naver's markup changes; run them before
blaming the code. Start any real run with 드라이런 (the UI checkbox, or the tool), then a
small live run (방문 상한 3, 블로그당 공감 1) before anything larger.

## Packaging (Windows exe)

전체 절차와 백신 오탐 대응은 `docs/PACKAGING.md`. 아래는 요약이다.

```
python -m pip install -e ".[build]"
python -m PyInstaller --noconfirm packaging/like-bot-v2.spec
```

**버전은 `engine/version.py`의 `__version__` 한 줄에서만 온다** — 창 제목, exe 속성창의
FileVersion/ProductVersion(스펙이 `VSVersionInfo`로 박는다), `pyproject.toml`의 dynamic
version이 전부 그 값을 읽는다. 배포본은 폴더째 압축돼 돌아다니고 압축 파일 이름은 쉽게
바뀌므로, 받는 사람이 버전을 확인할 수 있는 곳은 사실상 창 제목과 파일 속성뿐이다.
압축 파일 이름에만 버전을 붙이고(`like-bot-v2-2.0.1.zip`) 폴더·exe 이름은 그대로 둔다 —
백신 제외 경로와 바로가기가 버전마다 깨지면 안 된다.

Output is `dist/like-bot-v2/` (약 645MB, zip 280MB) — **onedir, not onefile**. Zip the
folder to distribute it. onefile would unpack the playwright driver, the Qt plugins and
chromium to a temp folder on every launch: slow to start and a frequent antivirus false
positive. UPX is off for the same reason.

**chromium ships inside the build.** The spec copies the newest `chromium-*` and
`winldd-*` out of this machine's playwright cache, so the receiving PC needs no python, no
Chrome and no download. `chromium_headless_shell-*` is deliberately left out: it is 272MB
and useless here — `engine/session.py` always launches `headless=False`, because the login
challenge needs a window a person can see.

Where the browser is found, in order (`engine/browsers.py`):

1. `PLAYWRIGHT_BROWSERS_PATH`, if the operator set it. Never overridden — otherwise there
   is no way to point the app elsewhere and diagnosis is stuck.
2. `sys._MEIPASS/ms-playwright`, when frozen **and** it actually holds a `chromium-*`
   folder. An empty folder counts as absent: PyInstaller drops empty directories, so a
   build made on a machine with no cached chromium ships without one.
3. `%LOCALAPPDATA%\like-bot-v2\browsers` — the download target for that case.

A dev run is left alone entirely and keeps using the machine's own ms-playwright cache;
mixing the two is how "내 PC에선 되는데" happens.

`_run_engine()` still calls `ensure_chromium()` before `session.open()`, which downloads
only when step 3 applies. Its output is streamed to the log window **unparsed** — progress
formats are someone else's markup (legacy defect 2).

`console=False`, so an uncaught exception has nowhere to print. `bootstrap()` installs a
`sys.excepthook` that appends to `%LOCALAPPDATA%\like-bot-v2\logs\crash.log` and shows a
dialog. That file holds tracebacks — if an exception ever carries storage_state again
(it did once), the session leaks into it and the security rules below apply.

The same folder holds one `run-{시각}-{run_id}.jsonl` per run (`engine/runlog.py`), written
by a tee around the desktop's `emit` callback — the engine still does not know who receives
its events. The log opens **before** chromium and login, since a run that stalls at the
challenge screen is exactly the one worth reading afterwards; `RunLog` swallows its own write
failures, because losing a log must never cost a 공감. The newest 30 runs are kept and
`crash.log` is never touched. The 📁 로그 폴더 button opens the folder. `LogLine.text` can
carry exception messages, so these files inherit crash.log's risk and its security rules.

Two hooks the build needs and PyInstaller cannot infer (verified absent from
pyinstaller-hooks-contrib 6.22): `collect_all("playwright")` for the driver, and
`collect_entry_point("keyring.backends")` + `keyring.backends.Windows` — keyring resolves
backends lazily, so without it the exe fails at password lookup, not at import.

## Store 파이썬은 로그·세션을 다른 곳에 쓴다 (2026-09-12)

`python -m desktop.app`을 **Microsoft Store 파이썬**
(`%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe`)으로 돌리면 Windows가
`%LOCALAPPDATA%` **쓰기**를 아래로 리디렉션한다:

```
%LOCALAPPDATA%\Packages\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\LocalCache\Local\like-bot-v2```

읽기는 실제 경로로 흘러내려가므로 세션은 멀쩡히 읽히는데 **로그만 저 아래에
쌓인다.** 실행이 분명히 있었는데 `%LOCALAPPDATA%\like-bot-v2\logs\`에는 아무것도
없어서 진단이 한 바퀴 돈 적이 있다. 그래서 `_run_engine()`이 실행 시작에
`실행 기록: {경로}` 한 줄을 찍는다 — 로그를 달라고 할 때는 그 줄을 보면 된다.
배포본(PyInstaller exe)에는 이 문제가 없다.

## Security rules

- **Credentials never enter the repo.** `.env`, `accounts.csv`, `session.dat`,
  `storage_state.json`, `data/` are gitignored. Check `git status` before every commit.
- **The saved session is a credential.** `%LOCALAPPDATA%\like-bot-v2\sessions\{account}.dat`
  holds NID_AUT / NID_SES encrypted with DPAPI. Whoever holds those cookies is logged in
  without needing the password. Never print, log, or paste storage_state — a bug that handed
  it to Playwright as a string once dumped the whole session into an exception message.
- **If session cookies are ever exposed** (a traceback, a screenshot, a pasted log), treat it
  as a credential leak and revoke them:
  1. 네이버 내정보 → 로그인 기록 / 기기 관리에서 로그아웃 (or change the password). This is
     the only step that actually invalidates the cookie — Naver holds the session, not us.
  2. Delete the stale `sessions/{account}.dat`.
  3. Re-run `python tools/login.py <네이버ID>`.
  Deleting the local file alone invalidates nothing. Verify rather than assume: load the
  stored session headless and check the NID cookies — a revoked session comes back with
  only `NID_JST`, since Naver clears `NID_AUT`/`NID_SES` itself. Done once on 2026-09-01;
  a plain browser logout did revoke the stored session too.

## Conventions

- Respond to the user in Korean (한글).
- Domain vocabulary stays Korean in user-facing strings and logs: 공감/좋아요, 이웃, 키워드,
  기간, 답방.
