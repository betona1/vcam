vCAM(vcam) 개발 지침

> 정식 제품명: **vCAM**  
> 약칭·실행명·Python 패키지명: **vcam**  
> 목표 플랫폼: **Windows 10/11, x64**  
> 기본 언어: **한국어**  
> 기술 방향: **Python 3.12+ / PySide6 / Windows Graphics Capture 계열 / FFmpeg**  
> 조사 기준일: **2026-10-06 (Asia/Seoul)**

이 문서는 Claude Code가 저장소 안에서 독립적으로 판단하고 구현할 때 따라야 할 최상위 프로젝트 지침이다. 구현을 시작하기 전에 이 파일 전체를 읽고, 기존 코드·테스트·문서·사용자 변경 사항을 먼저 확인한다.

## 1. 제품 한 문장

vCAM(약칭 vcam)은 오캠처럼 바로 쓸 수 있고, 반디캠처럼 필요한 설정은 충분하며, OBS·ShareX·ScreenToGif의 좋은 작업 흐름을 가볍게 흡수한 **로컬 우선 Windows 화면 녹화·캡처 도구**다.

핵심 경험은 다음과 같다.

1. 앱을 열고 10초 안에 녹화를 시작할 수 있다.
2. 화면 전체, 모니터, 창, 영역 중 하나를 시각적으로 선택한다.
3. 시스템 소리와 마이크를 각각 켜고 음량을 확인한다.
4. 녹화 중 일시정지, 스크린샷, 간단한 표시 도구를 쓴다.
5. 녹화 종료 후 미리보기와 앞뒤 자르기를 거쳐 안전하게 저장한다.
6. 모든 캡처와 처리는 기본적으로 사용자 PC 안에서 끝난다.

## 2. 절대 원칙

- 반디캠, 오캠, OBS, ShareX, ScreenToGif 또는 Windows 캡처 도구의 상표, 로고, 아이콘, 문구, 색 배치, 화면 배치를 복제하지 않는다.
- 기능 아이디어와 일반적인 UX 패턴만 참고하고, vCAM 고유의 이름·아이콘·색·컴포넌트 구조를 사용한다.
- 기본값은 초보자에게 안전해야 한다. 고급 옵션은 `고급 설정` 안에 점진적으로 공개한다.
- GUI 스레드에서 캡처, 인코딩, 파일 복사, 썸네일 생성, 장치 검색을 수행하지 않는다.
- 녹화 중 예외가 발생해도 가능한 한 이미 기록된 미디어를 복구할 수 있어야 한다.
- 사용자의 화면, 음성, 파일명, OCR 결과를 명시적 동의 없이 네트워크로 전송하지 않는다.
- 녹화 중임을 숨기는 스텔스 기능, 보안 UI 우회, DRM 우회, 권한 상승을 구현하지 않는다.
- 타인의 대화나 회의를 녹화할 때 동의가 필요할 수 있음을 첫 실행과 도움말에서 알린다.
- 작동하지 않는 버튼, 빈 설정 화면, 성공한 것처럼 보이는 모의 녹화는 완료로 간주하지 않는다.
- 라이선스가 확인되지 않은 코드나 에셋을 복사하지 않는다. 참고한 코드를 실제로 가져오면 파일 헤더와 `THIRD_PARTY_NOTICES.md`에 출처·라이선스를 기록한다.

## 3. 조사에서 채택한 장점

### 반디캠에서 배울 점

- 화면/영역/특정 창/게임/장치/오디오 전용처럼 목적을 명확히 나눈 녹화 모드
- 시스템 소리와 마이크 상태를 메인 창에서 즉시 확인하는 방식
- 웹캠 오버레이, 커서·클릭 효과, 키 입력 표시, 실시간 그리기
- 예약 녹화와 자동 종료
- 녹화 파일을 영상·이미지·오디오로 나눠 보여주는 최근 결과 라이브러리
- 준비/녹화/일시정지를 트레이 아이콘 색으로 구분하는 상태 피드백

### 오캠에서 배울 점

- `녹화`, `캡처`, `영역`, `열기`, `코덱`, `소리`처럼 행동 중심의 큰 버튼
- 화면/게임/오디오 탭을 빠르게 바꾸는 단순한 진입 구조
- 드래그 핸들과 중앙 이동 핸들이 있는 눈에 보이는 영역 선택 프레임
- 해상도 프리셋, 사용자 지정 크기, 전체 화면, 창 찾기를 한 메뉴에서 제공
- 코덱과 시스템 소리/마이크 선택을 깊은 설정에 숨기지 않는 방식

### OBS Studio에서 배울 점

