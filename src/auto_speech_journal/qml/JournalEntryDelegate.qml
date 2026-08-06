import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

FocusScope {
    id: root
    objectName: "timelineRow"

    required property string segmentId
    required property string hourLabel
    required property string timeLabel
    required property string statusLabel
    required property string segmentText
    required property string segmentState
    required property bool editable
    required property bool editing
    required property bool isHourStart
    required property string draftText
    required property string lastError
    required property string systemFontFamily
    required property string diaryFontFamily
    property real fontScale: 1

    signal beginEdit(string segmentId)
    signal updateDraft(string segmentId, string text)
    signal saveEdit(string segmentId)
    signal cancelEdit(string segmentId)

    readonly property real railX: 82
    readonly property real contentX: 112
    readonly property real headingHeight: isHourStart ? 42 : 0

    function px(size) {
        return Math.max(1, Math.round(size * fontScale))
    }

    activeFocusOnTab: editable && !editing
    height: headingHeight + segmentSurface.height
    Keys.onReturnPressed: function(event) {
        if (editable && !editing) {
            root.beginEdit(segmentId)
            event.accepted = true
        }
    }

    Rectangle {
        x: root.railX
        y: 0
        width: 1
        height: parent.height
        color: "#3D718A78"
    }

    Item {
        id: hourHeading
        width: parent.width
        height: root.headingHeight
        visible: root.isHourStart

        Text {
            objectName: "timelineHourLabel"
            x: 0
            anchors.verticalCenter: parent.verticalCenter
            width: root.railX - 15
            text: root.hourLabel
            horizontalAlignment: Text.AlignRight
            color: "#5C584F"
            font.family: root.systemFontFamily
            font.pixelSize: root.px(21)
            font.weight: Font.DemiBold
        }

        Rectangle {
            x: root.railX - 5
            anchors.verticalCenter: parent.verticalCenter
            width: 11
            height: 11
            radius: 6
            color: "#F8F1E8"
            border.width: 2
            border.color: "#718A78"
        }

        Rectangle {
            x: root.railX + 14
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            height: 1
            color: "#26718A78"
        }
    }

    Rectangle {
        id: segmentSurface
        objectName: "timelineSegmentSurface"
        x: root.contentX
        y: root.headingHeight
        width: Math.max(0, parent.width - x - 12)
        height: root.editing ? Math.max(194, editorColumn.implicitHeight + 30) :
                               Math.max(88, readerColumn.implicitHeight + 28)
        radius: 18
        color: root.editing ? "#DCF7F1E8" : "transparent"
        border.width: 0

        Behavior on color {
            enabled: !root.editing
            ColorAnimation { duration: 180 }
        }

        HoverHandler {
            id: segmentHover
            acceptedDevices: PointerDevice.Mouse
        }

        Canvas {
            id: mistCanvas
            x: -30
            y: -20
            width: parent.width + 60
            height: parent.height + 40
            visible: !root.editing
            opacity: segmentHover.hovered || root.activeFocus ? 0.98 : 0.82

            onWidthChanged: requestPaint()
            onHeightChanged: requestPaint()
            onPaint: {
                const ctx = getContext("2d")
                ctx.reset()
                if (width <= 0 || height <= 0)
                    return

                const failed = root.segmentState === "failed"
                const center = failed ? "rgba(255,244,239,0.68)" :
                                        "rgba(255,253,248,0.64)"
                const middle = failed ? "rgba(255,248,242,0.28)" :
                                        "rgba(255,253,248,0.26)"

                function paintHalo(cx, cy, rx, ry, alpha) {
                    ctx.save()
                    ctx.translate(cx, cy)
                    ctx.scale(rx, ry)
                    const mist = ctx.createRadialGradient(0, 0, 0, 0, 0, 1)
                    mist.addColorStop(0, center)
                    mist.addColorStop(0.48, middle)
                    mist.addColorStop(0.82, "rgba(255,253,248,0.08)")
                    mist.addColorStop(1, "rgba(255,253,248,0)")
                    ctx.globalAlpha = alpha
                    ctx.fillStyle = mist
                    ctx.fillRect(-1, -1, 2, 2)
                    ctx.restore()
                }

                paintHalo(width * 0.46, height * 0.51,
                          width * 0.50, height * 0.62, 1)
                paintHalo(width * 0.30, height * 0.58,
                          width * 0.31, height * 0.74, 0.36)
            }
        }

        Connections {
            target: root
            function onSegmentStateChanged() { mistCanvas.requestPaint() }
        }

        Rectangle {
            anchors.left: parent.left
            anchors.leftMargin: 14
            anchors.top: parent.top
            anchors.topMargin: 15
            width: 4
            height: 4
            radius: 2
            color: root.segmentState === "failed" ? "#B85C4A" : "#718A78"
            opacity: 0.72
        }

        Column {
            id: readerColumn
            visible: !root.editing
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: 14
            anchors.leftMargin: 28
            spacing: 9

            RowLayout {
                width: parent.width
                spacing: 7

                Text {
                    objectName: "timelineTimeLabel"
                    text: root.timeLabel
                    color: "#746F66"
                    font.family: root.systemFontFamily
                    font.pixelSize: root.px(13)
                }
                Text {
                    objectName: "timelineStatusLabel"
                    text: "·  " + root.statusLabel
                    color: root.segmentState === "failed" ? "#9C4F40" :
                           root.segmentState === "retry" ? "#8A704A" : "#586E5D"
                    font.family: root.systemFontFamily
                    font.pixelSize: root.px(12)
                }
                Item { Layout.fillWidth: true }
                Item {
                    objectName: "timelineCorrectionSlot"
                    Layout.preferredWidth: root.editable ? 66 : 0
                    Layout.preferredHeight: 30

                    Button {
                        id: correctionButton
                        objectName: "timelineCorrectionButton"
                        anchors.fill: parent
                        visible: root.editable &&
                                 (segmentHover.hovered || root.activeFocus || activeFocus)
                        enabled: visible
                        flat: true
                        text: "修正"
                        font.family: root.systemFontFamily
                        font.pixelSize: root.px(13)
                        Accessible.name: "修正 " + root.timeLabel + " 的文字"
                        onClicked: root.beginEdit(root.segmentId)

                        background: Rectangle {
                            radius: 10
                            color: correctionButton.down ? "#24718A78" :
                                   correctionButton.hovered ? "#18718A78" : "transparent"
                        }
                    }
                }
            }

            Text {
                objectName: "timelineSegmentText"
                width: parent.width
                text: root.segmentText || "（此片段尚無可顯示文字）"
                wrapMode: Text.Wrap
                color: root.segmentText ? "#363730" : "#8F8A80"
                font.family: root.diaryFontFamily
                font.pixelSize: root.px(17)
                lineHeight: 1.34
            }

            Text {
                objectName: "timelineErrorText"
                visible: root.lastError !== ""
                width: parent.width
                text: "注意：" + root.lastError
                wrapMode: Text.Wrap
                color: "#9C4F40"
                font.family: root.systemFontFamily
                font.pixelSize: root.px(13)
            }
        }

        Column {
            id: editorColumn
            visible: root.editing
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: 15
            spacing: 10

            RowLayout {
                width: parent.width
                Text {
                    text: root.timeLabel + "  ·  修正已保存片段"
                    color: "#5C675B"
                    font.family: root.systemFontFamily
                    font.pixelSize: root.px(13)
                }
                Item { Layout.fillWidth: true }
            }

            TextArea {
                id: editArea
                objectName: "timelineEditArea"
                width: parent.width
                height: 92
                text: root.draftText
                wrapMode: TextEdit.Wrap
                color: "#363730"
                font.family: root.diaryFontFamily
                font.pixelSize: root.px(17)
                selectByMouse: true
                leftPadding: 12
                rightPadding: 12
                topPadding: 10
                bottomPadding: 10
                background: Rectangle {
                    radius: 12
                    color: "#F5FFFDF8"
                    border.width: 1
                    border.color: editArea.activeFocus ? "#718A78" : "#AAB9A8"
                }
                onTextChanged: {
                    if (activeFocus)
                        root.updateDraft(root.segmentId, text)
                }
                onVisibleChanged: {
                    if (visible)
                        Qt.callLater(forceActiveFocus)
                }
            }

            Row {
                spacing: 8

                Button {
                    id: saveButton
                    implicitWidth: 76
                    implicitHeight: 32
                    text: "儲存"
                    font.family: root.systemFontFamily
                    font.pixelSize: root.px(13)
                    onClicked: root.saveEdit(root.segmentId)
                    background: Rectangle {
                        radius: 10
                        color: saveButton.down ? "#5F7865" : "#718A78"
                    }
                    contentItem: Text {
                        text: saveButton.text
                        color: "#FFFDF8"
                        font: saveButton.font
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                }

                Button {
                    id: cancelButton
                    implicitWidth: 76
                    implicitHeight: 32
                    flat: true
                    text: "取消"
                    font.family: root.systemFontFamily
                    font.pixelSize: root.px(13)
                    onClicked: root.cancelEdit(root.segmentId)
                    background: Rectangle {
                        radius: 10
                        color: cancelButton.down ? "#1D718A78" :
                               cancelButton.hovered ? "#12718A78" : "transparent"
                    }
                }
            }

            Text {
                objectName: "timelineEditErrorText"
                visible: root.lastError !== ""
                width: parent.width
                text: "注意：" + root.lastError
                wrapMode: Text.Wrap
                color: "#9C4F40"
                font.family: root.systemFontFamily
                font.pixelSize: root.px(13)
            }
        }
    }
}
