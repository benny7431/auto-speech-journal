import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "."

/*
 * One saved segment, drawn as a card, optionally preceded by its hour header.
 *
 * The header belongs to the delegate rather than a ListView section so that
 * collapsing an hour is a pure height change on the rows beneath it - the model
 * keeps its flat shape and the view keeps its scroll-position logic.
 */
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
    required property int hourSegmentCount
    required property string draftText
    required property string lastError
    required property string systemFontFamily
    required property string diaryFontFamily
    property real fontScale: 1
    property bool collapsed: false
    property color accentColor: Theme.accent

    signal beginEdit(string segmentId)
    signal updateDraft(string segmentId, string text)
    signal saveEdit(string segmentId)
    signal cancelEdit(string segmentId)
    signal toggleHour(string hourLabel)

    readonly property real headerHeight: isHourStart ? 40 : 0

    function px(size) {
        return Math.max(1, Math.round(size * fontScale))
    }

    activeFocusOnTab: editable && !editing && !collapsed
    height: headerHeight + (collapsed ? 0 : card.height + Theme.spaceSm)

    Keys.onReturnPressed: function(event) {
        if (editable && !editing && !collapsed) {
            root.beginEdit(segmentId)
            event.accepted = true
        }
    }

    // -- hour header ---------------------------------------------------------
    Item {
        id: hourHeader
        objectName: "timelineHourHeader"
        width: parent.width
        height: root.headerHeight
        visible: root.isHourStart

        TapHandler {
            onTapped: root.toggleHour(root.hourLabel)
        }

        HoverHandler {
            id: headerHover
            acceptedDevices: PointerDevice.Mouse
            cursorShape: Qt.PointingHandCursor
        }

        Rectangle {
            anchors.fill: parent
            anchors.topMargin: Theme.spaceXs
            anchors.bottomMargin: Theme.spaceXs
            radius: Theme.radiusSm
            color: headerHover.hovered ? Theme.wash(Theme.ink, 0.04) : "transparent"
        }

        Row {
            anchors.left: parent.left
            anchors.leftMargin: Theme.spaceSm
            anchors.verticalCenter: parent.verticalCenter
            spacing: Theme.spaceSm

            // Drawn rather than typed: the journal font is user-selectable and
            // the handwriting families have no geometric-shape glyphs, so a "▾"
            // renders as tofu in exactly the fonts this app is built around.
            Canvas {
                id: disclosure
                objectName: "timelineHourDisclosure"
                anchors.verticalCenter: parent.verticalCenter
                width: 10
                height: 10
                rotation: root.collapsed ? -90 : 0

                property color strokeColor: Theme.inkMuted
                onStrokeColorChanged: requestPaint()

                Behavior on rotation {
                    NumberAnimation { duration: 120; easing.type: Easing.OutCubic }
                }

                onPaint: {
                    const ctx = getContext("2d")
                    ctx.reset()
                    ctx.strokeStyle = strokeColor
                    ctx.lineWidth = 1.5
                    ctx.lineCap = "round"
                    ctx.lineJoin = "round"
                    ctx.beginPath()
                    ctx.moveTo(2, 3.5)
                    ctx.lineTo(5, 7)
                    ctx.lineTo(8, 3.5)
                    ctx.stroke()
                }
            }

            Rectangle {
                anchors.verticalCenter: parent.verticalCenter
                width: 7
                height: 7
                radius: 4
                color: root.accentColor
                opacity: 0.7
            }

            Text {
                objectName: "timelineHourLabel"
                anchors.verticalCenter: parent.verticalCenter
                text: root.hourLabel
                color: Theme.inkBody
                font.family: root.systemFontFamily
                font.pixelSize: root.px(16)
                font.weight: Font.DemiBold
            }
        }

        Text {
            objectName: "timelineHourCount"
            anchors.right: parent.right
            anchors.rightMargin: Theme.spaceSm
            anchors.verticalCenter: parent.verticalCenter
            text: root.hourSegmentCount + " 則"
            color: Theme.inkMuted
            font.family: root.systemFontFamily
            font.pixelSize: root.px(13)
        }
    }

    // -- segment card --------------------------------------------------------
    Rectangle {
        id: card
        objectName: "timelineSegmentSurface"
        anchors.left: parent.left
        anchors.right: parent.right
        y: root.headerHeight
        visible: !root.collapsed
        height: root.editing
                ? Math.max(190, editorColumn.implicitHeight + 2 * Theme.spaceLg)
                : Math.max(88, readerColumn.implicitHeight + 2 * Theme.spaceLg)
        radius: Theme.radiusMd
        color: Theme.paperRaised
        border.width: Theme.hairline
        border.color: root.activeFocus || cardHover.hovered ? Theme.accentSoft : Theme.line

        HoverHandler {
            id: cardHover
            acceptedDevices: PointerDevice.Mouse
        }

        // A state stripe down the leading edge: quiet when settled, coloured
        // when the segment still needs something from the user.
        Rectangle {
            width: 3
            height: parent.height - 2 * Theme.spaceMd
            anchors.left: parent.left
            anchors.leftMargin: Theme.hairline
            anchors.verticalCenter: parent.verticalCenter
            radius: 2
            color: Theme.segmentColor(root.segmentState)
            opacity: root.segmentState === "failed" || root.segmentState === "retry"
                     ? 0.9 : 0.35
        }

        Column {
            id: readerColumn
            visible: !root.editing
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: Theme.spaceLg
            spacing: Theme.spaceSm

            RowLayout {
                width: parent.width
                spacing: Theme.spaceSm

                Text {
                    objectName: "timelineTimeLabel"
                    text: root.timeLabel
                    color: Theme.inkMuted
                    font.family: root.systemFontFamily
                    font.pixelSize: root.px(13)
                }
                Text {
                    objectName: "timelineStatusLabel"
                    text: root.statusLabel
                    color: Theme.segmentColor(root.segmentState)
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
                                 (cardHover.hovered || root.activeFocus || activeFocus)
                        enabled: visible
                        flat: true
                        text: "修正"
                        font.family: root.systemFontFamily
                        font.pixelSize: root.px(13)
                        Accessible.name: "修正 " + root.timeLabel + " 的文字"
                        onClicked: root.beginEdit(root.segmentId)

                        background: Rectangle {
                            radius: Theme.radiusSm
                            color: correctionButton.down
                                   ? Theme.wash(Theme.accent, 0.16)
                                   : correctionButton.hovered
                                     ? Theme.wash(Theme.accent, 0.09) : "transparent"
                        }
                    }
                }
            }

            Text {
                objectName: "timelineSegmentText"
                width: parent.width
                text: root.segmentText || "（此片段尚無可顯示文字）"
                wrapMode: Text.Wrap
                color: root.segmentText ? Theme.ink : Theme.inkFaint
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
                color: Theme.danger
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
            anchors.margins: Theme.spaceLg
            spacing: Theme.spaceMd

            Text {
                text: root.timeLabel + "  ·  修正已保存片段"
                color: Theme.inkBody
                font.family: root.systemFontFamily
                font.pixelSize: root.px(13)
            }

            TextArea {
                id: editArea
                objectName: "timelineEditArea"
                width: parent.width
                height: 92
                text: root.draftText
                wrapMode: TextEdit.Wrap
                color: Theme.ink
                font.family: root.diaryFontFamily
                font.pixelSize: root.px(17)
                selectByMouse: true
                leftPadding: Theme.spaceMd
                rightPadding: Theme.spaceMd
                topPadding: Theme.spaceSm
                bottomPadding: Theme.spaceSm
                background: Rectangle {
                    radius: Theme.radiusSm
                    color: Theme.paper
                    border.width: Theme.hairline
                    border.color: editArea.activeFocus ? Theme.accent : Theme.accentSoft
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
                spacing: Theme.spaceSm

                Button {
                    id: saveButton
                    implicitWidth: 76
                    implicitHeight: 32
                    text: "儲存"
                    font.family: root.systemFontFamily
                    font.pixelSize: root.px(13)
                    onClicked: root.saveEdit(root.segmentId)
                    background: Rectangle {
                        radius: Theme.radiusSm
                        color: saveButton.down ? Theme.accentDeep : Theme.accent
                    }
                    contentItem: Text {
                        text: saveButton.text
                        color: Theme.paperRaised
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
                        radius: Theme.radiusSm
                        color: cancelButton.down
                               ? Theme.wash(Theme.accent, 0.14)
                               : cancelButton.hovered
                                 ? Theme.wash(Theme.accent, 0.08) : "transparent"
                    }
                }
            }

            Text {
                objectName: "timelineEditErrorText"
                visible: root.lastError !== ""
                width: parent.width
                text: "注意：" + root.lastError
                wrapMode: Text.Wrap
                color: Theme.danger
                font.family: root.systemFontFamily
                font.pixelSize: root.px(13)
            }
        }
    }
}