- 화면, 창, 웹캠, 이미지, 텍스트를 독립적인 `소스`로 다루는 확장 가능한 내부 모델
- 소스별 오디오 미터와 음소거, 게인, 노이즈 억제 구조
- 하드웨어에 맞는 자동 설정과 하드웨어 인코더 선택
- 거의 모든 동작에 지정 가능한 단축키
- 리플레이 버퍼와 녹화 전 미리보기 개념

vcam MVP에서 OBS 전체 장면 편집기를 만들지는 않는다. 내부 데이터 모델만 소스 확장에 견디도록 설계하고, 사용자에게는 `화면 + 웹캠 + 오디오`의 간단한 조합으로 보여준다.

### ShareX에서 배울 점

- 전체 화면, 창, 영역, 마지막 영역을 단축키 한 번으로 다시 캡처하는 흐름
- 캡처 후 `저장 → 클립보드 복사 → 편집 → 폴더 열기`를 조합하는 후처리 작업
- 화살표, 도형, 텍스트, 순서 번호, 강조, 블러/픽셀화 같은 주석 도구
- 스크롤 캡처, OCR, 화면 색상 추출 같은 생산성 기능
- 성공/부분 성공/실패를 구분하는 명확한 결과 상태

업로드는 기본 후처리 작업에 넣지 않는다. 향후 추가하더라도 서비스별 명시적 연결과 최종 확인이 있어야 한다.

### ScreenToGif에서 배울 점

- 녹화가 끝나면 바로 편집기로 넘어가는 흐름
- 프레임 또는 구간 삭제, 재생 속도 조정, 앞뒤 자르기
- GIF/APNG/MP4/WebM 등 목적 중심의 내보내기
- 프로젝트를 저장했다가 다시 편집하는 기능

MVP 편집기는 영상 편집기가 아니라 `미리보기 + 앞뒤 자르기 + 음량 + 내보내기`에 집중한다. 프레임 단위 GIF 편집은 후속 단계다.

### Windows 캡처 도구에서 배울 점

- 앱을 먼저 열지 않아도 전역 단축키로 바로 진입하는 짧은 흐름
- 캡처 유형을 먼저 고르고 영역을 지정한 다음 시작하는 예측 가능한 순서
- 캡처 직후 미리보고 저장하거나 다른 편집기로 넘기는 구조
- 한 화면에서 시스템 오디오와 마이크를 켜고 끄는 방식

### GitHub 공개 구현에서 배울 점

- `sabbour/followcursor`의 WGC/GDI 폴백, FFmpeg 파이프, 공통 시작 시각, 작업 스레드 분리, 하드웨어 인코더 자동 폴백은 구조적 참고 가치가 높다.
- `ra1nty/DXcam`은 Windows의 DXGI/WinRT 기반 고성능 캡처와 프레임 타임스탬프를 제공한다.
- `BoboTiG/python-mss`는 단순하고 안정적인 GDI 기반 폴백으로 적합하다.
- `bastibe/SoundCard`는 Windows WASAPI와 루프백 녹음을 지원하므로 시스템 오디오 캡처 후보로 적합하다.
- `PyAV-Org/PyAV`는 강력하지만 직접 미디어 API를 다루는 복잡도가 높고, 배포 바이너리에 포함된 FFmpeg의 라이선스 구성이 별도 쟁점이다. MVP 기본 의존성으로 사용하지 않는다.

공개 저장소의 코드를 통째로 가져오지 말고, 먼저 공식 API와 독립적인 어댑터를 작성한다. 실제 코드 차용이 더 안전하다고 판단될 때만 라이선스와 저작권 고지를 보존한다.

## 4. 사용자와 제품 범위

### 핵심 사용자

- 온라인 강의, 업무 설명, 버그 재현 영상을 만드는 일반 사용자
- 마이크와 PC 소리를 함께 녹음하려는 교사·직장인·콘텐츠 제작자
- OBS는 너무 복잡하고 Windows 캡처 도구는 부족하다고 느끼는 사용자

### P0: 첫 실행 가능한 MVP

- 전체 화면, 단일 모니터, 선택 영역 녹화
- 사각 영역 선택 오버레이와 좌표/크기 표시
- 시스템 오디오, 마이크 각각 켜기/끄기와 장치 선택
- 3초 카운트다운
- 녹화 시작/일시정지/재개/종료
- 전역 단축키: 시작/종료, 일시정지, 스크린샷
- MP4 출력과 PNG 스크린샷
- 저장 폴더 선택, 파일명 템플릿, 자동 충돌 회피
- 녹화 시간, 파일 크기, FPS, 오디오 레벨 표시
- 최근 녹화 목록, 재생, 폴더 열기
- 오류 메시지와 로그 파일
- 높은 DPI와 멀티 모니터 지원
- 최소 단위 테스트와 실제 3초 녹화 스모크 테스트

