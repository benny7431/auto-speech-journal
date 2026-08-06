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

        SceneArt {
            id: workspaceArt
            objectName: "workspaceScene"
            anchors.fill: parent
            sceneSource: root.journal.workspaceSceneSource
            sceneKey: root.journal.sceneKey
            cropMode: true
            stabilizeStates: false
            paperColor: workspaceCanvas.color
            monthTint: root.monthTint
            featherRight: false
            featherBottom: false
            reducedMotion: root.journal.reducedMotion
            motionEnabled: root.motionEnabled
        }

        Rectangle {
            anchors.fill: parent
            gradient: Gradient {
                GradientStop { position: 0; color: "#B8F8F2E8" }
                GradientStop { position: 0.25; color: "#58F8F2E8" }
                GradientStop { position: 0.66; color: "#38F8F2E8" }
                GradientStop { position: 1; color: "#78F8F2E8" }
            }
        }

        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            height: root.topInset + 52
            gradient: Gradient {
                GradientStop { position: 0; color: "#EDF8F2E8" }
                GradientStop { position: 0.68; color: "#99F8F2E8" }
                GradientStop { position: 1; color: "#00F8F2E8" }
            }
        }

        AmbientSoundRiver {
            anchors.fill: parent
            anchors.topMargin: root.topInset + 10
            sceneKey: root.journal.sceneKey
            speechActive: root.journal.speechActive
            audioLevel: root.journal.audioLevel
            motionEnabled: root.motionEnabled
            reducedMotion: root.journal.reducedMotion
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
