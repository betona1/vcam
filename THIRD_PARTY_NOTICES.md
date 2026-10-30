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
| [PyAV](https://github.com/PyAV-Org/PyAV) | faster-whisper의 의존성. **vCAM은 실행 시 사용하지 않음**(소리 추출은 외부 FFmpeg) | BSD-3-Clause 코드 + **동봉 FFmpeg DLL은 GPL 빌드(libx264/x265 포함)** |

**배포 시 주의:** `subtitles` 항목을 포함해 PyInstaller로 빌드하면 PyAV에 동봉된 GPL FFmpeg DLL이 배포본에 들어갑니다.
이 경우 배포본 전체가 GPL 호환 조건을 따라야 하므로, 자막 기능이 포함된 빌드를 배포하기 전에 라이선스 표기를 정리하거나
PyAV를 LGPL 빌드로 교체해야 합니다. 자막 기능이 없는 빌드(기본)는 영향이 없습니다.

### 별도로 내려받는 모델·런타임 (배포본에 포함하지 않음)

| 구성 요소 | 라이선스 | 비고 |
| --- | --- | --- |
| Whisper 모델 (Systran/faster-whisper-* 등) | MIT | 사용자가 '모델이 없으면 내려받기'를 켠 경우 HuggingFace에서 받음 |
| M2M100_418M (facebook/m2m100_418M, CTranslate2 int8 변환본) | MIT | 기본 오프라인 번역 모델. 사용자가 `%LOCALAPPDATA%\vcam\models`에 넣음 |
| NLLB-200 (facebook/nllb-200-distilled-600M) | **CC-BY-NC-4.0 (비상업 전용)** | 배포하지 않음. 사용자가 직접 넣은 경우에만 인식 |
| NVIDIA CUDA Runtime / cuBLAS 12, cuDNN 9 | NVIDIA EULA / cuDNN SLA | GPU 가속용. 사용자가 `%LOCALAPPDATA%\vcam\cuda`에 두거나 `vcam[subtitles-cuda]`로 설치 |

## FFmpeg

vcam은 FFmpeg/ffprobe를 **별도 프로세스로 실행**하며 배포본에 포함하지 않습니다.
사용자가 설치한 FFmpeg 빌드(예: `winget install Gyan.FFmpeg`, GPL 빌드)의 라이선스는 해당 빌드를 따릅니다.
FFmpeg를 번들하게 되면 빌드 구성과 코덱 라이선스를 검증한 뒤 이 문서와 배포 폴더에 고지를 추가합니다.

## 아이콘과 이미지

- `src/vcam/ui/icons.py`의 라인 아이콘은 이 프로젝트를 위해 직접 제작했습니다.
- 앱 아이콘(`assets/icons/vcam.*`)과 `assets/brand/vaveling_face.png`, `vaveling_glasses.jpg`는
  저작권자(프로젝트 소유자)가 제공한 베이블링 5단계 캐릭터 이미지를 잘라 만든 것입니다.