### P1: 제품다운 첫 릴리스

- 특정 창 녹화 및 창 목록 썸네일
- 웹캠 PIP: 위치, 크기, 원/둥근 사각형, 좌우 반전
- 커서 포함/제외, 클릭 파동, 커서 하이라이트
- 키 입력 표시: 암호 입력란과 민감한 조합은 표시하지 않는 안전 필터 포함
- 녹화 중 스크린샷
- 간단한 그리기: 펜, 화살표, 사각형, 하이라이트, 전체 지우기
- 녹화 후 앞뒤 자르기와 무손실 가능 구간 내보내기
- GIF 내보내기 프리셋
- 품질 프리셋: `작은 용량`, `균형`, `고화질`, `사용자 지정`
- NVENC → QuickSync → AMF → libx264 순서의 감지와 안전한 폴백
- 예약 녹화: 1회/매일/매주, 시작·종료 시각, 완료 후 동작
- 트레이 최소화와 상태별 아이콘
- 비정상 종료 후 미완료 녹화 복구 안내

### P2: 차별화 기능

- 마지막 15~120초를 저장하는 리플레이 버퍼
- 마우스 정착·클릭 구간을 이용한 로컬 스마트 줌
- 간단한 타임라인에서 줌 구간 추가/삭제/이동
- 스크롤 스크린샷
- 로컬 Windows OCR과 민감 정보 블러
- 후처리 파이프라인: 저장, 클립보드 복사, 편집기 열기, 폴더 열기
- 화면+창+웹캠+텍스트를 조합하는 라이트 소스 편집기
- 프로젝트 파일 저장/불러오기
- 녹화 프로필 가져오기/내보내기

### 명시적 비범위

- P0/P1에서 라이브 스트리밍, 플러그인 마켓, 다중 장면 방송 전환은 만들지 않는다.
- P0에서 AI 전사, 클라우드 업로드, 계정 시스템은 만들지 않는다.
- 게임 안티치트 우회, 보호된 비디오 캡처 우회, 숨김 녹화를 지원하지 않는다.
- 전체 비선형 영상 편집기를 만들지 않는다.

## 5. UX와 UI 명세

### 디자인 원리

- **즉시성:** 기본 프로필로 바로 녹화할 수 있어야 한다.
- **점진적 공개:** 메인 화면에는 자주 쓰는 항목만, 코덱·비트레이트·색 형식은 고급 설정에 둔다.
- **상태 가시성:** 준비, 카운트다운, 녹화, 일시정지, 마무리, 오류 상태가 색뿐 아니라 아이콘과 텍스트로도 구분되어야 한다.
- **파괴적 행동 보호:** 파일 삭제는 휴지통을 사용하고 확인 대화상자를 제공한다.
- **키보드 접근성:** 모든 핵심 기능은 Tab 탐색, 스페이스/엔터, 단축키로 사용할 수 있어야 한다.
- **원본성:** 경쟁 앱과 같은 빨강 녹화 원형은 일반 관습으로 사용할 수 있지만, 전체 색상·아이콘·배치 조합은 고유하게 만든다.

### 메인 화면 와이어프레임

```text
┌──────────────────────────────────────────────────────────────┐
│ vcam       [화면] [창] [영역] [오디오]       [?] [설정]     │
├───────────────────────────────┬──────────────────────────────┤
│                               │ 녹화 준비                    │
│       선택 대상 미리보기      │ 대상  모니터 1               │
│                               │ 크기  1920 × 1080            │
│                               │                              │
│                               │ 시스템 소리  [■■■□□]  [ON] │
│                               │ 마이크       [■■□□□]  [ON] │
│                               │ 웹캠                    [OFF]│
│                               │                              │
│                               │ 품질  [균형 ▾]               │
│                               │ 저장  C:\...\Videos  [열기] │
├───────────────────────────────┴──────────────────────────────┤
│ 최근 녹화  썸네일 · 이름 · 길이 · 해상도 · 크기 · 작업     │
├──────────────────────────────────────────────────────────────┤
│ [영역 다시 선택]       F9 시작       [ ● 녹화 시작 ]         │
└──────────────────────────────────────────────────────────────┘
```

### 녹화 중 미니 컨트롤

- 항상 위에 표시되는 작은 수평 바를 제공한다.
- 구성: 상태 점, 경과 시간, 일시정지/재개, 스크린샷, 그리기, 마이크, 종료.
- 녹화 대상 영역 밖에 우선 배치하고, 사용자가 접거나 트레이로 숨길 수 있다.
- 미니 컨트롤 자체는 캡처 결과에 포함하지 않는 경로를 우선 구현한다.
- 숨겼을 때도 트레이 아이콘과 단축키로 상태를 확인하고 제어할 수 있어야 한다.

