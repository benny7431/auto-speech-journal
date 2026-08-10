import QtQuick
import QtQuick.Controls

import "."

/*
 * Today's saved segments, as a list of cards grouped under collapsible hours.
 *
 * Collapse lives here rather than in the model. The model stays a flat list of
 * segments, so the jump-to-latest and scroll-preservation handshake below
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
    property real savedContentOffset: 0
    property string savedAnchorSegmentId: ""
    property real savedAnchorViewportY: 0

    property var collapsedHours: ({})

    property alias listView: timelineList
    property alias newSegmentsIndicator: newSegmentsPill

    function px(size) {
        return Math.max(1, Math.round(size * fontScale))
    }

    /*
     * The next few hours after the newest entry, as a hint that the day is still
     * open. Deliberately not every hour to midnight: fourteen empty rules is both
     * over-literal and long enough to dominate the scroll extent, which pushed
     * the actual entries off the top when jumping to the latest one.
     */
    readonly property int upcomingHourCount: 3
    readonly property var remainingHours: {
        const last = journal.timelineModel.lastHourLabel
        if (!last)
            return []
        const start = parseInt(last.slice(0, 2)) + 1
        const hours = []
        for (let hour = start; hour <= Math.min(23, start + upcomingHourCount - 1); ++hour)
            hours.push(("0" + hour).slice(-2) + ":00")
        return hours
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

    /*
     * The newest entry, not the end of the content. The footer draws the hours
     * still to come, so positioning at the end of the view would scroll the whole
     * day off the top and land the reader on empty rules.
     */
    function positionAtLatest() {
        timelineList.positionViewAtIndex(timelineList.count - 1, ListView.End)
        userScrolledBack = false
        newSegmentsPill.visible = false
    }

    function openAtLatest() {
        timelineWasOpened = false
        userScrolledBack = false
        newSegmentsPill.visible = false
        journal.showAboutToOpen()
        Qt.callLater(function() {
            root.positionAtLatest()
            timelineWasOpened = true
        })
    }

    function captureScrollPosition() {
        savedContentOffset = timelineList.contentY - timelineList.originY
        savedAnchorSegmentId = ""
        savedAnchorViewportY = 0

        const index = timelineList.indexAt(
            Math.max(1, timelineList.width / 2),
            timelineList.contentY + 1
        )
        if (index < 0)
            return
        const item = timelineList.itemAtIndex(index)
        if (!item)
            return
        savedAnchorSegmentId = item.segmentId
        savedAnchorViewportY = item.y - timelineList.contentY
    }

    function restoreScrollPosition() {
        timelineList.forceLayout()
        const anchorIndex = journal.timelineModel.indexForSegmentId(savedAnchorSegmentId)
        if (anchorIndex >= 0) {
            timelineList.positionViewAtIndex(anchorIndex, ListView.Beginning)
            Qt.callLater(function() {
                timelineList.forceLayout()
                const item = timelineList.itemAtIndex(anchorIndex)
                if (item)
                    timelineList.contentY = item.y - root.savedAnchorViewportY
            })
            return
        }

        const maximumY = timelineList.originY + Math.max(
            0,
            timelineList.contentHeight - timelineList.height
        )
        timelineList.contentY = Math.min(
            timelineList.originY + root.savedContentOffset,
            maximumY
        )
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
            root.captureScrollPosition()
        }

        function onTimelineUpdated(added) {
            if (!root.journal.expanded)
                return
            const preservePosition = root.userScrolledBack ||
                                     root.journal.timelineModel.hasActiveEdit
            if (!root.timelineWasOpened || !preservePosition) {
                Qt.callLater(function() { root.positionAtLatest() })
                return
            }

            Qt.callLater(function() { root.restoreScrollPosition() })
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
            font.pixelSize: root.px(26)
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

            /*
             * The hours still ahead, drawn as empty rules. Without them the page
             * simply stops and the rest of the window reads as a rendering bug
             * rather than as a day that is not over yet.
             */
            footer: Column {
                width: timelineList.width
                visible: timelineList.count > 0
                topPadding: Theme.spaceSm

                Repeater {
                    model: root.remainingHours

                    delegate: Item {
                        id: futureHour
                        required property var modelData
                        readonly property real gutter: Math.round(
                            64 * Math.min(root.fontScale, 1.35)
                        )
                        width: timelineList.width
                        height: Math.round(root.px(16) + 26)

                        Text {
                            anchors.left: parent.left
                            anchors.verticalCenter: parent.verticalCenter
                            width: futureHour.gutter - Theme.spaceSm
                            horizontalAlignment: Text.AlignRight
                            text: futureHour.modelData
                            color: Theme.inkFaint
                            opacity: 0.5
                            font.family: root.systemFontFamily
                            font.pixelSize: root.px(13)
                        }
                        Rectangle {
                            anchors.left: parent.left
                            anchors.leftMargin: futureHour.gutter
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            height: Math.max(1, Theme.hairline)
                            color: Theme.lineSoft
                            opacity: 0.6
                        }
                    }
                }
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
