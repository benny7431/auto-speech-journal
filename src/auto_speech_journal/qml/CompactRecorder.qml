import QtQuick
import QtQuick.Layouts

/*
 * The resident floating recorder: a fixed 440x190 window minus the title bar.
 *
 * Every size here is load bearing because the window cannot grow. The partial
 * transcript takes whatever vertical space the backlog line and the action row
 * leave behind, and drops to a single line once the journal font passes the
 * dense breakpoint.
 */
Item {
    id: root
    objectName: "compactContent"

    required property var journal
    required property string systemFontFamily
    required property color monthTint

    function px(size) {
        return Math.max(1, Math.round(size * journal.uiFontScale))
    }

    Rectangle {
        anchors.fill: parent
        color: root.monthTint
        opacity: 0.05
    }

    Item {
        id: compactInfo
        objectName: "compactInfo"
        z: 2
        anchors.left: parent.left
        anchors.leftMargin: 14
        anchors.right: parent.right
        anchors.rightMargin: 14
        anchors.top: parent.top
        anchors.bottom: parent.bottom

        Text {
            id: compactBacklogText
            objectName: "compactBacklogText"
            anchors.top: parent.top
            anchors.topMargin: 5
            anchors.right: parent.right
            text: root.journal.backlogText
            color: "#776B5E"
            font.family: root.systemFontFamily
            font.pixelSize: root.px(13)
            horizontalAlignment: Text.AlignRight
        }

        Text {
            objectName: "compactPartialText"
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: compactBacklogText.bottom
            anchors.topMargin: 6
            anchors.bottom: compactActionRow.top
            anchors.bottomMargin: 12
            text: root.journal.partialText
            wrapMode: Text.Wrap
            maximumLineCount: root.journal.uiFontScale > 1.4 ? 1 : 2
            elide: Text.ElideRight
            color: "#3E3831"
            font.family: root.systemFontFamily
            font.pixelSize: root.px(15)
            lineHeight: 1.18
            verticalAlignment: Text.AlignTop
        }

        RowLayout {
            id: compactActionRow
            objectName: "compactActionRow"
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.bottomMargin: 9
            height: Math.max(32, root.px(16) + 10)
            spacing: 8

            PaperButton {
                objectName: "pauseButton"
                Layout.fillWidth: true
                Layout.fillHeight: true
                enabled: root.journal.recordingControlsEnabled
                text: root.journal.paused ? "繼續聆聽" : "暫停"
                onClicked: root.journal.togglePause()
            }
            PaperButton {
                objectName: "expandButton"
                Layout.fillHeight: true
                text: "今日紀錄"
                onClicked: root.journal.toggleExpanded()
            }
        }
    }
}
