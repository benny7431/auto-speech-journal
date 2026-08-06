import QtQuick
import QtQuick.Controls

Item {
    id: root
    objectName: "soundRiver"

    required property var journal
    required property string systemFontFamily
    required property string diaryFontFamily
    property real fontScale: 1
    property color accentColor: "#718A78"
    property bool timelineWasOpened: false
    property bool userScrolledBack: false
    property real savedContentY: 0

    property alias listView: timelineList
    property alias newSegmentsIndicator: newSegmentsPill

    function px(size) {
        return Math.max(1, Math.round(size * fontScale))
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

    Text {
        id: timelineTitle
        objectName: "timelineTitle"
        anchors.left: parent.left
        anchors.top: parent.top
        text: "今日聲音時間河流"
        color: "#343A34"
        font.family: root.diaryFontFamily
        font.pixelSize: root.px(28)
        font.weight: Font.DemiBold
    }

    Canvas {
        id: readingMist
        anchors.left: parent.left
        anchors.leftMargin: 92
        anchors.right: parent.right
        anchors.top: timelineTitle.bottom
        anchors.topMargin: 8
        anchors.bottom: parent.bottom
        opacity: 0.78

        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            if (width <= 0 || height <= 0)
                return
            const radius = Math.max(width * 0.72, height * 0.9)
            const mist = ctx.createRadialGradient(
                width * 0.48, height * 0.34, 0,
                width * 0.48, height * 0.34, radius
            )
            mist.addColorStop(0, "rgba(255,253,248,0.62)")
            mist.addColorStop(0.58, "rgba(255,253,248,0.38)")
            mist.addColorStop(1, "rgba(255,253,248,0)")
            ctx.fillStyle = mist
            ctx.fillRect(0, 0, width, height)
        }
    }

    ListView {
        id: timelineList
        objectName: "timelineList"
        readonly property color idleSegmentColor: "transparent"
        readonly property real segmentBorderWidth: 0
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: timelineTitle.bottom
        anchors.topMargin: 10
        anchors.bottom: parent.bottom
        clip: true
        spacing: 6
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
            width: ListView.view.width - 10
            systemFontFamily: root.systemFontFamily
            diaryFontFamily: root.diaryFontFamily
            fontScale: root.fontScale
            onBeginEdit: function(segmentId) { root.journal.beginEdit(segmentId) }
            onUpdateDraft: function(segmentId, text) {
                root.journal.updateDraft(segmentId, text)
            }
            onSaveEdit: function(segmentId) { root.journal.saveEdit(segmentId) }
            onCancelEdit: function(segmentId) { root.journal.cancelEdit(segmentId) }
        }

        Text {
            anchors.centerIn: parent
            visible: timelineList.count === 0
            width: Math.min(parent.width - 80, 480)
            text: "今天還沒有已保存的片段\n開始說話後，聲跡會沿著時間河流出現"
            horizontalAlignment: Text.AlignHCenter
            color: "#777A72"
            font.family: root.systemFontFamily
            font.pixelSize: root.px(16)
            lineHeight: 1.5
        }
    }

    Button {
        id: newSegmentsPill
        objectName: "newSegmentsPill"
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 18
        visible: false
        implicitWidth: Math.max(190, newSegmentsLabel.implicitWidth + 32)
        implicitHeight: 38
        leftPadding: 16
        rightPadding: 16
        text: "有新片段  ·  回到最新"
        font.family: root.systemFontFamily
        font.pixelSize: root.px(14)
        onClicked: root.positionAtLatest()

        contentItem: Text {
            id: newSegmentsLabel
            text: newSegmentsPill.text
            color: "#FFFDF8"
            font: newSegmentsPill.font
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 19
            color: newSegmentsPill.down ? "#596F5D" : "#6D856F"
            border.width: 1
            border.color: "#55FFFFFF"
        }
    }
}
