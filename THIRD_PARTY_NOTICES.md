# Third-Party Notices

vCAM은 아래 오픈 소스 구성 요소를 **의존성으로 사용**합니다. 이 저장소에 이들의 소스 코드를
복사해 넣지는 않았습니다. 배포 전에는 고정한 버전의 라이선스를 다시 확인합니다.

| 구성 요소 | 용도 | 라이선스 |
| --- | --- | --- |
| [PySide6 / Qt 6](https://www.qt.io/qt-for-python) | GUI | LGPL-3.0 (동적 링크) |
| [NumPy](https://numpy.org/) | 프레임 버퍼 | BSD-3-Clause |
| [python-mss](https://github.com/BoboTiG/python-mss) | GDI 화면 캡처 | MIT |
| [DXcam](https://github.com/ra1nty/DXcam) | DXGI 화면 캡처 | MIT |
| [comtypes](https://github.com/enthought/comtypes) | DXcam 의존성 | MIT |
| [SoundCard](https://github.com/bastibe/SoundCard) | WASAPI 시스템 소리 루프백·마이크 녹음 | BSD-3-Clause |
| [Pillow](https://python-pillow.org/) | 아이콘 생성 | MIT-CMU (HPND) |
| [pywin32](https://github.com/mhammond/pywin32) | Windows API | PSF-2.0 |

## 자막 만들기(SRT) — 선택 설치 `vcam[subtitles]`

자막 기능의 핵심 로직은 프로젝트 소유자가 만든 SRTCONV(동일 저작자)에서 가져와 다시 작성했습니다. 아래는 그 기능이 쓰는 의존성입니다.

| 구성 요소 | 용도 | 라이선스 |
| --- | --- | --- |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | Whisper 음성 인식 | MIT |
| [CTranslate2](https://github.com/OpenNMT/CTranslate2) | 음성 인식·번역 모델 실행 | MIT |
| [Silero VAD](https://github.com/snakers4/silero-vad) (faster-whisper 동봉 onnx) | 무음 구간 제거 | MIT |
| [onnxruntime](https://github.com/microsoft/onnxruntime) | VAD 실행 | MIT |
| [sentencepiece](https://github.com/google/sentencepiece) | 번역 토크나이저 | Apache-2.0 |
| [tokenizers](https://github.com/huggingface/tokenizers), [huggingface_hub](https://github.com/huggingface/huggingface_hub) | 모델 토크나이저·내려받기 | Apache-2.0 |
| [deep-translator](https://github.com/nidhaloff/deep-translator) | 온라인 번역(사용자가 켠 경우만) | MIT — Google 번역은 비공식 엔드포인트, MyMemory는 자체 약관 |
| [PyAV](https://github.com/PyAV-Org/PyAV) | faster-whisper의 의존성. **vCAM은 사용하지 않고 배포본에도 넣지 않음**(소리 추출은 외부 FFmpeg) | BSD-3-Clause 코드 + 동봉 FFmpeg DLL은 GPL 빌드 → 배포본에서 제외 |

**배포본과 PyAV:** PyAV의 Windows 휠에는 GPL 빌드 FFmpeg DLL(libx264/x265 포함)이 들어 있어, 이를 함께 배포하면 MIT 배포본 전체가
GPL 조건을 따라야 합니다. 그래서 `scripts/build.ps1`은 `--exclude-module av`로 PyAV를 배포본에서 빼고, 런타임 훅
`scripts/pyi_rth_no_pyav.py`가 import만 통과하는 빈 `av` 모듈을 등록합니다(vCAM은 PyAV 기능을 호출하지 않음).
배포본에는 PyAV 코드도 FFmpeg DLL도 포함되지 않습니다. 개발 환경(`pip install vcam[subtitles]`)에는 PyAV가 설치되지만 실행 시 쓰지 않습니다.

### 별도로 내려받는 모델·런타임 (배포본에 포함하지 않음)

| 구성 요소 | 라이선스 | 비고 |
| --- | --- | --- |
| Whisper 모델 (Systran/faster-whisper-* 등) | MIT | 사용자가 '모델이 없으면 내려받기'를 켠 경우 HuggingFace에서 받음 |
| M2M100_418M (facebook/m2m100_418M, CTranslate2 int8 변환본) | MIT | 기본 오프라인 번역 모델. 사용자가 `%LOCALAPPDATA%\vcam\models`에 넣음 |
| NLLB-200 (facebook/nllb-200-distilled-600M) | **CC-BY-NC-4.0 (비상업 전용)** | 배포하지 않음. 사용자가 직접 넣은 경우에만 인식 |
| NVIDIA CUDA Runtime / cuBLAS 12, cuDNN 9 | NVIDIA EULA / cuDNN SLA | GPU 가속용. 사용자가 `%LOCALAPPDATA%\vcam\cuda`에 두거나 `vcam[subtitles-cuda]`로 설치. CTranslate2 휠에 든 작은 `cudnn64_9.dll` 로더와 Intel `libiomp5md.dll`(Intel Simplified Software License)은 배포본에 포함 |

## 인터넷 영상 내려받기 — 선택 설치 `vcam[download]`

| 구성 요소 | 용도 | 라이선스 |
| --- | --- | --- |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | YouTube·TikTok·Instagram 등 인터넷 영상·자막 내려받기 (별도 프로세스가 아닌 Python 모듈로 호출, 코드 복사 없음) | Unlicense (퍼블릭 도메인) |
| [yt-dlp-ejs](https://github.com/yt-dlp/ejs) | YouTube 서명 해독용 JavaScript 스크립트 | Unlicense AND MIT AND ISC |
| [curl_cffi](https://github.com/lexiforest/curl_cffi) | TikTok·Instagram 등이 요구하는 브라우저 위장(TLS 지문) 네트워크 계층. 배포본에 `libcurl-impersonate` DLL 포함 | MIT. 동봉 DLL은 [curl-impersonate](https://github.com/lexiforest/curl-impersonate)(MIT)와 curl(curl 라이선스), BoringSSL(ISC·OpenSSL), nghttp2(MIT), brotli(MIT), zlib, zstd(BSD-3) 등을 정적 링크 — 배포 직전 휠의 licenses 폴더로 재확인 |
| [pycryptodomex](https://github.com/Legrandin/pycryptodome) | 암호화된 HLS 조각 복호화 | BSD-2-Clause / Public Domain |
| [requests](https://github.com/psf/requests), [urllib3](https://github.com/urllib3/urllib3), [certifi](https://github.com/certifi/python-certifi), [websockets](https://github.com/python-websockets/websockets), [Brotli](https://github.com/google/brotli) | yt-dlp 네트워크 계층 | Apache-2.0 / MIT / MPL-2.0 / BSD-3-Clause / MIT |

- **mutagen**(GPL-2.0+)은 yt-dlp의 선택 의존성이지만 vCAM은 설치·배포하지 않습니다 (썸네일·태그 삽입을 쓰지 않음).
- **JS 런타임(Deno 등)** 은 배포본에 넣지 않습니다. 사용자가 `winget install DenoLand.Deno`로 설치하면 앱이 PATH에서 찾아 씁니다 (Deno: MIT).
- 내려받은 영상의 저작권과 각 사이트의 이용약관은 사용자가 지켜야 합니다. 앱은 DRM을 우회하지 않습니다. 로그인이 필요한 영상은 사용자가 '로그인 쿠키' 옵션을 켠 경우에만 사용자 브라우저의 쿠키를 그 사이트로 보냅니다.

## FFmpeg

vcam은 FFmpeg/ffprobe를 **별도 프로세스로 실행**하며 배포본에 포함하지 않습니다.
사용자가 설치한 FFmpeg 빌드(예: `winget install Gyan.FFmpeg`, GPL 빌드)의 라이선스는 해당 빌드를 따릅니다.
FFmpeg를 번들하게 되면 빌드 구성과 코덱 라이선스를 검증한 뒤 이 문서와 배포 폴더에 고지를 추가합니다.

## 설치 프로그램 — Inno Setup

- 설치 프로그램 `vcam-setup-<버전>.exe`는 [Inno Setup 6](https://jrsoftware.org/isinfo.php)(Jordan Russell, Martijn Laan)으로 만듭니다.
  Inno Setup 라이선스는 상업용을 포함해 무료 사용과, 만든 설치 프로그램의 배포를 허용합니다: https://jrsoftware.org/files/is/license.txt
- 설치 스크립트 `packaging/vcam.iss`는 vCAM 저장소의 코드이며 다른 스크립트를 복사하지 않았습니다. 한국어 화면은 Inno Setup에 포함된 공식 번역 파일(`Korean.isl`)을 씁니다.
- 설치 프로그램은 빌드 도구일 뿐 앱 실행에는 필요하지 않으며, zip 배포본에는 포함되지 않습니다.

## 아이콘과 이미지

- `src/vcam/ui/icons.py`의 라인 아이콘은 이 프로젝트를 위해 직접 제작했습니다.
- 앱 아이콘(`assets/icons/vcam.*`)과 `assets/brand/vaveling_face.png`, `vaveling_glasses.jpg`는
  저작권자(프로젝트 소유자)가 제공한 베이블링 5단계 캐릭터 이미지를 잘라 만든 것입니다.