### 영역 선택 오버레이

- 모든 모니터에 반투명 딤을 표시하고 드래그한 영역만 밝게 보인다.
- 확대경, 물리 픽셀 기준 좌표와 `너비 × 높이`, 화면 경계 스냅을 제공한다.
- 모서리/변 핸들 8개, 중앙 이동 핸들, `Esc` 취소, `Enter` 확정.
- 마지막 영역과 16:9, 4:3, 1:1, 720p, 1080p 프리셋을 제공한다.
- 음수 좌표를 갖는 보조 모니터와 서로 다른 DPI 배율을 정확히 처리한다.

### 시각 시스템

- 기본 폰트: `Segoe UI Variable`, 폴백 `Segoe UI`.
- 간격: 4px 기반 토큰(`4, 8, 12, 16, 24, 32`).
- 둥근 모서리: 컨트롤 6px, 카드 10px.
- 기본 테마: 시스템 테마를 따르고 라이트/다크 수동 선택을 허용한다.
- 브랜드 강조색은 기존 경쟁 앱과 다른 청록/보라 계열을 사용하되 대비 기준 WCAG AA를 만족한다.
- 녹화 상태 색은 빨강 하나에 의존하지 않고 `REC` 텍스트와 움직이지 않는 명확한 아이콘을 함께 쓴다.
- 의미 없는 애니메이션을 피하고, 전환은 100~200ms 안에서 끝낸다.
- 아이콘은 한 세트의 오픈 라이선스 SVG 또는 직접 제작한 SVG만 사용한다.

## 6. 기술 스택 결정

### GUI 비협상 조건

- GUI 프레임워크는 **PySide6(Qt 6)** 로 고정한다.
- Tkinter, CustomTkinter, PyQt5/6, wxPython, Electron, 웹뷰 기반 UI로 대체하지 않는다.
- Qt Widgets를 기본으로 사용한다. QML은 영역 선택기처럼 명확한 이점이 입증된 격리 컴포넌트가 아니면 도입하지 않는다.
- Qt 객체는 GUI 스레드에서만 생성·변경하고, 캡처·오디오·인코딩 작업은 `QThread`/worker 또는 별도 프로세스에서 수행한다.
- 컴포넌트 간 통신은 PySide6 신호/슬롯과 타입이 명확한 이벤트 객체를 사용한다.
- 트레이, 설정, 테마, 접근성, 다국어화도 가능한 범위에서 PySide6/Qt 기본 기능을 우선 사용한다.

### 기본 스택

- Python 3.12 이상
- PySide6: GUI, 신호/슬롯, `QSystemTrayIcon`, `QSettings`
- DXcam: Windows 고성능 화면 캡처의 첫 번째 후보
- python-mss: GDI 기반 폴백과 테스트 대체 백엔드
- SoundCard: WASAPI 시스템 오디오 루프백과 마이크 캡처 후보
- NumPy: 프레임 버퍼와 오디오 샘플 처리
- FFmpeg/ffprobe: 인코딩, 믹싱, 리먹스, 검증
- Pillow: 썸네일과 정적 이미지 처리
- pywin32 또는 최소 `ctypes`: 창 열거, `RegisterHotKey`, DPI, 전원 유지
- pytest, pytest-qt: 단위/GUI 테스트
- Ruff: 린트와 포맷
- PyInstaller: Windows one-folder 배포

### 중요한 선택 규칙

- 전역 단축키는 가능하면 Win32 `RegisterHotKey`로 구현한다. 모든 키 입력을 훅킹하는 라이브러리를 기본값으로 사용하지 않는다.
- 트레이는 PySide6의 `QSystemTrayIcon`을 사용한다. 별도 pystray 의존성을 추가하지 않는다.
- 미디어 핵심은 인터페이스 뒤에 숨긴다. `CaptureBackend`, `AudioBackend`, `EncoderBackend` 프로토콜을 만들고 구현을 교체할 수 있게 한다.
- PyAV는 MVP 필수 의존성으로 넣지 않는다. 정말 필요하면 별도 기술 결정 기록(ADR)에서 복잡도와 배포 라이선스를 검토한다.
- FFmpeg를 번들할 경우 빌드 구성과 코덱 라이선스를 검증한다. 실행 파일 옆에 라이선스와 `THIRD_PARTY_NOTICES.md`를 배포한다.
- FFmpeg가 없을 때 앱이 바로 종료되지 않게 하고, 진단 화면에서 경로를 지정하거나 설치 안내를 볼 수 있게 한다.

