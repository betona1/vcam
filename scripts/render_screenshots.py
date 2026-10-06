"""문서용 스크린샷 생성 → docs/images/*.png

실제 바탕화면 대신 데모 이미지, 실제 장치 대신 가짜 오디오 장치를 써서 개인 화면이 문서에 들어가지 않게 한다.
사용: python scripts/render_screenshots.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "images"
DEMO = Path(tempfile.mkdtemp(prefix="vcam-shots-"))
os.environ["LOCALAPPDATA"] = str(DEMO / "localappdata")
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from vcam.audio.base import AudioDevice, FakeAudioSource  # noqa: E402
from vcam.domain.models import Rect  # noqa: E402
from vcam.encoding.ffmpeg import find_ffmpeg  # noqa: E402
from vcam.services.audio_service import AudioService  # noqa: E402
from vcam.services.profile_service import ProfileService, Settings  # noqa: E402
from vcam.ui.dialogs import AboutDialog, SettingsDialog  # noqa: E402
from vcam.ui.main_window import MainWindow  # noqa: E402
from vcam.ui.theme import apply_theme  # noqa: E402
from vcam.ui.widgets.guide_frame import GuideFrame  # noqa: E402
from vcam.ui.widgets.recording_bar import RecordingBar  # noqa: E402
from vcam.ui.widgets.region_overlay import RegionSelector, _ScreenOverlay  # noqa: E402

BRAND = ROOT / "assets" / "brand" / "vaveling_lv5.jpg"


def demo_desktop(w: int = 1920, h: int = 1080) -> QImage:
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    p = QPainter(img)
    g = QLinearGradient(0, 0, w, h)
    g.setColorAt(0, QColor("#2a2f6b"))
    g.setColorAt(1, QColor("#5b3fb0"))
    p.fillRect(img.rect(), g)
    art = QPixmap(str(BRAND))
    art = art.scaledToHeight(int(h * 0.8), Qt.TransformationMode.SmoothTransformation)
    p.drawPixmap((w - art.width()) // 2, (h - art.height()) // 2, art)
    p.setPen(QColor(255, 255, 255, 200))
    f = p.font()
    f.setPointSize(36)
    f.setBold(True)
    p.setFont(f)
    p.drawText(QRectF(0, h * 0.05, w, 80), Qt.AlignmentFlag.AlignCenter, "vavelingCam 데모 화면")
    p.end()
    return img


def make_demo_recordings(out_dir: Path) -> None:
    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    frame = DEMO / "frame.png"
    demo_desktop(1280, 720).save(str(frame))
    for name, secs in (("vcam_2026-10-06_10-30-12", 42), ("vcam_2026-10-06_09-15-48", 125)):
        subprocess.run(
            [str(ffmpeg.ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-loop", "1", "-i", str(frame),
             "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", str(secs), "-r", "5",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out_dir / f"{name}.mp4")],
            check=True,
        )  # fmt: skip


def compose(background: QImage, overlay: QPixmap, offset: QPoint | None = None) -> QImage:
    img = background.copy()
    p = QPainter(img)
    p.drawPixmap(offset or QPoint(0, 0), overlay)
    p.end()
    return img


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    app = QApplication([])  # QPixmap보다 먼저 만들어야 한다
    out_dir = DEMO / "videos"
    make_demo_recordings(out_dir)
    desktop = demo_desktop()
    results: list[str] = []

    def shoot_main(theme: str, then) -> None:
        profiles = ProfileService(Path(os.environ["LOCALAPPDATA"]) / "vcam" / f"settings-{theme}.json")
        settings = Settings(output_dir=str(out_dir), theme=theme, first_run_notice_ack=True, auto_update=False)
        settings = profiles.update(settings, profile=settings.profile.__class__(microphone_enabled=True))
        palette = apply_theme(app, theme)
        w = MainWindow(profiles, settings, palette)
        w.controller.audio.shutdown()
        w.controller.audio = AudioService(
            lambda kind, _d: FakeAudioSource(channels=2 if kind == "system" else 1,
                                             amplitude=0.45 if kind == "system" else 0.2)  # fmt: skip
        )
        w.controller.apply_audio_settings()
        w._on_devices_loaded((
            [AudioDevice("", "스피커 (Realtek High Definition Audio)", "system", True)],
            [AudioDevice("m1", "마이크 (USB Microphone)", "microphone", True)],
        ))  # fmt: skip
        w.sampler.shutdown()
        w.preview.set_image(desktop.scaledToWidth(720, Qt.TransformationMode.SmoothTransformation))
        w.resize(1100, 780)
        w.move(-6000, -6000)
        w.show()

        def snap() -> None:
            # 실제 화면·장치 정보가 늦게 도착해 덮어쓰지 않도록 찍기 직전에 데모 값을 다시 넣는다.
            w.sampler.frame_ready.disconnect()
            w._bridge.devices_loaded.disconnect()
            w.preview.set_image(desktop.scaledToWidth(720, Qt.TransformationMode.SmoothTransformation))
            w._on_devices_loaded((
                [AudioDevice("", "스피커 (Realtek High Definition Audio)", "system", True)],
                [AudioDevice("m1", "마이크 (USB Microphone)", "microphone", True)],
            ))  # fmt: skip
            w.output_label.setText(r"C:\Users\사용자\Videos\vcam")
            w._update_meters()
            name = f"main_{theme}.png"
            w.grab().save(str(OUT / name))
            results.append(name)
            menu = w.menuBar().actions()[1].menu()
            menu.adjustSize()
            if theme == "dark":
                menu.grab().save(str(OUT / "menu_record.png"))
                dlg = SettingsDialog(w.settings, w.p, w)
                dlg.adjustSize()
                dlg.grab().save(str(OUT / "settings.png"))
                about = AboutDialog(w.p, "0.2.0", w)
                about.adjustSize()
                about.grab().save(str(OUT / "about.png"))
                results.extend(["menu_record.png", "settings.png", "about.png"])
            w.hotkeys.unregister_all()
            w.controller.audio.shutdown()
            w.hide()
            then()

        QTimer.singleShot(3500, snap)

    def shoot_overlays() -> None:
        palette = apply_theme(app, "dark")
        from vcam.platform.windows.dpi import ScreenMapper

        mapper = ScreenMapper()
        screen = app.primaryScreen()
        phys = mapper.physical_rect(screen)
        region = Rect(phys.left + 420, phys.top + 230, 1080, 608)
        sel = RegionSelector(palette, mapper, region)
        ov = _ScreenOverlay(sel, screen)
        sel._overlays.append(ov)
        ov.place_toolbar()
        compose(desktop.scaled(ov.size()), ov.grab()).scaledToWidth(1280, Qt.TransformationMode.SmoothTransformation).save(
            str(OUT / "region_select.png"))
        guide = GuideFrame(palette, mapper)
        _s, logical = mapper.rect_to_logical(region)
        guide._region = region
        guide._inner = QRectF(logical)
        guide._relayout()
        geo = guide.geometry().translated(-screen.geometry().topLeft())
        for name, locked in (("guide_frame.png", False), ("guide_frame_rec.png", True)):
            guide.set_locked(locked, "REC 01:23" if locked else "")
            shot = compose(desktop.scaled(screen.geometry().size()), guide.grab(), geo.topLeft())
            shot.copy(geo.adjusted(-60, -40, 60, 40)).save(str(OUT / name))
        bar = RecordingBar(palette)
        bar.set_elapsed("01:23")
        bar.set_mic(True, False)
        bar.adjustSize()
        crop = QRectF(800, 900, bar.width() + 80, bar.height() + 40).toRect()
        compose(desktop, bar.grab(), QPoint(crop.x() + 40, crop.y() + 20)).copy(crop).save(str(OUT / "recording_bar.png"))
        results.extend(["region_select.png", "guide_frame.png", "guide_frame_rec.png", "recording_bar.png"])
        app.quit()

    shoot_main("dark", lambda: shoot_main("light", shoot_overlays))
    app.exec()
    print("생성:", ", ".join(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
