import QtQuick
import QtQuick.Controls

import "."

/*
 * Read-only runtime status, plus the two whole-application actions.
 *
 * Wrapped in a Flickable because a bare Column here has no bottom anchor: at the
 * largest journal font in a short window the microphone notice used to run off
 * the bottom of the drawer with nothing to reveal it.
 */
Flickable {
    id: root
    objectName: "systemSheet"
    clip: true
    contentWidth: width
    contentHeight: body.implicitHeight
    boundsBehavior: Flickable.StopAtBounds

    required property var journal

    signal exitRequested()

    function px(size) {
        return Math.max(1, Math.round(size * journal.uiFontScale))
    }

    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

    Column {
        id: body
        width: root.width - 10
        spacing: Theme.spaceMd

        Text {
            width: parent.width
            text: root.journal.stateText
            wrapMode: Text.Wrap
            color: Theme.inkStrong
            font.family: root.journal.systemFontFamily
            font.pixelSize: root.px(20)
            font.weight: Font.DemiBold
        }
        Text {
            width: parent.width
            text: root.journal.statusMessage
            wrapMode: Text.Wrap
            color: Theme.inkBody
            font.family: root.journal.systemFontFamily
            font.pixelSize: root.px(16)
        }
        Rectangle { width: parent.width; height: Theme.hairline; color: Theme.line }
        Text {
            width: parent.width
            text: "場景狀態：" + root.journal.sceneKey
            wrapMode: Text.Wrap
            color: Theme.inkBody
            font.family: root.journal.systemFontFamily
            font.pixelSize: root.px(16)
        }
        Text {
            width: parent.width
            text: root.journal.backlogText
            wrapMode: Text.Wrap
            color: Theme.inkBody
            font.family: root.journal.systemFontFamily
            font.pixelSize: root.px(16)
        }
        Text {
            objectName: "systemMicrophoneStatus"
            width: parent.width
            text: "麥克風偏好：" + root.journal.preferredInputName +
                  "\n目前收音：" + (root.journal.activeInputName || "尚未開始") +
                  (root.journal.inputRouteNoticeText
                      ? "\n" + root.journal.inputRouteNoticeText
                      : "")
            wrapMode: Text.Wrap
            color: root.journal.inputFallbackActive ? Theme.warning : Theme.inkBody
            font.family: root.journal.systemFontFamily
            font.pixelSize: root.px(16)
        }
        Text {
            width: parent.width
            text: "今日資料版本：" + root.journal.timelineRevision
            wrapMode: Text.Wrap
            color: Theme.inkBody
            font.family: root.journal.systemFontFamily
            font.pixelSize: root.px(16)
        }
        PaperButton {
            width: parent.width
            text: "開啟紀錄資料夾"
            onClicked: root.journal.openRecordsFolder()
        }
        PaperButton {
            width: parent.width
            text: "結束程式"
            onClicked: root.exitRequested()
        }
    }
}