## 7. 권장 저장소 구조

```text
vcam/
├─ CLAUDE.md
├─ README.md
├─ LICENSE
├─ THIRD_PARTY_NOTICES.md
├─ pyproject.toml
├─ src/vcam/
│  ├─ __main__.py
│  ├─ app.py
│  ├─ domain/
│  │  ├─ models.py
│  │  ├─ states.py
│  │  └─ events.py
│  ├─ services/
│  │  ├─ recording_controller.py
│  │  ├─ profile_service.py
│  │  ├─ recovery_service.py
│  │  └─ media_library.py
│  ├─ capture/
│  │  ├─ base.py
│  │  ├─ dxcam_backend.py
│  │  ├─ mss_backend.py
│  │  ├─ window_catalog.py
│  │  └─ region.py
│  ├─ audio/
│  │  ├─ base.py
│  │  ├─ wasapi_backend.py
│  │  ├─ mixer.py
│  │  └─ meter.py
│  ├─ encoding/
│  │  ├─ ffmpeg.py
│  │  ├─ encoder_probe.py
│  │  ├─ muxer.py
│  │  └─ validator.py
│  ├─ platform/windows/
│  │  ├─ hotkeys.py
│  │  ├─ dpi.py
│  │  ├─ power.py
│  │  └─ trash.py
│  ├─ ui/
│  │  ├─ main_window.py
│  │  ├─ theme.py
│  │  ├─ tokens.py
│  │  ├─ viewmodels/
│  │  └─ widgets/
│  │     ├─ region_overlay.py
│  │     ├─ source_picker.py
│  │     ├─ audio_meter.py
│  │     ├─ recording_bar.py
│  │     └─ recent_recordings.py
│  └─ util/
│     ├─ paths.py
│     ├─ logging.py
│     └─ clock.py
├─ tests/
│  ├─ unit/
│  ├─ integration/
│  └─ smoke/
├─ scripts/
│  ├─ dev.ps1
│  ├─ test.ps1
│  └─ build.ps1
└─ assets/
   ├─ icons/
   └─ translations/
```

UI가 백엔드 라이브러리를 직접 호출하지 않게 한다. `MainWindow → RecordingController → Backend` 방향만 허용하고, 결과는 Qt 신호 또는 명시적인 이벤트 객체로 되돌린다.

## 8. 녹화 상태 머신

상태를 불리언 여러 개로 표현하지 않는다. 다음 단일 상태 머신을 사용한다.

```text
IDLE
  └─ select_source → SELECTING
SELECTING
  ├─ confirm → READY
  └─ cancel → IDLE
READY
  └─ start → COUNTDOWN
COUNTDOWN
  ├─ elapsed → RECORDING
  └─ cancel → READY
RECORDING
  ├─ pause → PAUSED
  ├─ stop → FINALIZING
  └─ fatal_error → RECOVERING
PAUSED
  ├─ resume → RECORDING
  ├─ stop → FINALIZING
  └─ fatal_error → RECOVERING
FINALIZING
  ├─ success → REVIEW
  └─ failure → RECOVERING
RECOVERING
  ├─ recovered → REVIEW
  └─ failed → ERROR
REVIEW
  ├─ new_recording → READY
  └─ close → IDLE
ERROR
  └─ acknowledge → READY
```

각 전이는 테스트하며, 잘못된 상태에서 온 명령은 조용히 무시하지 말고 구조화된 오류를 기록한다.

## 9. 캡처·오디오·인코딩 파이프라인

### 공통 시간 기준

- 녹화 직전에 `time.perf_counter_ns()`로 세션 기준 시각을 한 번 만든다.
- 비디오 프레임, 시스템 오디오, 마이크, 커서, 클릭 이벤트는 같은 기준 시각을 사용한다.
- 벽시계 변경에 영향을 받는 `time.time()`을 동기화 기준으로 사용하지 않는다.
- 각 스트림의 첫 샘플 오프셋과 드롭 수를 메타데이터에 기록한다.

### P0 권장 파이프라인

```text
DXcam/WGC ── BGRA frames ── bounded queue ── video encoder ── session.partial.mkv
SoundCard loopback ───────── bounded queue ── system.wav ─────┐
SoundCard microphone ─────── bounded queue ── mic.wav ────────┤
                                                              └─ FFmpeg mix/mux → final.mp4
```

