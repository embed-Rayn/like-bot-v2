# 배포본 만들기 (Windows exe)

받는 사람 PC에 파이썬도, 크롬도, 인터넷 연결도 필요 없게 만드는 것이 목표다.
`packaging/like-bot-v2.spec` 하나가 그 일을 하고, 이 문서는 그 스펙을 **왜**
그렇게 썼는지와 빌드가 어긋났을 때 무엇을 봐야 하는지를 적는다.

## 1. 빌드하는 법

```powershell
# 1) 빌드 의존성 (pyinstaller 포함)
python -m pip install -e ".[build]"

# 2) chromium을 이 PC 캐시에 받아 둔다 — 이게 그대로 배포본에 실린다
python -m playwright install chromium

# 3) 빌드
python -m PyInstaller --noconfirm packaging/like-bot-v2.spec
```

결과는 `dist/like-bot-v2/` (약 645MB, zip 280MB). **폴더째** 압축해서 준다.

빌드 전에 확인할 것:

- **테스트 3층이 다 통과하는가.** 배포본에서 처음 드러나는 실패는 진단 비용이
  몇 배다.
  ```powershell
  python -m pytest -q                 # 단위
  python -m pytest -q -m contract     # 실제 네이버 (비로그인, 안전)
  python -m pytest -q -m browser      # chromium 필요
  ```
- **`git status`가 깨끗한가.** `.env`, `accounts.csv`, `session.dat`,
  `storage_state.json`, `data/`는 절대 빌드에도 저장소에도 들어가면 안 된다
  (보안 규칙).

## 2. onedir이다 — onefile로 바꾸지 말 것

`EXE(exclude_binaries=True)` + `COLLECT`, 즉 폴더 배포다. onefile로 묶으면
실행할 때마다 playwright 드라이버(node.exe + cli.js), Qt 플러그인, chromium을
임시 폴더에 통째로 풀어야 한다. 기동이 눈에 띄게 느려지고, "매번 수백 MB를
임시 폴더에 쓰고 exe를 실행하는" 동작은 백신이 가장 좋아하는 오탐 패턴이다.

`upx=False`도 같은 이유다. **크기를 줄이려고 UPX를 켜지 말 것** — 압축된
실행 파일은 오탐률이 확 오른다. 줄여야 한다면 zip으로 줄인다.

## 3. chromium을 함께 싣는다

스펙의 `_browsers_to_ship()`이 이 PC의 playwright 캐시에서 가장 최신
`chromium-*`과 `winldd-*`를 통째로 복사해 `ms-playwright/` 아래에 넣는다.

`chromium_headless_shell-*`은 **일부러 뺀다.** 272MB인데 이 앱에는 쓸모가 없다 —
`engine/session.py`는 언제나 `headless=False`로 띄운다. 로그인 추가 확인(캡차)은
사람이 창을 봐야 끝낼 수 있기 때문이다.

배포본이 브라우저를 찾는 순서는 `engine/browsers.py`가 정한다:

| 순서 | 위치 | 언제 |
| --- | --- | --- |
| 1 | `PLAYWRIGHT_BROWSERS_PATH` | 운영자가 직접 지정했을 때. **절대 덮어쓰지 않는다** — 덮어쓰면 다른 곳을 가리킬 방법이 없어져 진단이 막힌다 |
| 2 | `sys._MEIPASS/ms-playwright` | 얼려서 빌드했고 **실제로 `chromium-*`이 들어 있을 때** |
| 3 | `%LOCALAPPDATA%\like-bot-v2\browsers` | 위 둘이 없을 때의 내려받기 대상 |

2번의 "실제로 들어 있을 때"가 중요하다. **빈 폴더는 없는 것으로 친다** —
PyInstaller는 빈 디렉터리를 그냥 버리므로, chromium 캐시가 없는 PC에서 빌드하면
브라우저 없는 배포본이 나온다. 그 경우 `_run_engine()`의 `ensure_chromium()`이
첫 실행 때 3번 위치로 내려받고, 그 진행 출력은 로그 창에 **가공하지 않고**
그대로 흘린다 (진행률 형식은 남의 마크업이다 — 레거시 결함 2).

개발 실행(얼리지 않은 실행)은 건드리지 않고 이 PC의 `ms-playwright` 캐시를
그대로 쓴다. 둘을 섞는 것이 "내 PC에선 되는데"가 생기는 경로다.

## 4. PyInstaller가 스스로 못 찾는 것 두 가지

둘 다 `pyinstaller-hooks-contrib` 6.22 기준으로 훅이 **없다**는 것을 확인했다.

1. **playwright 드라이버** — `collect_all("playwright")`.
   playwright는 순수 파이썬 패키지가 아니다. `driver/` 아래 `node.exe`와
   `cli.js`가 있고, 빠지면 배포본이 `Executable doesn't exist`로 죽는다.
