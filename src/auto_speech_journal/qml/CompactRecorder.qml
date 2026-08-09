import QtQuick
import QtQuick.Layouts

import "."

/*
 * The resident floating recorder: a fixed 440x190 window minus the title bar.
 *
 * Four stacked rows - status, live transcript, actions - inside a single card.
 * Every size is load bearing because the window cannot grow: the transcript
 * takes whatever the status row and the action row leave behind, and drops to
 * one line once the journal font passes the dense breakpoint.
 */
Item {
    id: root
    objectName: "compactContent"

    required property var journal
    required property string systemFontFamily
    required property color monthTint

    readonly property bool dense: journal.uiFontScale > Theme.denseFontScale

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
        anchors.leftMargin: Theme.spaceLg
        anchors.right: parent.right
        anchors.rightMargin: Theme.spaceLg
        anchors.top: parent.top
        anchors.bottom: parent.bottom

        // Live indicator on the left, backlog on the right, sharing one baseline.
        Item {
            id: statusRow
            objectName: "compactStatusRow"
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.topMargin: Theme.spaceXs
            height: Math.max(compactBacklogText.implicitHeight, 12)

            Row {
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                spacing: Theme.spaceSm

                Rectangle {
                    objectName: "compactStatusDot"
                    anchors.verticalCenter: parent.verticalCenter
                    width: 8
                    height: 8
                    radius: 4
                    color: root.journal.paused ? Theme.inkFaint : Theme.accent
                    opacity: root.journal.speechActive ? 1 : 0.45
                }

                LevelMeter {
                    objectName: "compactLevelMeter"
                    anchors.verticalCenter: parent.verticalCenter
                    width: 56
                    height: 10
                    level: root.journal.audioLevel
                    active: root.journal.speechActive
                }
            }

            Text {
                id: compactBacklogText
                objectName: "compactBacklogText"
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                text: root.journal.backlogText
                color: Theme.inkMuted
                font.family: root.systemFontFamily
                font.pixelSize: root.px(13)
                horizontalAlignment: Text.AlignRight
            }
        }

        Text {
            objectName: "compactPartialText"
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: statusRow.bottom
            anchors.topMargin: Theme.spaceSm
            anchors.bottom: compactActionRow.top
            anchors.bottomMargin: Theme.spaceMd
            text: root.journal.partialText
            wrapMode: Text.Wrap
            maximumLineCount: root.dense ? 1 : 2
            elide: Text.ElideRight
            color: Theme.ink
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
            anchors.bottomMargin: Theme.spaceMd
            height: Math.max(32, root.px(16) + 10)
            spacing: Theme.spaceSm

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