- 먼저 영상과 오디오를 안전한 중간 파일에 기록한 뒤 종료 시 FFmpeg로 믹스/리먹스한다.
- 중간 컨테이너는 비정상 종료 복구가 쉬운 Matroska를 우선한다.
- 최종 MP4는 임시 이름으로 생성하고 `ffprobe` 검증이 성공한 후 원자적으로 최종 이름으로 바꾼다.
- 큐는 반드시 크기 제한이 있어야 한다. 메모리를 무한히 늘리지 않는다.
- 인코더가 밀릴 때 정책을 명시한다: 비디오 프레임 드롭은 허용하되 오디오 기준 시간은 유지하고 드롭 수를 표시한다.
- 일시정지는 결과 시간축에서 공백으로 남기지 않는다. 재개 시 타임스탬프를 연속되게 보정한다.
- 녹화 중 디스플레이 모드 변경, 잠금 화면, 장치 분리, 오디오 기본 장치 변경을 감지하고 사용자에게 상태를 알린다.

### 인코더 자동 선택

1. 짧은 시험 인코딩으로 NVENC를 확인한다.
2. 실패하면 QuickSync를 확인한다.
3. 실패하면 AMF를 확인한다.
4. 모두 실패하면 `libx264`로 폴백한다.
5. 첫 1~2초 안에 하드웨어 인코더가 실패하면 소프트웨어 인코더로 한 번만 자동 재시도한다.
6. 최종 선택된 인코더를 로그와 결과 메타데이터에 기록한다.

장치 이름만 보고 지원 여부를 추측하지 않는다. 실제 짧은 인코딩 성공 여부를 확인한다.

### 해상도와 프레임

- 기본값: 원본 해상도, 30 FPS, 균형 프리셋.
- 60 FPS는 사용자가 선택하거나 게임 프로필일 때만 사용한다.
- 인코더가 요구하면 너비/높이를 짝수로 맞추되, 사용자가 고른 영역을 몰래 크게 자르지 않는다. 최대 1px 패딩한다.
- 다중 DPI 좌표는 논리 좌표가 아닌 물리 픽셀로 캡처 백엔드에 전달한다.
- 미리보기는 낮은 빈도·낮은 해상도로 별도 샘플링하여 캡처 성능을 해치지 않는다.

## 10. 설정과 데이터 모델

최소 모델은 타입이 명확한 dataclass 또는 동등한 타입 모델로 정의한다.

```python
RecordingProfile(
    id: str,
    name: str,
    source_kind: Literal["display", "window", "region", "audio"],
    fps: int,
    quality_preset: Literal["small", "balanced", "high", "custom"],
    encoder_preference: Literal["auto", "nvenc", "qsv", "amf", "x264"],
    system_audio_enabled: bool,
    microphone_enabled: bool,
    webcam_enabled: bool,
    cursor_enabled: bool,
)

RecordingSession(
    id: str,
    state: RecordingState,
    started_monotonic_ns: int,
    source: CaptureSource,
    profile: RecordingProfile,
    temp_paths: SessionPaths,
    metrics: RecordingMetrics,
)
```

