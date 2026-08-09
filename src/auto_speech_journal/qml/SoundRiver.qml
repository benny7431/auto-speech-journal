import QtQuick
import QtQuick.Controls

import "."

/*
 * Today's saved segments, as a list of cards grouped under collapsible hours.
 *
 * Collapse lives here rather than in the model. The model stays a flat list of
 * segments, so `positionViewAtEnd` and the scroll-preservation handshake below
 * keep working unchanged; a collapsed hour simply reports zero height for its
 * rows while its header stays visible.
 */
Item {
    id: root
    objectName: "soundRiver"

    required property var journal
    required property string systemFontFamily
    required property string diaryFontFamily
    property real fontScale: 1
    property color accentColor: Theme.accent
    property bool timelineWasOpened: false
    property bool userScrolledBack: false
    property real savedContentY: 0

    property var collapsedHours: ({})

    property alias listView: timelineList
    property alias newSegmentsIndicator: newSegmentsPill

    function px(size) {
        return Math.max(1, Math.round(size * fontScale))
    }

    function isCollapsed(hourLabel) {
        return collapsedHours[hourLabel] === true
    }

    function toggleHour(hourLabel) {
        // Reassign rather than mutate: QML only re-evaluates bindings on a new
        // value for a var property.
        const next = {}
        for (const key in collapsedHours)
            next[key] = collapsedHours[key]
        next[hourLabel] = !next[hourLabel]
        collapsedHours = next
    }

    function positionAtLatest() {
        timelineList.positionViewAtEnd()
        userScrolledBack = false
        newSegmentsPill.visible = false
    }

    function openAtLatest() {
        timelineWasOpened = false
        userScrolledBack = false
        newSegmentsPill.visible = false
        journal.showAboutToOpen()
        Qt.callLater(function() {
            timelineList.positionViewAtEnd()
            timelineWasOpened = true
        })
    }

    Component.onCompleted: {
        if (journal.expanded)
            openAtLatest()
    }

    Connections {
        target: root.journal

        function onExpandedChanged() {
            if (root.journal.expanded)
                root.openAtLatest()
        }

        function onTimelineUpdating() {
            root.savedContentY = timelineList.contentY
        }

        function onTimelineUpdated(added) {
            if (!root.journal.expanded)
                return
            const preservePosition = root.userScrolledBack ||
                                     root.journal.timelineModel.hasActiveEdit
            if (!root.timelineWasOpened || !preservePosition) {
                Qt.callLater(function() { timelineList.positionViewAtEnd() })
                return
            }

            Qt.callLater(function() {
                const maximumY = Math.max(
                    timelineList.originY,
                    timelineList.contentHeight - timelineList.height
                )
                timelineList.contentY = Math.min(root.savedContentY, maximumY)
            })
            if (added > 0)
                newSegmentsPill.visible = true
        }
    }

    // The reading column. Everything below is measured against this rather than
    // the window, so a wide workspace gets margins instead of longer lines.
    Item {
        id: column
        objectName: "timelineColumn"
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        width: Math.min(parent.width, Theme.readingMeasure)

        Text {
            id: timelineTitle
            objectName: "timelineTitle"
            anchors.left: parent.left
            anchors.top: parent.top
            text: "今日聲跡"
            color: Theme.inkStrong
            font.family: root.diaryFontFamily
            font.pixelSize: root.px(22)
            font.weight: Font.DemiBold
        }

        ListView {
            id: timelineList
            objectName: "timelineList"
            readonly property color idleSegmentColor: "transparent"
            readonly property real segmentBorderWidth: 0
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: timelineTitle.bottom
            anchors.topMargin: Theme.spaceMd
            anchors.bottom: parent.bottom
            clip: true
            spacing: 0
            model: root.journal.timelineModel
            boundsBehavior: Flickable.StopAtBounds
            reuseItems: true
            cacheBuffer: Math.max(0, Math.min(height * 1.5, 1200))
            ScrollBar.vertical: ScrollBar {
                policy: ScrollBar.AsNeeded
                width: 8
            }

            onMovementEnded: {
                root.userScrolledBack = !atYEnd
                if (atYEnd)
                    newSegmentsPill.visible = false
            }

            delegate: JournalEntryDelegate {
                width: ListView.view.width - Theme.spaceMd
                systemFontFamily: root.systemFontFamily
                diaryFontFamily: root.diaryFontFamily
                fontScale: root.fontScale
                accentColor: root.accentColor
                collapsed: root.isCollapsed(hourLabel)
                onBeginEdit: function(segmentId) { root.journal.beginEdit(segmentId) }
                onUpdateDraft: function(segmentId, text) {
                    root.journal.updateDraft(segmentId, text)
                }
                onSaveEdit: function(segmentId) { root.journal.saveEdit(segmentId) }
                onCancelEdit: function(segmentId) { root.journal.cancelEdit(segmentId) }
                onToggleHour: function(hourLabel) { root.toggleHour(hourLabel) }
            }

            Text {
                anchors.centerIn: parent
                visible: timelineList.count === 0
                width: Math.min(parent.width - 80, 480)
                text: "今天還沒有已保存的片段\n開始說話後，聲跡會依小時收進這裡"
                horizontalAlignment: Text.AlignHCenter
                color: Theme.inkMuted
                font.family: root.systemFontFamily
                font.pixelSize: root.px(16)
                lineHeight: 1.5
            }
        }
    }

    Button {
        id: newSegmentsPill
        objectName: "newSegmentsPill"
        anchors.horizontalCenter: column.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: Theme.spaceLg
        visible: false
        implicitWidth: Math.max(190, newSegmentsLabel.implicitWidth + 32)
        implicitHeight: 38
        leftPadding: Theme.spaceLg
        rightPadding: Theme.spaceLg
        text: "有新片段  ·  回到最新"
        font.family: root.systemFontFamily
        font.pixelSize: root.px(14)
        onClicked: root.positionAtLatest()

        contentItem: Text {
            id: newSegmentsLabel
            text: newSegmentsPill.text
            color: Theme.paperRaised
            font: newSegmentsPill.font
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 19
            color: newSegmentsPill.down ? Theme.accentDeep : Theme.accent
            border.width: Theme.hairline
            border.color: Theme.wash(Theme.paperRaised, 0.35)
        }
    }
}
