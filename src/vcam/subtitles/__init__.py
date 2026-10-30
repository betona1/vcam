"""자막(SRT) 생성: 로컬 Whisper(faster-whisper) 음성 인식과 선택적 번역.

이 패키지의 핵심 로직은 프로젝트 소유자가 만든 SRTCONV(E:\\...\\SRTCONV, 동일 저작자)에서
가져와 vCAM 구조에 맞게 다시 작성했다. Qt에 의존하지 않으며 GUI 스레드에서 호출하지 않는다.

무거운 의존성(faster-whisper, ctranslate2)은 `vcam[subtitles]` 추가 설치 항목이다.
설치되지 않았으면 `transcriber.availability()`가 이유를 알려 주고 UI는 안내만 보여 준다.
"""
