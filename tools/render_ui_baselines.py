"""Render compact and workspace screenshots for every recorder scene state."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QSG_RHI_BACKEND", "software")

from PySide6.QtCore import QSize  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from auto_speech_journal.config import AppConfig  # noqa: E402
from auto_speech_journal.controller import ControllerSnapshot  # noqa: E402
from auto_speech_journal.timeline import (  # noqa: E402
    DayTimelineView,
    TimelineHourView,
    TimelineSegmentView,
)
from auto_speech_journal.types import SegmentState, Severity, WorkerState  # noqa: E402
from auto_speech_journal.ui import (  # noqa: E402
    COMPACT_HEIGHT,
    COMPACT_WIDTH,
    EXPANDED_HEIGHT,
    EXPANDED_WIDTH,
    _configure_application,
    _create_main_window,
)
from auto_speech_journal.ui_models import SCENE_DIRECTORY_ENV  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "artifacts" / "ui-baselines"
TAIPEI = ZoneInfo("Asia/Taipei")
STATES = (
    "starting",
    "listening",
    "capturing",
    "finalizing",
    "paused",
    "degraded",
    "error",
    "stopped",
)
GATE_SIZES = (
    ("minimum", 960, 680),
    ("default", 1180, 820),
    ("large", 1440, 960),
)
GATE_FONT_SIZES = (14, 18, 26)
LONG_SEGMENT_TEXT = (
    "傍晚回到家後，我先把窗戶推開，讓雨後的風慢慢穿過房間。"
    "桌上的杯子還留著一點溫度，今天幾次重要的對話與臨時想到的細節，"
    "都應該完整保留下來，稍後再逐句修正，而不是被截成難以閱讀的碎片。"
)


class _PreviewController:
    def __init__(self, records_root: Path) -> None:
        self.config = AppConfig(records_root=str(records_root))
        self.snapshot = ControllerSnapshot(timeline_revision=1)
        self.long_text = False

    def tick(self) -> None:
        return

    def timeline_for_date(self, day) -> DayTimelineView:
        day_key = day.isoformat()
        hour_key = f"{day_key}_09"
        segments = (
            TimelineSegmentView(
                segment_id="baseline-1",
                time_label="[09:12:06]",
                text=(
                    LONG_SEGMENT_TEXT
                    if self.long_text
                    else "今天的第一段聲跡，已經安全收進日記。"
                ),
                status_label="已定稿",
                editable=True,
                hour_key=hour_key,
                state=SegmentState.FINAL_READY,
            ),
            TimelineSegmentView(
                segment_id="baseline-2",
                time_label="[09:28:41]",
                text=(
                    "第二段內容延續到另一個較長的段落，用來確認在最大字級下，"
                    "時間、狀態、正文與修正入口仍保有清楚層級。"
                    if self.long_text
                    else "正在整理新的語音片段。"
                ),
                status_label="待定稿",
                editable=True,
                hour_key=hour_key,
                state=SegmentState.FINALIZING,
            ),
        )
        return DayTimelineView(
            day_key=day_key,
            hours=(TimelineHourView(hour_key=hour_key, label="09:00", segments=segments),),
        )

    def available_hours(self) -> list[str]:
        return ["2026-01-15_09"]

    def stop(self, *, suppress_errors: bool = False) -> None:
        return


def _snapshot(state: str, revision: int) -> ControllerSnapshot:
    common = {
        "partial_text": "我正在說的這句話會在這裡即時出現…",
        "rms_dbfs": -25.0,
        "peak_dbfs": -17.0,
        "timeline_revision": revision,
    }
    if state == "starting":
        return ControllerSnapshot(
            state=WorkerState.STARTING,
            message="正在喚醒錄音與辨識引擎",
            **common,
        )
    if state == "capturing":
        return ControllerSnapshot(
            state=WorkerState.RECORDING,
            message="聽見你了，正在即時辨識",
            speech_active=True,
            **common,
        )
    if state == "finalizing":
        return ControllerSnapshot(
            state=WorkerState.RECORDING,
            message="片段已保存，正在完成最終轉錄",
            backlog=2,
            **common,
        )
    if state == "paused":
        return ControllerSnapshot(
            state=WorkerState.PAUSED,
            message="錄音已暫停；今日紀錄仍安全保留",
            paused=True,
            **common,
        )
    if state == "degraded":
        return ControllerSnapshot(
            state=WorkerState.DEGRADED,
            severity=Severity.WARNING,
            message="錄音持續中，辨識速度暫時變慢",
            backlog=3,
            **common,
        )
    if state == "error":
        return ControllerSnapshot(
            state=WorkerState.ERROR,
            severity=Severity.ERROR,
            message="麥克風連線中斷；請檢查設備",
            last_error="麥克風連線中斷",
            **common,
        )
    if state == "stopped":
        return ControllerSnapshot(
            state=WorkerState.STOPPED,
            message="聲跡已收妥，錄音目前停止",
            partial_text="",
            rms_dbfs=-120.0,
            peak_dbfs=-120.0,
            timeline_revision=revision,
        )
    return ControllerSnapshot(
        state=WorkerState.RECORDING,
        message="持續聽寫中，所有紀錄僅儲存在這台電腦",
        **common,
    )


def _ensure_mode(view_model, *, expanded: bool) -> None:
    if view_model.expanded == expanded:
        return
    if expanded:
        view_model.toggleExpanded()
    else:
        view_model.collapseToCompact()
    QTest.qWait(120)


def _set_baseline_workspace_size(window, view_model) -> None:
    """Keep baseline output deterministic on Qt's small offscreen display."""
    size = QSize(EXPANDED_WIDTH, EXPANDED_HEIGHT)
    window.setMaximumSize(size)
    view_model._set_expanded_size(size)
    window.resize(size)
    QTest.qWait(120)


