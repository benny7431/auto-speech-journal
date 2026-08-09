import QtQuick

Item {
    id: root
    objectName: "todayWorkspace"

    required property var journal
    required property color monthTint
    required property string systemFontFamily
    required property string diaryFontFamily
    property real fontScale: 1
    property real topInset: 50
    property bool motionEnabled: true

    property alias timelineList: soundRiver.listView
    property alias newSegmentsIndicator: soundRiver.newSegmentsIndicator

    signal openSheet(string sheetKey)

    Rectangle {
        id: workspaceCanvas
        objectName: "paperSpread"
        anchors.fill: parent
        color: "#F4EEE3"
        border.width: 0
        clip: true

        // A single low-opacity month tint is the whole seasonal signal now that
        // the scene photographs are gone. It sits under the content rather than
        // over it, so no compensating paper wash is needed to keep text legible.
        Rectangle {
            anchors.fill: parent
            color: root.monthTint
            opacity: 0.05
        }

        TodayLiveBar {
            id: liveBar
            anchors.left: parent.left
            anchors.leftMargin: 22
            anchors.right: parent.right
            anchors.rightMargin: 22
            anchors.top: parent.top
            anchors.topMargin: root.topInset + 12
            height: implicitHeight
            journal: root.journal
            systemFontFamily: root.systemFontFamily
            diaryFontFamily: root.diaryFontFamily
            fontScale: root.fontScale
            accentColor: root.monthTint
            motionEnabled: root.motionEnabled
            onOpenSheet: function(sheetKey) { root.openSheet(sheetKey) }
        }

        SoundRiver {
            id: soundRiver
            anchors.left: parent.left
            anchors.leftMargin: 30
            anchors.right: parent.right
            anchors.rightMargin: 26
            anchors.top: liveBar.bottom
            anchors.topMargin: 13
            anchors.bottom: parent.bottom
            anchors.bottomMargin: 18
            journal: root.journal
            systemFontFamily: root.systemFontFamily
            diaryFontFamily: root.diaryFontFamily
            fontScale: root.fontScale
            accentColor: root.monthTint
        }
    }
}
