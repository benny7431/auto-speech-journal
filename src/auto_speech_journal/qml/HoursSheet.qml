import QtQuick
import QtQuick.Controls

import "."

/*
 * Permanent deletion of a past hour. Wrapped in a Flickable for the same reason
 * as SystemSheet: a bare Column here has no bottom anchor and would run off the
 * drawer at the largest journal font.
 */
Flickable {
    id: root
    objectName: "hoursSheet"
    clip: true
    contentWidth: width
    contentHeight: body.implicitHeight
    boundsBehavior: Flickable.StopAtBounds

    required property var journal

    property var hourOptions: []

    signal deleteHourRequested(string hourKey)

    function reload() {
        hourOptions = journal.availableHours()
        hourPicker.currentIndex = hourOptions.length > 0 ? 0 : -1
    }

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
            text: "永久刪除會同步清除 SQLite、重建輸出，並移除仍存在的暫存音訊。"
            wrapMode: Text.Wrap
            color: Theme.inkMuted
            font.family: root.journal.systemFontFamily
            font.pixelSize: root.px(15)
        }
        ComboBox {
            id: hourPicker
            objectName: "hourPicker"
            width: parent.width
            model: root.hourOptions
            font.family: root.journal.systemFontFamily
            font.pixelSize: root.px(16)
        }
        Text {
            visible: root.hourOptions.length === 0
            width: parent.width
            wrapMode: Text.Wrap
            text: "目前沒有可刪除的歷史時段"
            color: Theme.inkFaint
            font.family: root.journal.systemFontFamily
            font.pixelSize: root.px(16)
        }
        PaperButton {
            width: parent.width
            enabled: root.hourOptions.length > 0
            text: "永久刪除選取時段"
            onClicked: root.deleteHourRequested(hourPicker.currentText)
        }
    }
}
