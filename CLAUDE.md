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

Logging in is done from the app: type 아이디/비밀번호 once and press 실행. If Naver puts up
its 추가 확인 screen, the browser window stays open and waits for you — see "Live-site facts".
`tools/login.py <네이버ID>` does the same thing from the CLI.

```
engine/    search · posts(RSS) · session · like · runner · ratelimit · history · safety
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
- **Automated credential entry triggers a challenge.** Typing ID/PW with Playwright lands on
  "보안을 위해 추가 확인" (an image challenge). Do not try to defeat it — that is
  bot-detection evasion and it risks the account. Hand it to the operator instead:
  `_login()` classifies why it stalled, reports that through `on_challenge`, and waits on
  the open window (`MANUAL_LOGIN_TIMEOUT_S`, 300s) for a person to finish. Only a timeout
  raises. So login has three shapes, and the desktop UI covers all of them:
  | 상황 | 동작 |
  | --- | --- |
  | 세션 유효 | 로그인 페이지를 아예 거치지 않는다 (결정 3) |
  | 세션 없음 + 비밀번호 있음 | 자동 입력 → 막히면 창을 열어둔 채 사람을 기다린다 |
  | 세션 없음 + 비밀번호 없음 | 자동 입력을 건너뛰고 창만 열어 준다 |

  The UI's 비밀번호 field feeds `keyring`, never the repo. A missing password is not an
  error — it just means the third row. `tools/login.py` is the CLI form of that third row,
  still useful for bootstrapping without opening the app.
- **Login-failure text.** The plain login form always carries a "일회용 번호 로그인" link, so
  that phrase must never be used as a two-factor hint — it makes every failure look like 2FA.
- **공감 button.** `a.u_likeit_button._face` inside `frame_locator("#mainFrame")`; the `on` /
  `off` class tokens still carry the state. A post renders **two** of them: a floating one
  that trails the scroll and sits permanently just below the fold (never clickable — click()
  times out with "element is outside of the viewport"), and the in-post one under
  `#area_sympathy{logNo}`. Always scope to the post number, and
  `scroll_into_view_if_needed()` before clicking.

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

```
python -m pip install -e ".[build]"
python -m PyInstaller --noconfirm packaging/like-bot-v2.spec
```

Output is `dist/like-bot-v2/` (약 205MB) — **onedir, not onefile**. Zip the folder to
distribute it. onefile would unpack the playwright driver and the Qt plugins to a temp
folder on every launch: slow to start and a frequent antivirus false positive. UPX is off
for the same reason.

**chromium is not bundled** (약 450MB on top of the 205MB). The build carries only the
playwright driver (`node.exe` + `cli.js`), and the frozen app downloads a browser on its
first real run:

- `bootstrap()` (desktop/app.py) sets `PLAYWRIGHT_BROWSERS_PATH` to
  `%LOCALAPPDATA%\like-bot-v2\browsers` — **only when frozen**, and only if the operator
  has not set it themselves. A dev run keeps using the machine's own ms-playwright cache;
  mixing the two is how "내 PC에선 되는데" happens.
- `_run_engine()` calls `ensure_chromium()` before `session.open()`, because a missing
  browser otherwise surfaces as playwright's "Executable doesn't exist" mid-login. The
  installer's output is streamed to the log window **unparsed** — progress formats are
  someone else's markup (legacy defect 2).
- `chromium_present()` ignores `chromium_headless_shell-*`: login needs a window a person
  can see, so the headless shell alone is worthless here.

`console=False`, so an uncaught exception has nowhere to print. `bootstrap()` installs a
`sys.excepthook` that appends to `%LOCALAPPDATA%\like-bot-v2\logs\crash.log` and shows a
dialog. That file holds tracebacks — if an exception ever carries storage_state again
(it did once), the session leaks into it and the security rules below apply.

Two hooks the build needs and PyInstaller cannot infer (verified absent from
pyinstaller-hooks-contrib 6.22): `collect_all("playwright")` for the driver, and
`collect_entry_point("keyring.backends")` + `keyring.backends.Windows` — keyring resolves
backends lazily, so without it the exe fails at password lookup, not at import.

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
