# Third-Party Notices

vavelingCam은 아래 오픈 소스 구성 요소를 **의존성으로 사용**합니다. 이 저장소에 이들의 소스 코드를
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

## FFmpeg

vcam은 FFmpeg/ffprobe를 **별도 프로세스로 실행**하며 배포본에 포함하지 않습니다.
사용자가 설치한 FFmpeg 빌드(예: `winget install Gyan.FFmpeg`, GPL 빌드)의 라이선스는 해당 빌드를 따릅니다.
FFmpeg를 번들하게 되면 빌드 구성과 코덱 라이선스를 검증한 뒤 이 문서와 배포 폴더에 고지를 추가합니다.

## 아이콘과 이미지

- `assets/icons/vcam.svg` 및 여기서 생성한 `vcam.ico`/`vcam.png`, `src/vcam/ui/icons.py`의 라인 아이콘은
  이 프로젝트를 위해 직접 제작했습니다.
- `assets/brand/vaveling_lv5.jpg`는 저작권자(프로젝트 소유자)가 제공한 바브링 캐릭터 이미지
  (`VAVEVAVE_Level5.png`)를 잘라 축소한 것입니다.
