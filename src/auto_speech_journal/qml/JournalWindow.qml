import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

import "."

ApplicationWindow {
    id: window
    objectName: "journalWindow"

    width: journal.onboardingPending ? 560 : 440
    height: journal.onboardingPending ? 480 : 190
    minimumWidth: journal.onboardingPending ? 560 : (journal.expanded ? 960 : 440)
    maximumWidth: journal.onboardingPending ? 560 : (journal.expanded ? 16777215 : 440)
    minimumHeight: journal.onboardingPending ? 480 : (journal.expanded ? 680 : 190)
    maximumHeight: journal.onboardingPending ? 480 : (journal.expanded ? 16777215 : 190)
    visible: false
    color: "transparent"
    title: "聲跡日記"
    flags: Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint

    property string activeSheet: ""
    property string confirmationKind: ""
    property string confirmationHour: ""
    property string confirmationMessage: ""
    property string toastText: ""
    property bool toastIsError: false
    readonly property var journalModel: journal
    readonly property string systemFontFamily: journal.systemFontFamily
    readonly property string diaryFontFamily: journal.uiFontFamily
    readonly property string uiFontFamily: diaryFontFamily
    readonly property int monthIndex: Math.max(
        0,
        Math.min(11, parseInt(journal.dayKey.slice(5, 7)) - 1)
    )
    readonly property color monthTint: [
        "#AAB7C7", "#A58AB6", "#9FB88D", "#7894A8",
        "#5E8B70", "#657AA3", "#D6A143", "#3F8582",
        "#8794B3", "#C88D4E", "#8A668F", "#C7B99E"
    ][monthIndex]
    readonly property bool motionAllowed: visible && !journal.reducedMotion &&
                                           (visibility === Window.Windowed ||
                                            visibility === Window.FullScreen)

    function fontPx(baseSize) {
        return Math.max(1, Math.round(baseSize * journal.uiFontScale))
    }

    // Opening a sheet reloads it from the view model. The drawer dispatches to
    // the right sheet, so no field is reached across a file boundary by id.
    onActiveSheetChanged: utilityDrawer.reloadSheet(activeSheet)

    function askDelete(hourKey) {
        confirmationKind = "delete"
        confirmationHour = hourKey
        confirmationMessage = "確定永久刪除「" + hourKey + "」的資料庫紀錄、Markdown 與仍存在的暫存音訊？"
    }

    function askExit() {
        confirmationKind = "exit"
        confirmationHour = ""
        confirmationMessage = "確定停止錄音、完成可及的轉錄並結束聲跡日記？"
    }

    function askClearVocabulary() {
        confirmationKind = "clearVocabulary"
        confirmationHour = ""
        confirmationMessage = "確定清空所有已學詞語？既有的使用者修正仍會保留。"
    }

    function showToast(message, isError) {
        toastText = message
        toastIsError = isError
        toast.visible = true
        toastTimer.restart()
    }

    onXChanged: journal.rememberCompactPosition(x, y)
    onYChanged: journal.rememberCompactPosition(x, y)
    onWidthChanged: journal.rememberExpandedSize(width, height)
    onHeightChanged: journal.rememberExpandedSize(width, height)
    onClosing: function(close) {
        if (!journal.allowClose) {
            close.accepted = false
            journal.handleNativeClose()
        }
    }

    Connections {
        target: journal
        function onActionFailed(message) { window.showToast(message, true) }
        function onActionSucceeded(message) { window.showToast(message, false) }
        function onExpandedChanged() {
            window.activeSheet = ""
        }
    }

    component ResizeHandle: MouseArea {
        required property int edges
        required property int handleCursor

        visible: journal.expanded
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton
        cursorShape: handleCursor
        z: 120

        onPressed: function(mouse) {
            window.startSystemResize(edges)
            mouse.accepted = true
        }
    }

    Rectangle {
        id: shell
        anchors.fill: parent
        radius: journal.expanded ? Theme.radiusExpandedWindow
                                 : Theme.radiusCompactWindow
        color: Theme.paper
        border.width: Theme.hairline
        border.color: Theme.line
        clip: true

        Rectangle {
            id: titleBar
            objectName: "titleBar"
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            height: journal.expanded ? 50 : 38
            color: "transparent"
            border.width: 0
            z: 20

            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                height: parent.height + (journal.expanded ? 24 : 0)
                gradient: Gradient {
                    GradientStop {
                        position: 0
                        color: journal.expanded ? "#D6F8F2E8" : "#2EF8F2E8"
                    }
                    GradientStop {
                        position: 0.64
                        color: journal.expanded ? "#98F8F2E8" : "#2EF8F2E8"
                    }
                    GradientStop {
                        position: 1
                        color: journal.expanded ? "#00F8F2E8" : "#2EF8F2E8"
                    }
                }
            }

            Rectangle {
                anchors.fill: parent
                color: window.monthTint
                opacity: 0.025
            }

            MouseArea {
                anchors.fill: parent
                onPressed: window.startSystemMove()
            }

            RowLayout {
                id: brandRow
                anchors.left: parent.left
                anchors.leftMargin: journal.expanded ? 20 : 14
                anchors.verticalCenter: parent.verticalCenter
                spacing: 10

                Image {
                    objectName: "brandIcon"
                    Layout.preferredWidth: journal.expanded ? 30 : 24
                    Layout.preferredHeight: Layout.preferredWidth
                    Layout.alignment: Qt.AlignVCenter
                    source: Qt.resolvedUrl("../assets/brand/journal-ink-icon.png")
                    sourceSize.width: 64
                    sourceSize.height: 64
                    fillMode: Image.PreserveAspectFit
                    smooth: true
                    mipmap: true
                }

                Text {
                    objectName: "brandTitle"
                    Layout.alignment: Qt.AlignVCenter
                    text: "聲跡日記"
                    color: Theme.inkStrong
                    font.family: window.diaryFontFamily
                    font.pixelSize: window.fontPx(journal.expanded ? 22 : 18)
                    font.weight: Font.DemiBold
                }

                // The date used to headline the live bar, which pushed the whole
                // workspace down. It reads just as well as a title-bar subtitle
                // and gives the timeline back that vertical space.
                Text {
                    objectName: "workspaceDate"
                    Layout.alignment: Qt.AlignVCenter
                    Layout.leftMargin: Theme.spaceSm
                    visible: journal.expanded
                    text: journal.dateLabel
                    color: Theme.inkMuted
                    font.family: window.diaryFontFamily
                    font.pixelSize: window.fontPx(16)
                }
            }

            IconButton {
                objectName: "closeButton"
                anchors.right: parent.right
                anchors.rightMargin: journal.expanded ? 12 : 9
                anchors.verticalCenter: parent.verticalCenter
                implicitWidth: 34
                implicitHeight: 32
                text: "×"
                Accessible.name: journal.expanded ? "收回精簡浮窗" : "最小化浮窗"
                onClicked: journal.handleNativeClose()
            }
        }

        CompactRecorder {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: titleBar.bottom
            anchors.bottom: parent.bottom
            visible: !journal.expanded
            journal: window.journalModel
            systemFontFamily: window.systemFontFamily
            monthTint: window.monthTint
        }

        Item {
            id: expandedContent
            objectName: "expandedContent"
            anchors.fill: parent
            visible: journal.expanded

            TodayWorkspace {
                id: todayWorkspace
                anchors.fill: parent
                journal: window.journalModel
                monthTint: window.monthTint
                systemFontFamily: window.systemFontFamily
                diaryFontFamily: window.diaryFontFamily
                fontScale: journal.uiFontScale
                topInset: titleBar.height
                motionEnabled: window.motionAllowed && journal.expanded
                onOpenSheet: function(sheetKey) { window.activeSheet = sheetKey }
            }
        }
        UtilityDrawer {
            id: utilityDrawer
            anchors.fill: parent
            visible: journal.expanded && window.activeSheet !== ""
            z: 30
            journal: window.journalModel
            activeSheet: window.activeSheet
            onSheetRequested: function(key) { window.activeSheet = key }
            onCloseRequested: window.activeSheet = ""
            onExitRequested: window.askExit()
            onClearVocabularyRequested: window.askClearVocabulary()
            onDeleteHourRequested: function(hourKey) { window.askDelete(hourKey) }
        }

        Rectangle {
            objectName: "updateAvailableBanner"
            anchors.top: parent.top
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.topMargin: 8
            width: Math.min(parent.width - 20, 430)
            height: updateBannerRow.implicitHeight + 16
            radius: 10
            color: "#FFF2DC"
            border.color: "#D6AA6D"
            visible: journal.updateAvailable && !journal.onboardingPending
            z: 45

            RowLayout {
                id: updateBannerRow
                anchors.fill: parent
                anchors.margins: 8
                Text {
                    Layout.fillWidth: true
                    text: journal.updateAvailableText
                    color: "#815528"
                    font.family: window.systemFontFamily
                    font.pixelSize: window.fontPx(13)
                    elide: Text.ElideRight
                }
                Button {
                    objectName: "openUpdateReleaseButton"
                    text: "查看下載頁"
                    onClicked: journal.openUpdateRelease()
                }
            }
        }

        FirstRunWizard {
            anchors.fill: parent
            visible: journal.onboardingPending
            z: 100
            viewModel: journal
            hostWindow: window
        }

        Rectangle {
            id: confirmationOverlay
            objectName: "confirmationOverlay"
            anchors.fill: parent
            visible: window.confirmationKind !== ""
            color: "#704B4036"
            z: 50

            MouseArea {
                anchors.fill: parent
                preventStealing: true
                onClicked: function(mouse) { mouse.accepted = true }
            }

            Rectangle {
                id: confirmationCard
                objectName: "confirmationCard"
                anchors.centerIn: parent
                readonly property real contentMargin: journal.expanded ? 24 : 6
                width: journal.expanded ? 430 : parent.width - 16
                height: Math.min(
                    parent.height - (journal.expanded ? 80 : 8),
                    confirmationContent.implicitHeight + contentMargin * 2
                )
                radius: 14
                color: "#FFF9EE"
                border.width: 1
                border.color: "#CDBEAA"
                clip: true

                Column {
                    id: confirmationContent
                    objectName: "confirmationContent"
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: confirmationCard.contentMargin
                    spacing: journal.expanded ? 16 : 4
                    Text {
                        text: window.confirmationKind === "delete" ? "刪除此小時" :
                              window.confirmationKind === "clearVocabulary" ? "清空校正字典" :
                              "結束聲跡日記"
                        color: "#493F35"
                        font.family: window.systemFontFamily
                        font.pixelSize: window.fontPx(journal.expanded ? 24 : 16)
                        font.weight: Font.DemiBold
                    }
                    Text {
                        objectName: "confirmationMessage"
                        width: parent.width
                        height: implicitHeight
                        text: window.confirmationMessage
                        wrapMode: Text.Wrap
                        color: "#685C50"
                        font.family: window.systemFontFamily
                        font.pixelSize: window.fontPx(journal.expanded ? 16 : 12)
                    }
                    Row {
                        objectName: "confirmationActions"
                        anchors.right: parent.right
                        spacing: 8
                        PaperButton {
                            text: "取消"
                            onClicked: window.confirmationKind = ""
                        }
                        PaperButton {
                            text: window.confirmationKind === "delete" ? "永久刪除" :
                                  window.confirmationKind === "clearVocabulary" ? "清空全部" :
                                  "停止並結束"
                            onClicked: {
                                const kind = window.confirmationKind
                                const hour = window.confirmationHour
                                window.confirmationKind = ""
                                if (kind === "delete") {
                                    if (journal.deleteHour(hour))
                                        utilityDrawer.refreshHourOptions()
                                } else if (kind === "clearVocabulary") {
                                    journal.clearVocabulary()
                                } else {
                                    journal.exitApplication()
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    ResizeHandle {
        objectName: "leftResizeHandle"
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.topMargin: 12
        anchors.bottomMargin: 12
        width: 7
        edges: Qt.LeftEdge
        handleCursor: Qt.SizeHorCursor
    }
    ResizeHandle {
        objectName: "rightResizeHandle"
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.topMargin: 12
        anchors.bottomMargin: 12
        width: 7
        edges: Qt.RightEdge
        handleCursor: Qt.SizeHorCursor
    }
    ResizeHandle {
        objectName: "topResizeHandle"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.leftMargin: 12
        anchors.rightMargin: 12
        height: 7
        edges: Qt.TopEdge
        handleCursor: Qt.SizeVerCursor
    }
    ResizeHandle {
        objectName: "bottomResizeHandle"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: 12
        anchors.rightMargin: 12
        height: 7
        edges: Qt.BottomEdge
        handleCursor: Qt.SizeVerCursor
    }
    ResizeHandle {
        anchors.left: parent.left
        anchors.top: parent.top
        width: 12
        height: 12
        edges: Qt.LeftEdge | Qt.TopEdge
        handleCursor: Qt.SizeFDiagCursor
    }
    ResizeHandle {
        anchors.right: parent.right
        anchors.top: parent.top
        width: 12
        height: 12
        edges: Qt.RightEdge | Qt.TopEdge
        handleCursor: Qt.SizeBDiagCursor
    }
    ResizeHandle {
        anchors.left: parent.left
        anchors.bottom: parent.bottom
        width: 12
        height: 12
        edges: Qt.LeftEdge | Qt.BottomEdge
        handleCursor: Qt.SizeBDiagCursor
    }
    ResizeHandle {
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        width: 12
        height: 12
        edges: Qt.RightEdge | Qt.BottomEdge
        handleCursor: Qt.SizeFDiagCursor
    }

    Rectangle {
        id: toast
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 22
        width: Math.min(parent.width - 40, toastLabel.implicitWidth + 34)
        height: 40
        radius: 12
        visible: false
        color: window.toastIsError ? "#9E5749" : "#637D68"
        z: 80

        Text {
            id: toastLabel
            anchors.centerIn: parent
            text: window.toastText
            color: "#FFFDF8"
            font.family: window.systemFontFamily
            font.pixelSize: window.fontPx(15)
        }
    }

    Timer {
        id: toastTimer
        interval: 3200
        onTriggered: toast.visible = false
    }
}
