import QtQuick
import QtQuick.Controls

Column {
    id: root
    objectName: "hoursSheet"
    spacing: 13

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


    Text {
        width: parent.width
        text: "永久刪除會同步清除 SQLite、重建輸出，並移除仍存在的暫存音訊。"
        wrapMode: Text.Wrap
        color: "#75685B"
        font.family: journal.systemFontFamily
        font.pixelSize: root.px(15)
    }
    ComboBox {
        id: hourPicker
        objectName: "hourPicker"
        width: parent.width
        model: root.hourOptions
        font.family: journal.systemFontFamily
        font.pixelSize: root.px(16)
    }
    Text {
        visible: root.hourOptions.length === 0
        text: "目前沒有可刪除的歷史時段"
        color: "#95887A"
        font.family: journal.systemFontFamily
        font.pixelSize: root.px(16)
    }
    PaperButton {
        width: parent.width
        enabled: root.hourOptions.length > 0
        text: "永久刪除選取時段"
        onClicked: root.deleteHourRequested(hourPicker.currentText)
    }
}
