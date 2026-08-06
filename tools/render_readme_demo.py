"""Render the README demo from production QML and synthetic in-memory state."""

from __future__ import annotations

import argparse
import io
import math
import os
import sys
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QSG_RHI_BACKEND", "software")

from PIL import Image  # noqa: E402
from PySide6.QtCore import QBuffer, QIODevice, QSettings  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor,
    QFont,
    QFontDatabase,
    QImage,
    QPainter,
    QRawFont,
)
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from auto_speech_journal.config import (  # noqa: E402
    AppConfig,
    MicrophoneMode,
    MicrophoneSelection,
)
from auto_speech_journal.controller import ControllerSnapshot  # noqa: E402
from auto_speech_journal.timeline import (  # noqa: E402
    DayTimelineView,
    TimelineHourView,
    TimelineSegmentView,
)
from auto_speech_journal.types import (  # noqa: E402
    InputRoute,
    SegmentState,
    WorkerState,
)
from auto_speech_journal.ui import (  # noqa: E402
    _configure_application,
    _create_main_window,
)

TAIPEI = ZoneInfo("Asia/Taipei")
DEMO_NOW = datetime(2026, 8, 6, 9, 32, tzinfo=TAIPEI)
DEFAULT_OUTPUT = ROOT / "docs" / "images" / "auto-speech-journal-demo.gif"
MAX_GIF_BYTES = 8 * 1024**2


def _load_offscreen_cjk_font() -> tuple[str | None, int | None]:
    """Load an OS font explicitly because Qt's Windows offscreen plugin does not."""
    fonts_root = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    candidates = (
        fonts_root / "msjh.ttc",
        fonts_root / "msyh.ttc",
        fonts_root / "mingliu.ttc",
        fonts_root / "simsun.ttc",
    )
    for candidate in candidates:
        if not candidate.is_file():
            continue
        font_id = QFontDatabase.addApplicationFont(str(candidate))
        if font_id < 0:
            continue
        families = tuple(QFontDatabase.applicationFontFamilies(font_id))
        ordered = sorted(
            families,
            key=lambda family: ("JhengHei UI" not in family, "JhengHei" not in family),
        )
        for family in ordered:
            raw_font = QRawFont.fromFont(QFont(family))
            if raw_font.isValid() and raw_font.supportsCharacter("聲"):
                return family, font_id
        QFontDatabase.removeApplicationFont(font_id)
    return None, None