def _grab(window, view_model, destination: Path, *, expanded: bool) -> None:
    QTest.qWait(700)
    _ensure_mode(view_model, expanded=expanded)
    QTest.qWait(150)
    image = window.grabWindow()
    expected = (
        (view_model.expandedWidth, view_model.expandedHeight)
        if expanded
        else (COMPACT_WIDTH, COMPACT_HEIGHT)
    )
    if (image.width(), image.height()) != expected:
        raise RuntimeError(
            f"unexpected capture size for {destination}: "
            f"{image.width()}x{image.height()} != {expected[0]}x{expected[1]}"
        )
    # Frameless windows produce an alpha PNG. Some Qt 6.11/software-renderer
    # consumers display opaque tiles from that image as black, so flatten only
    # the visual-baseline artifact over the application's paper color.
    flattened = QImage(image.size(), QImage.Format.Format_RGB32)
    flattened.fill(QColor("#F4EEE3"))
    painter = QPainter(flattened)
    painter.drawImage(0, 0, image)
    painter.end()
    if image.isNull() or not flattened.save(str(destination), "PNG"):
        raise RuntimeError(f"unable to capture {destination}")


def render_baselines(output_dir: Path, *, month: int) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    application = QApplication.instance() or QApplication([])
    _configure_application(application)
    controller = _PreviewController(output_dir / "records")
    window = _create_main_window(controller, application)
    view_model = window._journal_view_model
    fixed_now = datetime(2026, month, 15, 10, 30, tzinfo=TAIPEI)
    view_model._clock = lambda: fixed_now
    window.show()
    QTest.qWait(100)

    outputs: list[Path] = []
    try:
        for revision, state in enumerate(STATES, start=2):
            controller.snapshot = _snapshot(state, revision)
            view_model._scene_changed_at = view_model._monotonic() - 3.0
            view_model.refresh(force_timeline=True)

            compact = output_dir / f"compact-{state}.png"
            _grab(window, view_model, compact, expanded=False)
            outputs.append(compact)

        _ensure_mode(view_model, expanded=True)
        _set_baseline_workspace_size(window, view_model)
        QTest.qWait(200)
        for revision, state in enumerate(STATES, start=20):
            controller.snapshot = _snapshot(state, revision)
            view_model._scene_changed_at = view_model._monotonic() - 3.0
            view_model.refresh(force_timeline=True)

            workspace = output_dir / f"workspace-{state}.png"
            _grab(window, view_model, workspace, expanded=True)
            outputs.append(workspace)
    finally:
        view_model._poll_timer.stop()
        view_model._allow_close = True
        window.close()
    return outputs


