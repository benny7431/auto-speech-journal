pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root
    objectName: "todayLiveBar"

    required property var journal
    required property string systemFontFamily
    required property string diaryFontFamily
    property real fontScale: 1
    property color accentColor: "#718A78"
    property bool motionEnabled: true

    signal openSheet(string sheetKey)

    readonly property bool hasPartial: journal.hasPartialText
    function px(size) {
        return Math.max(1, Math.round(size * fontScale))
    }

    implicitHeight: (hasPartial ? 168 : 146) + (fontScale > 1.4 ? 36 : 0)

    Behavior on implicitHeight {
        enabled: root.motionEnabled
        NumberAnimation { duration: 260; easing.type: Easing.OutCubic }
    }

    component LiveButton: Button {
        id: control
        property bool emphasized: false
        implicitHeight: 34
        implicitWidth: Math.max(72, label.implicitWidth + 24)
        leftPadding: 12
        rightPadding: 12
        flat: !emphasized
        font.family: root.systemFontFamily
        font.pixelSize: root.px(13)

        contentItem: Text {
            id: label
            text: control.text
            color: control.emphasized ? "#FFFDF8" : "#4E584F"
            font: control.font
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 11
            color: control.emphasized ?
                       (control.down ? "#596F5D" : "#718A78") :
                       (control.down ? "#23718A78" :
                        control.hovered ? "#15718A78" : "#42FFFDF8")
            border.width: control.emphasized ? 0 : 1
            border.color: "#35718A78"
        }
    }

    Rectangle {
        anchors.fill: parent
        radius: 22
        color: "#DDFBF7EF"
        border.width: 1
        border.color: "#55FFFFFF"

        Rectangle {
            anchors.fill: parent
            anchors.margins: 1
            radius: parent.radius - 1
            color: root.accentColor
            opacity: 0.035
        }
    }

    Text {
        id: workspaceDate
        objectName: "workspaceDate"
        anchors.left: parent.left
        anchors.leftMargin: 22
        anchors.top: parent.top
        anchors.topMargin: 15
        text: root.journal.dateLabel
        color: "#41473F"
        font.family: root.diaryFontFamily
        font.pixelSize: root.px(24)
        font.weight: Font.DemiBold
    }

    RowLayout {
        id: workspaceStatusRow
        objectName: "workspaceStatusRow"
        anchors.right: parent.right
        anchors.rightMargin: 22
        anchors.top: workspaceDate.bottom
        anchors.topMargin: 10
        height: Math.max(25, root.px(18) + 4)
        spacing: 14

        Text {
            objectName: "workspaceStateText"
            text: root.journal.stateText
            color: "#41473F"
            font.family: root.systemFontFamily
            font.pixelSize: root.px(18)
            font.weight: Font.DemiBold
            Layout.alignment: Qt.AlignVCenter
        }
        Text {
            objectName: "workspaceBacklogText"
            text: root.journal.backlogText
            color: "#687068"
            font.family: root.systemFontFamily
            font.pixelSize: root.px(15)
            Layout.alignment: Qt.AlignVCenter
        }
    }

    Row {
        id: primaryActionRow
        objectName: "primaryActionRow"
        anchors.right: secondaryActionRow.left
        anchors.rightMargin: 8
        anchors.verticalCenter: workspaceDate.verticalCenter

        LiveButton {
            objectName: "expandedPauseButton"
            emphasized: true
            text: root.journal.paused ? "繼續聽" : "暫停錄音"
            onClicked: root.journal.togglePause()
        }
    }

    Row {
        id: secondaryActionRow
        objectName: "secondaryActionRow"
        anchors.right: parent.right
        anchors.rightMargin: 16
        anchors.verticalCenter: workspaceDate.verticalCenter
        spacing: 4

        LiveButton {
            objectName: "settingsButton"
            text: "設定"
            onClicked: root.openSheet("settings")
        }
        LiveButton {
            objectName: "systemButton"
            text: "系統"
            onClicked: root.openSheet("system")
        }
        LiveButton {
            objectName: "vocabularyButton"
            text: "字典"
            onClicked: root.openSheet("vocabulary")
        }
        LiveButton {
            objectName: "hoursButton"
            text: "時段"
            onClicked: root.openSheet("hours")
        }
    }

    Item {
        id: workspaceLiveTrace
        objectName: "workspaceLiveTrace"
        anchors.left: parent.left
        anchors.leftMargin: 22
        anchors.right: parent.right
        anchors.rightMargin: 22
        anchors.top: workspaceStatusRow.bottom
        anchors.topMargin: 7
        height: root.fontScale > 1.4 ? (root.hasPartial ? 72 : 60) :
                                      (root.hasPartial ? 56 : 34)

        Rectangle {
            id: workspaceLiveWash
            objectName: "workspaceLiveWash"
            anchors.fill: parent
            radius: 14
            color: root.accentColor
            opacity: root.journal.speechActive ? 0.095 : 0.045

            Behavior on opacity {
                enabled: root.motionEnabled
                NumberAnimation {
                    duration: root.journal.speechActive ? 180 : 900
                    easing.type: Easing.InOutSine
                }
            }
        }

        Text {
            id: workspaceLiveLabel
            objectName: "workspaceLiveLabel"
            anchors.left: parent.left
            anchors.leftMargin: 12
            anchors.top: parent.top
            anchors.topMargin: 7
            text: root.hasPartial ? "即時文字，尚未保存" : "即時預覽"
            color: "#5D685D"
            font.family: root.systemFontFamily
            font.pixelSize: root.px(13)
            font.weight: Font.DemiBold
        }

        Text {
            objectName: "workspacePartialText"
            anchors.left: parent.left
            anchors.leftMargin: root.fontScale > 1.4 ? 12 : 152
            anchors.right: parent.right
            anchors.rightMargin: 12
            y: root.fontScale > 1.4 ?
                   workspaceLiveLabel.y + workspaceLiveLabel.height + 2 :
                   (parent.height - height) / 2
            text: root.hasPartial ? root.journal.partialText : "等待下一段聲音…"
            wrapMode: Text.Wrap
            maximumLineCount: root.hasPartial ? 2 : 1
            elide: Text.ElideRight
            color: root.hasPartial ? "#363A35" : "#7B827A"
            font.family: root.systemFontFamily
            font.pixelSize: root.px(17)
            lineHeight: 1.22
        }
    }
}