2. **keyring 백엔드** — `collect_entry_point("keyring.backends")` +
   hiddenimport `keyring.backends.Windows`.
   keyring은 백엔드를 진입점으로 **늦게** 찾는다. 그래서 빠져도 import는
   멀쩡히 되고, 비밀번호를 조회하는 순간에 `No recommended backend`로 죽는다.
   증상이 원인에서 멀리 떨어져 나타나는 종류다.

`win32timezone`도 hiddenimport에 있다 — pywin32가 런타임에만 import 한다.

## 5. 콘솔이 없다 — 그래서 남기는 두 가지

`console=False`로 빌드하므로 잡히지 않은 예외는 인쇄될 곳이 없다.
`%LOCALAPPDATA%\like-bot-v2\logs\`에 두 종류가 쌓인다.

- **`crash.log`** — `bootstrap()`이 설치한 `sys.excepthook`이 트레이스백을
  덧붙이고 대화상자를 띄운다. 지워지지 않는다.
- **`run-{시각}-{run_id}.jsonl`** — 실행 하나당 한 파일, 한 줄에 이벤트 하나
  (`engine/runlog.py`). 브라우저와 로그인 **전에** 열린다. 로그인 확인 화면에서
  멈춘 실행이야말로 나중에 읽을 이유가 크기 때문이다. 최근 30개만 남는다.

앱의 **📁 로그 폴더** 버튼이 이 폴더를 연다. 문제 리포트를 받을 때는 이
`.jsonl` 하나를 받는 것이 스크린샷 열 장보다 낫다.

> **보안:** `LogLine.text`에는 예외 메시지가 실릴 수 있다. storage_state가
> 예외에 실린 버그가 실제로 한 번 있었다. 이 파일들을 남에게 보내기 전에
> `NID_AUT` / `NID_SES`가 들어 있지 않은지 확인하고, 들어 있었다면 자격 증명
> 유출로 취급해 CLAUDE.md의 폐기 절차를 밟을 것.

## 6. 백신이 배포본을 지우는 경우

**증상:** 실행 중에 앱이 갑자기 사라지고, `dist/like-bot-v2/` 안의 파일이나
`like-bot-v2.exe` 자체가 없어져 있다. 대화상자도 `crash.log`도 안 남는다.

이건 앱의 버그가 아니다. **코드에는 자기 파일을 지우는 경로가 없다.** Windows
Defender(또는 사내 백신)가 실행 중인 파일을 격리한 것이다. 서명 없는
PyInstaller 배포본은 흔히 `Wacatac`/`Trojan:Script/*` 계열 휴리스틱에 걸린다.
`crash.log`가 비어 있는 것 자체가 단서다 — 프로세스가 예외로 죽은 것이 아니라
밖에서 없어진 것이다.

**확인 (지웠다는 사실을 먼저 증거로 잡는다):**

1. Windows 보안 → 바이러스 및 위협 방지 → **보호 기록**. 격리 항목에
   `like-bot-v2.exe` 또는 `dist\like-bot-v2\...` 경로가 있으면 확정이다.
2. PowerShell로도 볼 수 있다:
   ```powershell
   Get-MpThreatDetection | Select-Object -First 5 InitialDetectionTime, Resources
   ```

**대응:**

- 격리에서 **복원**하고, 배포본 폴더를 **제외 목록**에 넣는다:
  Windows 보안 → 바이러스 및 위협 방지 → 설정 관리 → 제외 항목 → 폴더 추가.
- 압축을 풀 위치는 `%USERPROFILE%\Downloads` 같은 곳보다 고정 폴더가 낫다.
  경로가 바뀔 때마다 제외 항목을 다시 넣어야 한다.
- 근본 해결은 **코드 서명 인증서**다. EV 인증서면 SmartScreen 평판도 바로
  붙는다. 그 전까지는 제외 항목이 현실적인 답이다.
- **UPX를 켜거나 onefile로 바꾸지 말 것.** 둘 다 오탐률을 올린다 (§2).

로그인 추가 확인(이미지·영수증 입력) 화면을 여러 번 반복한 뒤에 이 증상이
나오는 것은 우연이다 — 그 화면이 오래 떠 있는 동안 백신 검사 주기가 한 번
돌았을 뿐이다. 추가 확인 자체를 우회하려 들지 말 것. 그건 봇 탐지 회피이고
계정을 잃는 길이다 (CLAUDE.md).

## 7. 받는 사람에게 같이 알려 줄 것

- 폴더째 압축을 풀 것. exe 하나만 꺼내면 동작하지 않는다.
- 첫 실행에서 SmartScreen이 뜨면 **추가 정보 → 실행**.
- 아이디/비밀번호는 앱에 한 번 입력하면 Windows 자격 증명 관리자(keyring)에
  들어간다. 저장소나 배포본에는 들어가지 않는다.
- 처음에는 **드라이런** 체크 → 그다음 방문 상한 3, 블로그당 공감 1로 작게.
- 문제가 생기면 **📁 로그 폴더**의 가장 최근 `run-*.jsonl`을 보낼 것.