def render_gate_matrix(output_dir: Path, *, month: int) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    application = QApplication.instance() or QApplication([])
    _configure_application(application)
    controller = _PreviewController(output_dir / "records")
    controller.long_text = True
    window = _create_main_window(controller, application)
    view_model = window._journal_view_model
    fixed_now = datetime(2026, month, 15, 10, 30, tzinfo=TAIPEI)
    view_model._clock = lambda: fixed_now
    controller.snapshot = _snapshot("listening", 100)
    view_model._scene_changed_at = view_model._monotonic() - 3.0
    view_model.refresh(force_timeline=True)
    window.show()
    QTest.qWait(100)

    outputs: list[Path] = []
    report: list[dict[str, object]] = []
    try:
        for font_size in GATE_FONT_SIZES:
            view_model._set_runtime_appearance(view_model.uiFontFamily, font_size)
            destination = output_dir / f"gate-compact-{font_size}px.png"
            _grab(window, view_model, destination, expanded=False)
            outputs.append(destination)
            report.append(
                {
                    "mode": "compact",
                    "font_px": font_size,
                    "width": COMPACT_WIDTH,
                    "height": COMPACT_HEIGHT,
                    "file": destination.name,
                }
            )

        _ensure_mode(view_model, expanded=True)
        for size_name, requested_width, requested_height in GATE_SIZES:
            screen = window.screen() or application.primaryScreen()
            available = (
                screen.availableGeometry().size()
                if screen is not None
                else QSize(requested_width, requested_height)
            )
            bounded = view_model._clamp_expanded_size(
                QSize(requested_width, requested_height), available
            )
            view_model._set_expanded_size(bounded)
            window.resize(bounded)
            QTest.qWait(120)
            for font_size in GATE_FONT_SIZES:
                view_model._set_runtime_appearance(view_model.uiFontFamily, font_size)
                destination = output_dir / f"gate-workspace-{size_name}-{font_size}px.png"
                _grab(window, view_model, destination, expanded=True)
                outputs.append(destination)
                report.append(
                    {
                        "mode": "workspace",
                        "size": size_name,
                        "font_px": font_size,
                        "width": bounded.width(),
                        "height": bounded.height(),
                        "file": destination.name,
                    }
                )
    finally:
        view_model._poll_timer.stop()
        view_model._allow_close = True
        window.close()

    report_path = output_dir / "gate-matrix.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return outputs


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--month", type=int, choices=range(1, 13), default=1)
    parser.add_argument(
        "--scene-dir",
        type=Path,
        help="optional v2 prototype scene root used through the runtime override",
    )
    parser.add_argument(
        "--gate-matrix",
        action="store_true",
        help="also render compact font and workspace size/font/long-text gate cases",
    )
    parser.add_argument(
        "--gate-matrix-only",
        action="store_true",
        help="render only the size/font/long-text gate cases",
    )
    args = parser.parse_args(argv)
    if args.scene_dir is not None:
        os.environ[SCENE_DIRECTORY_ENV] = str(args.scene_dir.resolve())
    outputs = (
        []
        if args.gate_matrix_only
        else render_baselines(args.output_dir, month=args.month)
    )
    if args.gate_matrix or args.gate_matrix_only:
        outputs.extend(render_gate_matrix(args.output_dir, month=args.month))
    print(f"Rendered {len(outputs)} UI baselines under {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
