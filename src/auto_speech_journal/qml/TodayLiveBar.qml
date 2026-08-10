pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "."

/*
 * The fixed control strip under the title bar.
 *
 * This used to be a 146px floating card that also carried the date, which left
 * the workspace top-heavy: status, date and live transcript crowded the first
 * fifth of the window while the rest sat empty. The date now lives in the title
 * bar, so this is one 64px row of controls plus a live row that only exists
 * while there is a partial transcript to show.
 */
Item {
    id: root
    objectName: "todayLiveBar"

    required property var journal
    required property string systemFontFamily
    required property string diaryFontFamily
    property real fontScale: 1
    property color accentColor: Theme.accent
    property bool motionEnabled: true

    signal openSheet(string sheetKey)

    readonly property bool hasPartial: journal.hasPartialText
    readonly property bool dense: fontScale > Theme.denseFontScale
    readonly property real controlRowHeight: Math.max(64, root.px(18) + 40)
    readonly property real liveRowHeight: dense ? 62 : 44

    function px(size) {
        return Math.max(1, Math.round(size * fontScale))
    }

    implicitHeight: controlRowHeight + (hasPartial ? liveRowHeight : 0)

    Behavior on implicitHeight {
        enabled: root.motionEnabled
        NumberAnimation { duration: 260; easing.type: Easing.OutCubic }
    }

    /* The one action of the bar: start or stop listening. */
    component PrimaryButton: Button {
        id: primary
        readonly property bool contentWidthGuard: true
        implicitHeight: 36
        implicitWidth: Math.max(96, primaryLabel.implicitWidth + 32)
        font.family: root.systemFontFamily
        font.pixelSize: root.px(14)

        contentItem: Text {
            id: primaryLabel
            text: primary.text
            color: primary.enabled ? Theme.paperRaised : Theme.inkFaint
            font: primary.font
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: Theme.radiusSm
            color: !primary.enabled ? Theme.paperSunken
                   : primary.down ? Theme.accentDeep : Theme.accent
        }
    }

    /* Ways into the drawer. These are navigation, not actions, so they carry no
     * chrome at all - four outlined buttons beside a filled one read as five
     * equal choices and flattened the whole bar. */
    component ToolButton: Button {
        id: tool
        readonly property bool contentWidthGuard: true
        implicitHeight: 30
        implicitWidth: Math.max(46, toolLabel.implicitWidth + leftPadding + rightPadding)
        leftPadding: root.dense ? 5 : 9
        rightPadding: leftPadding
        flat: true
        font.family: root.systemFontFamily
        font.pixelSize: root.px(13)

        contentItem: Text {
            id: toolLabel
            text: tool.text
            color: tool.down ? Theme.ink : Theme.inkMuted
            font: tool.font
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: Theme.radiusSm
            color: tool.down ? Theme.wash(Theme.ink, 0.10)
                   : tool.hovered ? Theme.wash(Theme.ink, 0.05) : "transparent"
        }
    }

    Rectangle {
        anchors.fill: parent
        color: Theme.paperRaised
        radius: Theme.radiusMd
        border.width: Theme.hairline
        border.color: Theme.line
    }

    // -- control row ---------------------------------------------------------
    Item {
        id: controlRow
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: root.controlRowHeight

        Row {
            id: statusGroup
            anchors.left: parent.left
            anchors.leftMargin: Theme.spaceLg
            anchors.verticalCenter: parent.verticalCenter
            spacing: Theme.spaceSm

            // The one place sage means "audio is arriving right now".
            Rectangle {
                objectName: "workspaceStatusDot"
                anchors.verticalCenter: parent.verticalCenter
                width: 9
                height: 9
                radius: 5
                color: root.journal.paused ? Theme.inkFaint : root.accentColor
                opacity: root.journal.speechActive ? 1 : 0.45

                Behavior on opacity {
                    enabled: root.motionEnabled
                    NumberAnimation { duration: 220; easing.type: Easing.InOutSine }
                }
            }

            Text {
                objectName: "workspaceStateText"
                anchors.verticalCenter: parent.verticalCenter
                text: root.journal.stateText
                color: Theme.ink
                font.family: root.systemFontFamily
                font.pixelSize: root.px(18)
                font.weight: Font.DemiBold
            }

            LevelMeter {
                objectName: "workspaceLevelMeter"
                anchors.verticalCenter: parent.verticalCenter
                visible: !root.dense
                width: 64
                height: 12
                level: root.journal.audioLevel
                active: root.journal.speechActive
                accentColor: root.accentColor
                motionEnabled: root.motionEnabled
            }

            Text {
                objectName: "workspaceBacklogText"
                anchors.verticalCenter: parent.verticalCenter
                text: root.journal.backlogText
                color: Theme.inkMuted
                font.family: root.systemFontFamily
                font.pixelSize: root.px(15)
            }
        }

        RowLayout {
            id: actionRow
            objectName: "secondaryActionRow"
            anchors.right: parent.right
            anchors.rightMargin: Theme.spaceLg
            anchors.verticalCenter: parent.verticalCenter
            spacing: 0

            ToolButton {
                objectName: "settingsButton"
                text: "設定"
                onClicked: root.openSheet("settings")
            }
            ToolButton {
                objectName: "systemButton"
                text: "狀態"
                onClicked: root.openSheet("system")
            }
            ToolButton {
                objectName: "vocabularyButton"
                text: "字典"
                onClicked: root.openSheet("vocabulary")
            }
            ToolButton {
                objectName: "hoursButton"
                text: "時段"
                onClicked: root.openSheet("hours")
            }

            Rectangle {
                Layout.leftMargin: Theme.spaceMd
                Layout.rightMargin: Theme.spaceMd
                Layout.preferredWidth: Math.max(1, Theme.hairline)
                Layout.preferredHeight: 20
                Layout.alignment: Qt.AlignVCenter
                color: Theme.line
            }

            Item {
                objectName: "primaryActionRow"
                Layout.preferredWidth: pauseButton.implicitWidth
                Layout.preferredHeight: pauseButton.implicitHeight

                PrimaryButton {
                    id: pauseButton
                    objectName: "expandedPauseButton"
                    anchors.fill: parent
                    enabled: root.journal.recordingControlsEnabled
                    text: root.journal.paused ? "繼續聆聽" : "暫停錄音"
                    onClicked: root.journal.togglePause()
                }
            }
        }
    }

    // -- live row ------------------------------------------------------------
    Item {
        id: liveRow
        objectName: "workspaceLiveTrace"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: controlRow.bottom
        height: root.hasPartial ? root.liveRowHeight : 0
        visible: height > 0
        clip: true

        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            height: Theme.hairline
            color: Theme.lineSoft
        }

        Text {
            id: liveLabel
            objectName: "workspaceLiveLabel"
            anchors.left: parent.left
            anchors.leftMargin: Theme.spaceLg
            anchors.top: parent.top
            anchors.topMargin: Theme.spaceMd
            text: root.hasPartial ? "即時文字，尚未保存" : "即時預覽"
            color: Theme.inkMuted
            font.family: root.systemFontFamily
            font.pixelSize: root.px(13)
            font.weight: Font.DemiBold
        }

        Text {
            objectName: "workspacePartialText"
            anchors.left: root.dense ? parent.left : liveLabel.right
            anchors.leftMargin: root.dense ? Theme.spaceLg : Theme.spaceMd
            anchors.right: parent.right
            anchors.rightMargin: Theme.spaceLg
            y: root.dense
               ? liveLabel.y + liveLabel.height + Theme.spaceXs
               : liveLabel.y - Math.round((height - liveLabel.height) / 2)
            text: root.hasPartial ? root.journal.partialText : "等待下一段聲音…"
            wrapMode: Text.Wrap
            maximumLineCount: 1
            elide: Text.ElideRight
            color: Theme.ink
            font.family: root.systemFontFamily
            font.pixelSize: root.px(16)
        }
    }
}