class SyntheticDemoController:
    """Controller-shaped, memory-only state for the public documentation demo."""

    def __init__(self) -> None:
        self.config = AppConfig(
            microphone=MicrophoneSelection(mode=MicrophoneMode.SYSTEM_DEFAULT),
            records_root="C:/AutoSpeechJournalSynthetic/Records",
        )
        self.snapshot = ControllerSnapshot()
        self.segments: tuple[TimelineSegmentView, ...] = ()
        self.set_frame(0, 48)

    def set_frame(self, frame_index: int, frame_count: int) -> None:
        phase = min(3, frame_index * 4 // max(1, frame_count))
        phase_progress = (frame_index * 4 % max(1, frame_count)) / max(1, frame_count)
        pulse = math.sin(frame_index * math.tau / 8)
        day_key = DEMO_NOW.date().isoformat()
        hour_key = f"{day_key}_09"
        first = TimelineSegmentView(
            segment_id="demo-1",
            time_label="[09:12:06]",
            text="早上先整理今天的優先順序，所有內容只留在自己的電腦。",
            status_label="Markdown 已同步",
            editable=True,
            hour_key=hour_key,
            state=SegmentState.AUDIO_DELETED,
        )
        draft = "把會議重點整理成三個可執行步驟，下午逐一完成。"
        previews = (
            "把會議重點",
            "把會議重點整理成三個",
            "把會議重點整理成三個可執行步驟",
            draft,
        )

        if phase == 0:
            message = "正在錄音 · 音訊安全寫入本機 FLAC spool"
            partial_text = "聽見你了，正在本機辨識…"
            speech_active = True
            backlog = 0
            self.segments = (first,)
        elif phase == 1:
            message = "即時預覽 · 不上傳音訊或逐字稿"
            partial_text = previews[min(3, int(phase_progress * 4))]
            speech_active = True
            backlog = 0
            self.segments = (first,)
        elif phase == 2:
            message = "人工修正已保存 · 模型結果不會覆蓋"
            partial_text = ""
            speech_active = False
            backlog = 1
            corrected = TimelineSegmentView(
                segment_id="demo-2",
                time_label="[09:31:42]",
                text=draft,
                status_label="已修正",
                editable=True,
                hour_key=hour_key,
                state=SegmentState.FINAL_READY,
            )
            self.segments = (first, corrected)
        else:
            message = "Markdown 已同步 · 暫存 FLAC 已安全清理"
            partial_text = ""
            speech_active = False
            backlog = 0
            synced = TimelineSegmentView(
                segment_id="demo-2",
                time_label="[09:31:42]",
                text=draft,
                status_label="Markdown 已同步",
                editable=True,
                hour_key=hour_key,
                state=SegmentState.AUDIO_DELETED,
            )
            self.segments = (first, synced)

        self.snapshot = ControllerSnapshot(
            state=WorkerState.RECORDING,
            message=message,
            partial_text=partial_text,
            backlog=backlog,
            rms_dbfs=-28.0 + pulse * 5.0 if speech_active else -58.0,
            peak_dbfs=-18.0 + pulse * 3.0 if speech_active else -52.0,
            speech_active=speech_active,
            timeline_revision=frame_index + 1,
            preferred_input_name="Windows 預設麥克風",
            active_input_name="Windows 預設麥克風",
            input_route=InputRoute.SYSTEM_DEFAULT,
            preferred_input_available=True,
        )

    def tick(self) -> None:
        return

    def timeline_for_date(self, day) -> DayTimelineView:
        day_key = day.isoformat()
        hour_key = f"{day_key}_09"
        return DayTimelineView(
            day_key=day_key,
            hours=(
                TimelineHourView(
                    hour_key=hour_key,
                    label="09:00",
                    segments=self.segments,
                ),
            ),
        )

    def toggle_pause(self) -> None:
        return

    def correct_segment(self, segment_id: str, text: str) -> None:
        self.segments = tuple(
            replace(segment, text=text, status_label="已修正")
            if segment.segment_id == segment_id
            else segment
            for segment in self.segments
        )

    def available_hours(self) -> list[str]:
        return ["2026-08-06_09"]

    def delete_hour(self, hour_key: str) -> None:
        return

    def open_records_folder(self) -> None:
        return

    def open_settings_history_file(self) -> None:
        return

    def update_settings(self, config: AppConfig) -> None:
        self.config = config

    def configure_microphone(self, selection: MicrophoneSelection) -> None:
        self.config = replace(self.config, microphone=selection)

    def skip_microphone_setup(self) -> None:
        return

    def retry_preferred_input(self) -> None:
        return

    def start(self) -> None:
        return

    def learned_vocabulary(self) -> dict[str, int]:
        return {"聲跡日記": 3, "本機辨識": 2}

    def delete_vocabulary_term(self, term: str) -> bool:
        return False

    def clear_vocabulary(self) -> int:
        return 0

    def set_vocabulary_learning_enabled(self, enabled: bool) -> None:
        return

    def report_ui_error(self, message: str) -> None:
        return

    def stop(self, *, suppress_errors: bool = False) -> None:
        return


def _capture_frame(window) -> Image.Image:
    image = window.grabWindow()
    if image.isNull():
        raise RuntimeError("Qt returned an empty README demo frame")

    flattened = QImage(image.size(), QImage.Format.Format_RGB32)
    flattened.fill(QColor("#F4EEE3"))
    painter = QPainter(flattened)
    painter.drawImage(0, 0, image)
    painter.end()

    buffer = QBuffer()
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not flattened.save(buffer, "PNG"):
        raise RuntimeError("Unable to encode a README demo frame")
    return Image.open(io.BytesIO(bytes(buffer.data()))).convert("RGB")


def _write_gif(
    frames: Sequence[Image.Image],
    destination: Path,
    *,
    duration_ms: int,
    max_width: int,
) -> None:
    attempts = (
        (max_width, 128),
        (max(1, round(max_width * 0.88)), 96),
        (max(1, round(max_width * 0.75)), 64),
        (max(1, round(max_width * 0.62)), 48),
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    for width, colors in attempts:
        encoded: list[Image.Image] = []
        for frame in frames:
            rendered = frame.copy()
            if rendered.width > width:
                height = max(1, round(rendered.height * width / rendered.width))
                rendered = rendered.resize((width, height), Image.Resampling.LANCZOS)
            encoded.append(
                rendered.quantize(
                    colors=colors,
                    method=Image.Quantize.MEDIANCUT,
                    dither=Image.Dither.NONE,
                )
            )
        encoded[0].save(
            destination,
            format="GIF",
            save_all=True,
            append_images=encoded[1:],
            duration=duration_ms,
            loop=0,
            disposal=2,
            optimize=False,
        )
        if destination.stat().st_size <= MAX_GIF_BYTES:
            return
    raise RuntimeError(f"README demo exceeds 8 MiB after compression: {destination}")


def render_demo(
    output: Path = DEFAULT_OUTPUT,
    *,
    frame_count: int = 48,
    duration_ms: int = 125,
    max_width: int = 900,
) -> Path:
    """Render a roughly six-second GIF without touching runtime data or devices."""
    if frame_count < 4:
        raise ValueError("frame_count must be at least 4")
    if duration_ms <= 0:
        raise ValueError("duration_ms must be positive")
    if not 1 <= max_width <= 960:
        raise ValueError("max_width must be between 1 and 960")

    application = QApplication.instance() or QApplication([])
    original_font = QFont(application.font())
    demo_font_family, demo_font_id = _load_offscreen_cjk_font()
    try:
        _configure_application(application)
        if demo_font_family is not None:
            application.setFont(QFont(demo_font_family))
        controller = SyntheticDemoController()
        with TemporaryDirectory(prefix="auto-speech-journal-demo-") as temporary:
            temporary_root = Path(temporary)
            settings = QSettings(
                str(temporary_root / "window.ini"),
                QSettings.Format.IniFormat,
            )
            settings.setValue("expandedWidth", 960)
            settings.setValue("expandedHeight", 680)
            window = _create_main_window(
                controller,
                application,
                window_settings=settings,
                microphone_device_provider=lambda: (),
                clock=lambda: DEMO_NOW,
                font_directories=(temporary_root / "synthetic-fonts",),
            )
            view_model = window._journal_view_model
            frames: list[Image.Image] = []
            try:
                window.show()
                QTest.qWait(300)
                view_model.toggleExpanded()
                QTest.qWait(900)
                for frame_index in range(frame_count):
                    controller.set_frame(frame_index, frame_count)
                    view_model._scene_changed_at = view_model._monotonic() - 3.0
                    view_model.refresh(force_timeline=True)
                    QTest.qWait(20)
                    frames.append(_capture_frame(window))
            finally:
                view_model._poll_timer.stop()
                view_model._allow_close = True
                window.close()
                window.deleteLater()
                application.processEvents()

        _write_gif(frames, output, duration_ms=duration_ms, max_width=max_width)
        return output
    finally:
        application.setFont(original_font)
        if demo_font_id is not None:
            QFontDatabase.removeApplicationFont(demo_font_id)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    output = render_demo(args.output)
    size_mib = output.stat().st_size / 1024**2
    print(f"Rendered {output} ({size_mib:.2f} MiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
