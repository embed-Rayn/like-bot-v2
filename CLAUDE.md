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

The legacy implementation is present in `legacy/` and has been fully analyzed. **v2 has
not been written yet** — we are in the design phase. See "v2 Design Decisions" below for
what is settled and what is still open.

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
6. **Visit history is persisted**, so a cap of N means N *new* blogs every run.
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
Next step is an implementation plan; no v2 code is written yet.

## Conventions

- Respond to the user in Korean (한글).
- Domain vocabulary stays Korean in user-facing strings and logs: 공감/좋아요, 이웃, 키워드,
  기간, 답방.
