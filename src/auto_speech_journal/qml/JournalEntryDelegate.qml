import QtQuick
import QtQuick.Controls

import "."

/*
 * One saved segment as a line of manuscript, optionally under its hour rule.
 *
 * Time is the spine. A fixed left gutter carries the timestamp, a hairline rail
 * runs down its edge, and the text hangs to the right and flows. There is no card
 * per segment: a voice journal is mostly short utterances, and giving an eight
 * character line the same 88px box as a three line one wasted most of the window
 * and made every entry look identical.
 *
 * Height follows content. A settled segment says nothing about its state - only
 * the ones that still want something from you carry a mark on the rail.
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

    // The gutter grows with the journal font so the timestamp never collides
    // with the rail, but the rail itself stays on one vertical line all day.
    readonly property real gutter: Math.round(64 * Math.min(fontScale, 1.35))
    readonly property real railX: gutter
    readonly property real contentX: gutter + Theme.spaceLg
    readonly property real headerHeight: isHourStart ? Math.round(px(16) + 30) : 0

    // "已定稿" is the resting state of nearly every segment; printing it beside
    // each one is noise. Only an unsettled segment announces itself.
    readonly property bool needsAttention: segmentState === "failed" ||
                                           segmentState === "retry" ||
                                           segmentState === "finalizing" ||
                                           segmentState === "captured"

    /* "[09:12:06]" reads as "09:12" in the gutter. */
    readonly property string gutterTime: {
        const match = /(\d{2}:\d{2})/.exec(timeLabel)
        return match ? match[1] : timeLabel
    }

    function px(size) {
        return Math.max(1, Math.round(size * fontScale))
    }

    activeFocusOnTab: editable && !editing && !collapsed
    height: headerHeight + (collapsed ? 0 : bodyHeight)

    readonly property real bodyHeight: editing
        ? editorColumn.implicitHeight + 2 * Theme.spaceMd
        : readerColumn.implicitHeight + Theme.spaceMd

    Keys.onReturnPressed: function(event) {
        if (editable && !editing && !collapsed) {
            root.beginEdit(segmentId)
            event.accepted = true
        }
    }

    // -- hour rule -----------------------------------------------------------
    Item {
        id: hourHeader
        objectName: "timelineHourHeader"
        width: parent.width
        height: root.headerHeight
        visible: root.isHourStart

        MouseArea {
            id: headerHover
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: root.toggleHour(root.hourLabel)
        }

        Text {
            objectName: "timelineHourLabel"
            anchors.left: parent.left
            anchors.bottom: rule.bottom
            anchors.bottomMargin: Theme.spaceXs
            width: root.gutter - Theme.spaceSm
            horizontalAlignment: Text.AlignRight
            text: root.hourLabel
            color: headerHover.containsMouse ? Theme.ink : Theme.inkBody
            font.family: root.systemFontFamily
            font.pixelSize: root.px(16)
            font.weight: Font.DemiBold
        }

        // The rule is the day's strongest horizontal: it is what tells you an
        // hour turned over.
        Rectangle {
            id: rule
            anchors.left: parent.left
            anchors.leftMargin: root.railX
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.bottomMargin: Theme.spaceSm
            height: Math.max(1, Theme.hairline)
            color: Theme.line
        }

        Rectangle {
            anchors.horizontalCenter: rule.left
            anchors.verticalCenter: rule.verticalCenter
            width: 7
            height: 7
            radius: 4
            color: root.accentColor
        }

        Text {
            objectName: "timelineHourCount"
            anchors.right: parent.right
            anchors.bottom: rule.bottom
            anchors.bottomMargin: Theme.spaceXs
            text: root.collapsed ? root.hourSegmentCount + " 則 · 已收合"
                                 : root.hourSegmentCount + " 則"
            color: Theme.inkFaint
            font.family: root.systemFontFamily
            font.pixelSize: root.px(12)
        }
    }

    // -- the rail ------------------------------------------------------------
    Rectangle {
        x: root.railX
        y: root.headerHeight
        width: Math.max(1, Theme.hairline)
        height: root.collapsed ? 0 : root.bodyHeight
        visible: !root.collapsed
        // The rail is what makes this read as a timeline rather than a list, so
        // it carries the structural line weight, not the faintest one.
        color: Theme.line
    }

    // -- segment -------------------------------------------------------------
    Item {
        id: body
        objectName: "timelineSegmentSurface"
        anchors.left: parent.left
        anchors.right: parent.right
        y: root.headerHeight
        height: root.bodyHeight
        visible: !root.collapsed

        HoverHandler {
            id: rowHover
            acceptedDevices: PointerDevice.Mouse
        }

        // Hovering lifts the whole line rather than outlining a box.
        Rectangle {
            anchors.left: parent.left
            anchors.leftMargin: root.railX
            anchors.right: parent.right
            anchors.top: parent.top
            height: parent.height - Theme.spaceSm
            radius: Theme.radiusSm
            color: root.editing ? Theme.paperRaised
                   : rowHover.hovered || root.activeFocus
                     ? Theme.wash(Theme.ink, 0.035) : "transparent"
            border.width: root.editing ? Theme.hairline : 0
            border.color: Theme.line
        }

        Text {
            objectName: "timelineTimeLabel"
            anchors.left: parent.left
            anchors.top: parent.top
            anchors.topMargin: Math.round(root.px(19) * 0.34)
            width: root.gutter - Theme.spaceSm
            horizontalAlignment: Text.AlignRight
            text: root.gutterTime
            color: Theme.inkMuted
            font.family: root.systemFontFamily
            font.pixelSize: root.px(13)
        }

        // Unsettled segments get a filled node on the rail; settled ones leave
        // the rail clean, so a glance down the gutter finds the exceptions.
        Rectangle {
            objectName: "timelineStateNode"
            x: root.railX - 3.5
            y: Math.round(root.px(19) * 0.42)
            width: 8
            height: 8
            radius: 4
            visible: root.needsAttention
            color: Theme.segmentColor(root.segmentState)
            border.width: Theme.hairline
            border.color: Theme.paper
        }

        Column {
            id: readerColumn
            visible: !root.editing
            anchors.left: parent.left
            anchors.leftMargin: root.contentX
            anchors.right: parent.right
            anchors.rightMargin: Theme.spaceSm
            anchors.top: parent.top
            spacing: 2

            Text {
                objectName: "timelineSegmentText"
                width: parent.width - correctionSlot.width
                text: root.segmentText || "（此片段尚無可顯示文字）"
                wrapMode: Text.Wrap
                color: root.segmentText ? Theme.ink : Theme.inkFaint
                font.family: root.diaryFontFamily
                font.pixelSize: root.px(19)
                lineHeight: 1.34
            }

            Row {
                spacing: Theme.spaceSm
                visible: root.needsAttention || root.lastError !== ""

                Text {
                    objectName: "timelineStatusLabel"
                    text: root.statusLabel
                    color: Theme.segmentColor(root.segmentState)
                    font.family: root.systemFontFamily
                    font.pixelSize: root.px(12)
                }
                Text {
                    objectName: "timelineErrorText"
                    visible: root.lastError !== ""
                    text: root.lastError
                    color: Theme.danger
                    font.family: root.systemFontFamily
                    font.pixelSize: root.px(12)
                }
            }
        }

        Item {
            id: correctionSlot
            objectName: "timelineCorrectionSlot"
            anchors.right: parent.right
            anchors.rightMargin: Theme.spaceSm
            anchors.top: parent.top
            width: root.editable ? 66 : 0
            height: 30

            Button {
                id: correctionButton
                objectName: "timelineCorrectionButton"
                anchors.fill: parent
                visible: root.editable && !root.editing &&
                         (rowHover.hovered || root.activeFocus || activeFocus)
                enabled: visible
                flat: true
                text: "修正"
                font.family: root.systemFontFamily
                font.pixelSize: root.px(13)
                Accessible.name: "修正 " + root.timeLabel + " 的文字"
                onClicked: root.beginEdit(root.segmentId)

                background: Rectangle {
                    radius: Theme.radiusSm
                    color: correctionButton.down ? Theme.wash(Theme.accent, 0.16)
                           : correctionButton.hovered
                             ? Theme.wash(Theme.accent, 0.09) : "transparent"
                }
            }
        }

        Column {
            id: editorColumn
            visible: root.editing
            anchors.left: parent.left
            anchors.leftMargin: root.contentX
            anchors.right: parent.right
            anchors.rightMargin: Theme.spaceMd
            anchors.top: parent.top
            anchors.topMargin: Theme.spaceSm
            spacing: Theme.spaceSm

            Text {
                text: "修正已保存片段"
                color: Theme.inkMuted
                font.family: root.systemFontFamily
                font.pixelSize: root.px(12)
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
                font.pixelSize: root.px(19)
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
                        color: cancelButton.down ? Theme.wash(Theme.accent, 0.14)
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
