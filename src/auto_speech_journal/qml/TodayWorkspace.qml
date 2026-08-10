import QtQuick

import "."

/*
 * The expanded journal: a control strip over a scrolling list of segment cards.
 *
 * The page is deliberately plain. The month tint is the only decoration and it
 * sits under everything at 5% so the cards, not the background, carry the eye.
 */
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
        color: Theme.paper
        border.width: 0
        clip: true

        Rectangle {
            anchors.fill: parent
            color: root.monthTint
            opacity: 0.05
        }

        // The control bar shares the timeline's measure so the workspace reads as
        // one column rather than a full-width toolbar over a narrow page.
        readonly property real columnWidth: Math.min(
            width - 2 * Theme.spaceXl, Theme.readingMeasure
        )

        TodayLiveBar {
            id: liveBar
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: parent.top
            anchors.topMargin: root.topInset + Theme.spaceSm
            width: workspaceCanvas.columnWidth
            height: implicitHeight
            journal: root.journal
            systemFontFamily: root.systemFontFamily
            diaryFontFamily: root.diaryFontFamily
            fontScale: root.fontScale
            motionEnabled: root.motionEnabled
            onOpenSheet: function(sheetKey) { root.openSheet(sheetKey) }
        }

        SoundRiver {
            id: soundRiver
            anchors.horizontalCenter: parent.horizontalCenter
            width: workspaceCanvas.columnWidth
            anchors.top: liveBar.bottom
            anchors.topMargin: Theme.spaceXl
            anchors.bottom: parent.bottom
            anchors.bottomMargin: Theme.spaceLg
            journal: root.journal
            systemFontFamily: root.systemFontFamily
            diaryFontFamily: root.diaryFontFamily
            fontScale: root.fontScale
            accentColor: root.monthTint
        }
    }
}