- 사용자 설정은 `%LOCALAPPDATA%\vcam\` 아래에 저장한다.
- 녹화 파일은 기본적으로 사용자의 Videos 폴더 아래 `vcam`에 저장한다.
- 설정 스키마에 버전을 두고 마이그레이션한다.
- 파일명 템플릿 기본값: `vcam_{yyyy-MM-dd}_{HH-mm-ss}`.
- 로그에는 화면 내용, 키 입력 원문, 장치의 민감한 식별자, 전체 사용자 경로를 불필요하게 남기지 않는다.

## 11. 안정성·복구·개인정보

- 앱 시작 시 `.partial`, 미완료 세션 메타데이터, orphan WAV를 검색하고 복구 UI를 보여준다.
- 녹화 중 절전 방지는 Win32 실행 상태 API를 쓰고, 세션 종료 시 반드시 해제한다.
- 디스크 여유 공간을 시작 전과 녹화 중 주기적으로 확인한다. 임계값 아래면 경고하고 안전 종료한다.
- 출력 폴더 쓰기 권한과 FFmpeg 실행 가능 여부를 녹화 전 검사한다.
- 원본 파일 삭제는 직접 삭제하지 말고 휴지통으로 이동한다.
- 키 입력 오버레이는 기본 OFF다. 켜더라도 비밀번호 필드, PIN, 결제 창으로 판단되는 경우 숨기고 모든 문자 입력을 저장하지 않는다.
- 화면 내용 분석, OCR, 스마트 줌은 기본적으로 로컬 처리한다.
- 크래시 로그 업로드는 자동으로 하지 않는다. 사용자가 내용을 검토하고 명시적으로 공유하게 한다.

## 12. 구현 순서

### 단계 0: 기반과 진단

- `pyproject.toml`, 패키지 구조, Ruff, pytest, 로깅, 경로 유틸리티를 만든다.
- PySide6 빈 창보다 먼저 `SystemProbe`를 구현해 OS, 모니터, FFmpeg, 인코더, 오디오 장치를 진단한다.
- 가짜 캡처·오디오 백엔드를 만들어 실제 장치 없이 상태 머신과 UI를 테스트한다.

완료 조건: 앱이 켜지고 진단 화면에서 사용 가능/불가 이유를 설명하며 단위 테스트가 통과한다.

### 단계 1: 무음 화면 녹화 수직 슬라이스

- 단일 모니터 캡처 → 중간 MKV → 최종 MP4까지 실제로 동작시킨다.
- 시작, 일시정지, 재개, 종료, 경과 시간, 오류 처리를 연결한다.
- 3초/30초 녹화 결과를 ffprobe로 검증한다.

완료 조건: 다른 PC에서도 재생 가능한 MP4가 생성되고 앱 UI가 멈추지 않는다.

### 단계 2: 영역과 다중 모니터

- 영역 선택 오버레이와 전체/모니터/마지막 영역을 구현한다.
- 서로 다른 DPI와 음수 좌표 모니터 조합을 테스트한다.

완료 조건: 선택 사각형과 실제 결과의 픽셀이 일치한다.

### 단계 3: 오디오와 동기화

- 시스템 루프백과 마이크를 독립 트랙으로 기록한다.
- 음량 미터, 장치 선택, 믹스, 일시정지 시간축 보정을 구현한다.
- 10분 녹화에서 A/V 드리프트를 측정한다.

완료 조건: 시작과 종료의 동기 오차가 허용 범위 안이며 장치 실패가 영상 파일을 파괴하지 않는다.

### 단계 4: 사용자 경험

- 전역 단축키, 트레이, 녹화 바, 최근 파일, 미리보기, 기본 설정을 구현한다.
- 키보드 접근성, 화면 읽기용 accessible name, 고대비 테마를 확인한다.

### 단계 5: P1 기능

- 특정 창, 웹캠 PIP, 커서/클릭, 주석, 자르기, GIF, 예약 녹화를 기능 플래그 아래 하나씩 추가한다.
- 각 기능은 세로로 완성한다. UI만 먼저 대량으로 만들지 않는다.

## 13. 테스트 기준

### 단위 테스트

- 상태 전이와 잘못된 명령
- 파일명 충돌 회피와 경로 정규화
- 설정 스키마 마이그레이션
- 영역 좌표의 DPI 변환
- 프레임 페이싱과 드롭 계산
- 일시정지 타임스탬프 보정
- 인코더 폴백 순서
- 복구 가능한 임시 파일 탐지

### 통합 테스트

- 가짜 비디오/오디오 스트림을 3초 기록하고 ffprobe로 길이·코덱·해상도 검증
- 시스템 오디오만, 마이크만, 둘 다, 둘 다 OFF 조합
- 디스크 가득 참, FFmpeg 없음, 장치 분리, 캡처 백엔드 오류
- 녹화 도중 앱 창 닫기와 Windows 종료 이벤트

### 수동 Windows 테스트 행렬

- Windows 10과 Windows 11
- 100%, 125%, 150%, 200% DPI
- 단일 모니터, 좌우 듀얼, 주 모니터가 오른쪽인 음수 좌표 구성
- Intel 내장 그래픽, NVIDIA, AMD, 하드웨어 인코더 없음
- 헤드셋 연결/분리, Bluetooth 마이크, 기본 오디오 장치 변경
- 1080p30 30분, 1440p60 10분, 가능하면 4K30 10분
- 전체 화면 게임, 일반 창, 브라우저 하드웨어 가속 창

### 품질 예산

- 유휴 상태 CPU 평균 1% 내외를 목표로 한다.
- 녹화 중 UI 입력 응답은 100ms 안에 반응해야 한다.
- 30분 녹화 동안 메모리 사용량이 지속적으로 증가하지 않아야 한다.
- 10분 결과에서 A/V 드리프트 목표는 80ms 이하, 최대 허용은 150ms다.
- 사용자가 정한 FPS를 못 지키면 조용히 숨기지 말고 드롭 프레임 수를 상태/로그에 남긴다.

## 14. 개발 명령과 작업 규칙

초기 스크립트는 다음 인터페이스를 제공한다.

```powershell
.\scripts\dev.ps1       # 가상환경 준비 후 앱 실행
.\scripts\test.ps1      # Ruff + pytest
.\scripts\build.ps1     # PyInstaller one-folder 빌드
```

Claude Code 작업 규칙:

1. 변경 전에 관련 파일과 테스트를 읽고 현재 구조를 요약한다.
2. 한 번에 하나의 수직 기능을 구현한다.
3. 외부 API를 감싸는 최소 인터페이스를 먼저 만든다.
4. GUI 위젯 안에 캡처·인코딩 로직을 넣지 않는다.
5. 스레드 간에는 불변 데이터, 제한된 큐, Qt 신호를 사용한다.
6. `except Exception: pass`, 무제한 재시도, 무제한 큐를 금지한다.
7. 프로세스 종료 시 FFmpeg stdin을 닫고 제한 시간 내 정상 종료를 기다린 뒤에만 강제 종료한다.
8. 사용자 파일을 덮어쓰지 않는다. 임시 파일에서 검증 후 원자적 이름 변경을 사용한다.
9. 기능 변경에는 테스트, 오류 메시지, 로그, 문서 중 필요한 항목을 함께 갱신한다.
10. TODO나 비활성 버튼을 남기면 현재 릴리스 범위에서 숨긴다.
11. 새 의존성을 추가하기 전에 유지보수 상태, 라이선스, 바이너리 크기, Windows 지원을 기록한다.
12. 완료 보고에는 바뀐 파일, 실행한 검증, 남은 실제 제약만 간단히 적는다.

## 15. 완료 정의

기능은 다음을 모두 만족해야 완료다.

- 실제 Windows 환경에서 핵심 경로가 동작한다.
- UI가 멈추지 않고 취소·실패·재시도 경로가 있다.
- 생성 파일을 ffprobe와 일반 플레이어로 검증했다.
- 실패 시 사용자가 무엇을 해야 하는지 한국어로 알 수 있다.
- 단위 또는 통합 테스트가 회귀를 막는다.
- 개인정보와 파일 손실 위험을 검토했다.
- 사용한 외부 라이브러리와 에셋의 라이선스를 기록했다.
- 문서의 범위를 벗어난 기능을 완료한 것처럼 표시하지 않는다.

## 16. 조사 출처

아래 링크는 기능과 구조를 이해하기 위한 근거다. 디자인 에셋이나 코드를 무단 복제하는 근거가 아니다.

### 제품 공식 자료

- [Bandicam 사용자 인터페이스와 녹화 모드](https://www.bandicam.com/guide/user-interface/)
- [Bandicam 영역 녹화 창과 실시간 그리기](https://www.bandicam.com/support/configuration/rectangle_window/)
- [Bandicam 예약 녹화](https://www.bandicam.com/guide/scheduled-recording/)
- [Bandicam 웹캠 오버레이](https://www.bandicam.com/guide/overlay-webcam/)
- [oCam 메인 화면과 기능](https://www.ohsoft.net/eng/ocam_help.php)
- [oCam 공식 도움말 문서](https://ohsoft.net/docs/ocam_eng/index.php?p=desk&page=1)
- [OBS Studio 기능](https://obsproject.com/)
- [OBS Studio 개요와 하드웨어 인코더·리플레이 버퍼](https://obsproject.com/kb/obs-studio-overview)
- [ShareX 기능](https://getsharex.com/)
- [ShareX 영역 캡처와 캡처 후 작업](https://getsharex.com/docs/region-capture)
- [ShareX 이미지 편집기](https://getsharex.com/docs/image-editor)
- [ShareX OCR](https://getsharex.com/docs/ocr)
- [ScreenToGif 공식 사이트](https://www.screentogif.com/)
- [ScreenToGif 편집기 설명](https://github.com/NickeManarin/ScreenToGif/wiki/Help-%E2%96%AA-Editor-%E2%9C%8F%EF%B8%8F)
- [Microsoft Snipping Tool 화면·영상 캡처](https://support.microsoft.com/en-us/windows/apps/use-snipping-tool-to-capture-screenshots)

### GitHub와 기술 참고

- [FollowCursor 저장소](https://github.com/sabbour/followcursor) — MIT, Python/PySide6/WGC/FFmpeg 구조 참고
- [FollowCursor 아키텍처 문서](https://github.com/sabbour/followcursor/blob/main/docs/ARCHITECTURE.md)
- [DXcam](https://github.com/ra1nty/DXcam) — MIT, Windows DXGI/WinRT 화면 캡처 후보
- [python-mss](https://github.com/BoboTiG/python-mss) — MIT, 폴백 화면 캡처 후보
- [SoundCard](https://github.com/bastibe/SoundCard) — BSD-3-Clause, WASAPI 루프백 후보
- [PyAV](https://github.com/PyAV-Org/PyAV) — BSD-3-Clause 코드, 바이너리 FFmpeg 구성은 별도 라이선스 검토 필요
- [OBS 하드웨어 인코딩 설명](https://obsproject.com/kb/hardware-encoding)

출처의 최신 상태와 라이선스는 의존성을 고정하거나 배포하기 직전에 다시 확인한다.
