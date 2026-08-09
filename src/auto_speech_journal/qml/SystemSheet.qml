import QtQuick

Column {
    id: root
    objectName: "systemSheet"
    spacing: 13

    required property var journal

    signal exitRequested()

    function px(size) {
        return Math.max(1, Math.round(size * journal.uiFontScale))
    }


    Text { text: journal.stateText; color: "#493F35"; font.family: journal.systemFontFamily; font.pixelSize: root.px(22) }
    Text { width: parent.width; text: journal.statusMessage; wrapMode: Text.Wrap; color: "#6F6255"; font.family: journal.systemFontFamily; font.pixelSize: root.px(16) }
    Rectangle { width: parent.width; height: 1; color: "#D8CBBA" }
    Text { text: "場景狀態：" + journal.sceneKey; color: "#6F6255"; font.family: journal.systemFontFamily; font.pixelSize: root.px(16) }
    Text { text: journal.backlogText; color: "#6F6255"; font.family: journal.systemFontFamily; font.pixelSize: root.px(16) }
    Text {
        objectName: "systemMicrophoneStatus"
        text: "麥克風偏好：" + journal.preferredInputName +
              "\n目前收音：" + (journal.activeInputName || "尚未開始") +
              (journal.inputRouteNoticeText
                  ? "\n" + journal.inputRouteNoticeText
                  : "")
        width: parent.width
        wrapMode: Text.Wrap
        color: journal.inputFallbackActive ? "#9A642E" : "#6F6255"
        font.family: journal.systemFontFamily
        font.pixelSize: root.px(16)
    }
    Text { text: "今日資料版本：" + journal.timelineRevision; color: "#6F6255"; font.family: journal.systemFontFamily; font.pixelSize: root.px(16) }
    PaperButton { width: parent.width; text: "開啟紀錄資料夾"; onClicked: journal.openRecordsFolder() }
    PaperButton { width: parent.width; text: "結束程式"; onClicked: root.exitRequested() }
}
