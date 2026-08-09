import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

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
    property var hourOptions: []
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

    function fontDisplayName(family) {
        const names = {
            "HanyiSentyJournal": "漢儀新蒂手札體",
            "Hanyi Senty Diary": "漢儀新蒂日記體",
            "SentyWen": "漢儀新蒂文徵明體",
            "SentyFountainPen": "新蒂美工鋼筆",
            "SentyOrchid": "新蒂君子蘭",
            "SentyCreek": "新蒂山泉體",
            "HanyiSentyZhangjizhi": "漢儀新蒂張即之體",
            "Hanyi Senty Lingfei Scroll": "漢儀新蒂靈飛經體",
            "SentyEtherealWander": "新蒂逍遙遊",
            "LXGW WenKai TC": "霞鶩文楷 TC v1.522"
        }
        return names[family] ? names[family] + "  ·  " + family : family
    }

    function microphoneIndex(picker) {
        for (let index = 0; index < journal.microphoneOptions.length; ++index) {
            if (journal.microphoneOptions[index].key === journal.selectedMicrophoneKey)
                return index
        }
        return -1
    }

    function syncMicrophonePickers() {
        microphonePicker.currentIndex = microphoneIndex(microphonePicker)
    }

    onActiveSheetChanged: {
        if (activeSheet === "settings") {
            recordsField.text = journal.recordsRoot
            previewSpin.value = journal.previewInterval
            silenceSpin.value = journal.endpointSilence
            maxSegmentSpin.value = journal.maxSegment
            startupCheck.checked = journal.startupEnabled
            updateCheck.checked = journal.updateCheckEnabled
            fontPicker.currentIndex = Math.max(0, fontPicker.find(journal.uiFontFamily))
            fontSizeSpin.value = journal.uiFontSize
            journal.resetMicrophoneSelection()
            journal.rescanMicrophones()
            Qt.callLater(window.syncMicrophonePickers)
        } else if (activeSheet === "hours") {
            hourOptions = journal.availableHours()
            hourPicker.currentIndex = hourOptions.length > 0 ? 0 : -1
        } else if (activeSheet === "vocabulary") {
            journal.refreshVocabulary()
        }
    }

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
        function onAppearanceChanged() {
            fontPicker.currentIndex = Math.max(0, fontPicker.find(journal.uiFontFamily))
            fontSizeSpin.value = journal.uiFontSize
        }
        function onAvailableFontsChanged() {
            fontPicker.currentIndex = Math.max(0, fontPicker.find(journal.uiFontFamily))
        }
        function onMicrophoneDevicesChanged() {
            window.syncMicrophonePickers()
        }
        function onMicrophoneSelectionChanged() {
            window.syncMicrophonePickers()
        }
        function onExpandedChanged() {
            window.activeSheet = ""
        }
    }

    component PaperButton: Button {
        id: paperButton
        readonly property bool contentWidthGuard: true
        implicitHeight: 38
        implicitWidth: Math.max(
            90,
            paperButtonLabel.implicitWidth + leftPadding + rightPadding
        )
        leftPadding: journal.uiFontScale > 1.4 ? 8 : 14
        rightPadding: leftPadding
        font.family: window.systemFontFamily
        font.pixelSize: window.fontPx(16)

        contentItem: Text {
            id: paperButtonLabel
            text: paperButton.text
            color: paperButton.enabled ? "#493F35" : "#A69B8E"
            font: paperButton.font
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 9
            color: paperButton.down ? "#DDD2C0" : paperButton.hovered ? "#F5EEE1" : "#ECE3D4"
            border.width: 1
            border.color: "#D0C3AF"
        }
    }

    component TextButton: Button {
        id: textButton
        readonly property bool contentWidthGuard: true
        implicitHeight: 36
        implicitWidth: Math.max(
            78,
            textButtonLabel.implicitWidth + leftPadding + rightPadding
        )
        leftPadding: journal.uiFontScale > 1.4 ? 4 : 10
        rightPadding: leftPadding
        flat: true
        font.family: window.systemFontFamily
        font.pixelSize: window.fontPx(15)

        contentItem: Text {
            id: textButtonLabel
            text: textButton.text
            color: textButton.enabled ? (textButton.down ? "#354D3D" : "#655A4F") :
                                        "#A69B8E"
            font: textButton.font
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 8
            color: textButton.down ? "#14789682" :
                   textButton.hovered ? "#0B789682" : "transparent"
        }
    }

    component IconButton: Button {
        id: iconButton
        implicitWidth: 30
        implicitHeight: 28
        font.family: window.systemFontFamily
        font.pixelSize: 16

        contentItem: Text {
            text: iconButton.text
            color: "#5C5044"
            font: iconButton.font
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 8
            color: iconButton.down ? "#DDD2C0" : iconButton.hovered ? "#EFE5D6" : "transparent"
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
        radius: journal.expanded ? 18 : 14
        color: "#F4EEE3"
        border.width: 1
        border.color: "#CFC2AF"
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
                    color: "#493F35"
                    font.family: window.diaryFontFamily
                    font.pixelSize: window.fontPx(journal.expanded ? 22 : 18)
                    font.weight: Font.DemiBold
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

        Item {
            id: compactContent
            objectName: "compactContent"
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: titleBar.bottom
            anchors.bottom: parent.bottom
            visible: !journal.expanded

            Rectangle {
                anchors.fill: parent
                color: window.monthTint
                opacity: 0.05
            }

            Item {
                id: compactInfo
                objectName: "compactInfo"
                z: 2
                anchors.left: parent.left
                anchors.leftMargin: 14
                anchors.right: parent.right
                anchors.rightMargin: 14
                anchors.top: parent.top
                anchors.bottom: parent.bottom

                Text {
                    id: compactBacklogText
                    objectName: "compactBacklogText"
                    anchors.top: parent.top
                    anchors.topMargin: 5
                    anchors.right: parent.right
                    text: journal.backlogText
                    color: "#776B5E"
                    font.family: window.systemFontFamily
                    font.pixelSize: window.fontPx(13)
                    horizontalAlignment: Text.AlignRight
                }

                Text {
                    id: compactPartialText
                    objectName: "compactPartialText"
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: compactBacklogText.bottom
                    anchors.topMargin: 6
                    anchors.bottom: compactActionRow.top
                    anchors.bottomMargin: 12
                    text: journal.partialText
                    wrapMode: Text.Wrap
                    maximumLineCount: journal.uiFontScale > 1.4 ? 1 : 2
                    elide: Text.ElideRight
                    color: "#3E3831"
                    font.family: window.systemFontFamily
                    font.pixelSize: window.fontPx(15)
                    lineHeight: 1.18
                    verticalAlignment: Text.AlignTop
                }

                RowLayout {
                    id: compactActionRow
                    objectName: "compactActionRow"
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    anchors.bottomMargin: 9
                    height: Math.max(32, window.fontPx(16) + 10)
                    spacing: 8

                    PaperButton {
                        id: compactPauseButton
                        objectName: "pauseButton"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        enabled: journal.recordingControlsEnabled
                        text: journal.paused ? "繼續聆聽" : "暫停"
                        onClicked: journal.togglePause()
                    }
                    PaperButton {
                        id: expandButton
                        objectName: "expandButton"
                        Layout.fillHeight: true
                        text: "今日紀錄"
                        onClicked: journal.toggleExpanded()
                    }
                }
            }
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
        Rectangle {
            id: sheetShade
            objectName: "sheetShade"
            anchors.fill: parent
            visible: journal.expanded && window.activeSheet !== ""
            color: "#624B4036"
            z: 30

            MouseArea {
                anchors.fill: parent
                onClicked: window.activeSheet = ""
            }

            Rectangle {
                id: sideSheet
                objectName: "sideSheet"
                anchors.top: parent.top
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                width: Math.min(430, Math.max(390, parent.width * 0.42))
                color: "#F8F5EEDD"
                border.width: 1
                border.color: "#D1C3B0"

                MouseArea { anchors.fill: parent }

                Text {
                    id: sheetTitle
                    objectName: "sheetTitle"
                    anchors.left: parent.left
                    anchors.leftMargin: 24
                    anchors.top: parent.top
                    anchors.topMargin: 28
                    text: "今日工具"
                    color: "#493F35"
                    font.family: window.systemFontFamily
                    font.pixelSize: window.fontPx(28)
                    font.weight: Font.DemiBold
                }

                IconButton {
                    anchors.right: parent.right
                    anchors.rightMargin: 16
                    anchors.verticalCenter: sheetTitle.verticalCenter
                    text: "×"
                    onClicked: window.activeSheet = ""
                }

                RowLayout {
                    id: drawerTabs
                    objectName: "utilityDrawerTabs"
                    anchors.left: parent.left
                    anchors.leftMargin: 20
                    anchors.right: parent.right
                    anchors.rightMargin: 20
                    anchors.top: sheetTitle.bottom
                    anchors.topMargin: 16
                    height: 38
                    spacing: 6

                    TextButton {
                        objectName: "settingsDrawerTab"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        text: "設定"
                        font.weight: window.activeSheet === "settings" ? Font.DemiBold : Font.Normal
                        onClicked: window.activeSheet = "settings"
                        background: Rectangle {
                            radius: 8
                            color: window.activeSheet === "settings" ? "#1B718A78" : "transparent"
                        }
                    }
                    TextButton {
                        objectName: "systemDrawerTab"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        text: "狀態"
                        font.weight: window.activeSheet === "system" ? Font.DemiBold : Font.Normal
                        onClicked: window.activeSheet = "system"
                        background: Rectangle {
                            radius: 8
                            color: window.activeSheet === "system" ? "#1B718A78" : "transparent"
                        }
                    }
                    TextButton {
                        objectName: "vocabularyDrawerTab"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        text: "字典"
                        font.weight: window.activeSheet === "vocabulary" ? Font.DemiBold : Font.Normal
                        onClicked: window.activeSheet = "vocabulary"
                        background: Rectangle {
                            radius: 8
                            color: window.activeSheet === "vocabulary" ? "#1B718A78" : "transparent"
                        }
                    }
                    TextButton {
                        objectName: "hoursDrawerTab"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        text: "時段"
                        font.weight: window.activeSheet === "hours" ? Font.DemiBold : Font.Normal
                        onClicked: window.activeSheet = "hours"
                        background: Rectangle {
                            radius: 8
                            color: window.activeSheet === "hours" ? "#1B718A78" : "transparent"
                        }
                    }
                }

                ColumnLayout {
                    id: vocabularySheet
                    objectName: "vocabularySheet"
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: drawerTabs.bottom
                    anchors.bottom: parent.bottom
                    anchors.margins: 24
                    spacing: 13
                    visible: window.activeSheet === "vocabulary"

                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            Layout.fillWidth: true
                            text: "自動學習使用者修正"
                            color: "#493F35"
                            font.family: window.systemFontFamily
                            font.pixelSize: window.fontPx(17)
                            font.weight: Font.DemiBold
                        }
                        Switch {
                            id: vocabularyLearningSwitch
                            objectName: "vocabularyLearningSwitch"
                            checked: journal.vocabularyLearningEnabled
                            font.family: window.systemFontFamily
                            font.pixelSize: window.fontPx(15)
                            onToggled: {
                                if (checked !== journal.vocabularyLearningEnabled &&
                                        !journal.setVocabularyLearningEnabled(checked))
                                    checked = journal.vocabularyLearningEnabled
                            }
                        }
                    }
                    Text {
                        Layout.fillWidth: true
                        text: "停用後仍會保留修正文字與既有字典，只停止從後續修正學習新詞。辨識提示目前使用累計 2 次以上的詞語。"
                        wrapMode: Text.Wrap
                        color: "#75685B"
                        font.family: window.systemFontFamily
                        font.pixelSize: window.fontPx(14)
                    }
                    Rectangle {
                        Layout.fillWidth: true
                        height: 1
                        color: "#D8CBBA"
                    }
                    Text {
                        objectName: "vocabularyEmpty"
                        Layout.fillWidth: true
                        visible: journal.vocabularyEntries.length === 0
                        text: "目前沒有已學詞語"
                        color: "#95887A"
                        font.family: window.systemFontFamily
                        font.pixelSize: window.fontPx(16)
                    }
                    ListView {
                        id: vocabularyList
                        objectName: "vocabularyList"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        visible: journal.vocabularyEntries.length > 0
                        clip: true
                        spacing: 7
                        model: journal.vocabularyEntries
                        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                        delegate: Rectangle {
                            id: vocabularyEntry
                            required property var modelData
                            objectName: "vocabularyEntry"
                            width: ListView.view.width
                            height: Math.max(48, vocabularyTerm.implicitHeight + 16)
                            radius: 8
                            color: "#55ECE3D4"
                            border.width: 1
                            border.color: "#D8CBBA"

                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 12
                                anchors.rightMargin: 8
                                spacing: 10
                                Text {
                                    id: vocabularyTerm
                                    objectName: "vocabularyTerm"
                                    Layout.fillWidth: true
                                    text: vocabularyEntry.modelData.term
                                    elide: Text.ElideRight
                                    color: "#493F35"
                                    font.family: window.systemFontFamily
                                    font.pixelSize: window.fontPx(17)
                                }
                                Text {
                                    objectName: "vocabularyCount"
                                    text: vocabularyEntry.modelData.count + " 次"
                                    color: "#776B5E"
                                    font.family: window.systemFontFamily
                                    font.pixelSize: window.fontPx(14)
                                }
                                TextButton {
                                    objectName: "deleteVocabularyTermButton"
                                    text: "刪除"
                                    onClicked: journal.deleteVocabularyTerm(
                                        vocabularyEntry.modelData.term
                                    )
                                }
                            }
                        }
                    }
                    PaperButton {
                        id: clearVocabularyButton
                        objectName: "clearVocabularyButton"
                        Layout.fillWidth: true
                        enabled: journal.vocabularyEntries.length > 0
                        text: "清空全部已學詞"
                        onClicked: window.askClearVocabulary()
                    }
                }

                Flickable {
                    id: settingsSheet
                    objectName: "settingsSheet"
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: drawerTabs.bottom
                    anchors.bottom: parent.bottom
                    anchors.margins: 24
                    visible: window.activeSheet === "settings"
                    clip: true
                    contentWidth: width
                    contentHeight: settingsForm.implicitHeight
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                    Column {
                        id: settingsForm
                        width: settingsSheet.width - 10
                        spacing: 13

                    Text {
                        text: "日記字體"
                        color: "#695D50"
                        font.family: window.systemFontFamily
                        font.pixelSize: window.fontPx(16)
                    }
                    ComboBox {
                        id: fontPicker
                        objectName: "fontPicker"
                        width: parent.width
                        model: journal.availableFontFamilies
                        displayText: window.fontDisplayName(currentText)
                        font.family: currentText || window.systemFontFamily
                        font.pixelSize: window.fontPx(16)
                        delegate: ItemDelegate {
                            required property var modelData
                            width: fontPicker.width
                            text: window.fontDisplayName(modelData)
                            font.family: modelData
                            font.pixelSize: window.fontPx(16)
                        }
                        onActivated: journal.applyAppearance(currentText, fontSizeSpin.value)
                    }
                    Text {
                        text: "日記字級"
                        color: "#695D50"
                        font.family: window.systemFontFamily
                        font.pixelSize: window.fontPx(16)
                    }
                    SpinBox {
                        id: fontSizeSpin
                        objectName: "fontSizeSpin"
                        width: parent.width
                        from: journal.minUiFontSize
                        to: journal.maxUiFontSize
                        value: journal.uiFontSize
                        editable: true
                        font.family: window.systemFontFamily
                        font.pixelSize: window.fontPx(16)
                        onValueModified: journal.applyAppearance(fontPicker.currentText, value)
                    }
                    Text {
                        width: parent.width
                        text: "本機字體資料夾\n" + journal.fontFolder
                        wrapMode: Text.WrapAnywhere
                        color: "#86796B"
                        font.family: window.systemFontFamily
                        font.pixelSize: window.fontPx(13)
                    }
                    GridLayout {
                        width: parent.width
                        columns: journal.uiFontScale > 1.4 ? 1 : 2
                        TextButton {
                            Layout.fillWidth: true
                            text: "開啟字體資料夾"
                            onClicked: journal.openFontFolder()
                        }
                        TextButton {
                            Layout.fillWidth: true
                            text: "重新掃描"
                            onClicked: journal.rescanFonts()
                        }
                    }
                    Rectangle { width: parent.width; height: 1; color: "#D8CBBA" }
                    Text {
                        text: "紀錄資料夾"
                        color: "#695D50"
                        font.family: window.systemFontFamily
                        font.pixelSize: window.fontPx(16)
                    }
                    RowLayout {
                        width: parent.width
                        TextField {
                            id: recordsField
                            objectName: "recordsField"
                            Layout.fillWidth: true
                            text: journal.recordsRoot
                            color: "#493F35"
                            font.family: window.systemFontFamily
                            font.pixelSize: window.fontPx(16)
                            background: Rectangle { radius: 7; color: "#FFFDF8"; border.color: "#CFC2AF" }
                        }
                        PaperButton {
                            text: "瀏覽…"
                            onClicked: recordsField.text = journal.chooseRecordsFolder(recordsField.text)
                        }
                    }
                    CheckBox {
                        id: startupCheck
                        objectName: "startupCheck"
                        width: parent.width
                        checked: journal.startupEnabled
                        text: "登入 Windows 後自動啟動"
                        font.family: window.systemFontFamily
                        font.pixelSize: window.fontPx(15)
                    }
                    CheckBox {
                        id: updateCheck
                        objectName: "updateCheck"
                        width: parent.width
                        checked: journal.updateCheckEnabled
                        text: "最多每 24 小時檢查 GitHub 新版本"
                        font.family: window.systemFontFamily
                        font.pixelSize: window.fontPx(15)
                    }
                    Text {
                        width: parent.width
                        text: "更新只會顯示提示，不會背景下載或自動安裝。"
                        wrapMode: Text.Wrap
                        color: "#86796B"
                        font.family: window.systemFontFamily
                        font.pixelSize: window.fontPx(13)
                    }
                    Text {
                        width: parent.width
                        text: "Auto Speech Journal v" + journal.appVersion
                        color: "#86796B"
                        font.family: window.systemFontFamily
                        font.pixelSize: window.fontPx(13)
                    }
                    PaperButton {
                        objectName: "resumeOnboardingButton"
                        width: parent.width
                        visible: !journal.onboardingCompleted
                        text: "繼續首次設定"
                        onClicked: {
                            window.activeSheet = ""
                            journal.openOnboarding()
                        }
                    }
                    Text { text: "預覽間隔（毫秒）"; color: "#695D50"; font.family: window.systemFontFamily; font.pixelSize: window.fontPx(16) }
                    SpinBox { id: previewSpin; objectName: "previewSpin"; width: parent.width; from: 250; to: 10000; value: journal.previewInterval; editable: true; font.family: window.systemFontFamily; font.pixelSize: window.fontPx(16) }
                    Text { text: "靜音分段（毫秒）"; color: "#695D50"; font.family: window.systemFontFamily; font.pixelSize: window.fontPx(16) }
                    SpinBox { id: silenceSpin; objectName: "silenceSpin"; width: parent.width; from: 250; to: 10000; value: journal.endpointSilence; editable: true; font.family: window.systemFontFamily; font.pixelSize: window.fontPx(16) }
                    Text { text: "片段最長（毫秒）"; color: "#695D50"; font.family: window.systemFontFamily; font.pixelSize: window.fontPx(16) }
                    SpinBox { id: maxSegmentSpin; objectName: "maxSegmentSpin"; width: parent.width; from: 1000; to: 120000; value: journal.maxSegment; editable: true; stepSize: 1000; font.family: window.systemFontFamily; font.pixelSize: window.fontPx(16) }
                    Rectangle { width: parent.width; height: 1; color: "#D8CBBA" }
                    Text {
                        text: "麥克風"
                        color: "#695D50"
                        font.family: window.systemFontFamily
                        font.pixelSize: window.fontPx(16)
                        font.weight: Font.DemiBold
                    }
                    ComboBox {
                        id: microphonePicker
                        objectName: "microphonePicker"
                        width: parent.width
                        model: journal.microphoneOptions
                        enabled: journal.onboardingCompleted
                        textRole: "label"
                        valueRole: "key"
                        currentIndex: -1
                        displayText: currentIndex >= 0 ? currentText : "請選擇麥克風"
                        font.family: window.systemFontFamily
                        font.pixelSize: window.fontPx(15)
                        delegate: ItemDelegate {
                            required property var modelData
                            width: microphonePicker.width
                            enabled: Boolean(modelData.selectable)
                            text: modelData.label
                            font.family: window.systemFontFamily
                            font.pixelSize: window.fontPx(15)
                        }
                        onActivated: {
                            if (!journal.selectMicrophone(currentValue))
                                Qt.callLater(window.syncMicrophonePickers)
                        }
                    }
                    Text {
                        id: microphoneRouteStatus
                        objectName: "microphoneRouteStatus"
                        width: parent.width
                        text: journal.inputRouteNoticeText
                        visible: text.length > 0 && !journal.inputFallbackActive
                        wrapMode: Text.Wrap
                        color: journal.inputFallbackActive ? "#9A642E" : "#86796B"
                        font.family: window.systemFontFamily
                        font.pixelSize: window.fontPx(14)
                    }
                    Rectangle {
                        objectName: "microphoneFallbackWarning"
                        width: parent.width
                        height: fallbackText.implicitHeight + 20
                        radius: 8
                        visible: journal.inputFallbackActive
                        color: "#FFF2DC"
                        border.color: "#D6AA6D"
                        Text {
                            id: fallbackText
                            anchors.fill: parent
                            anchors.margins: 10
                            text: journal.inputRouteNoticeText
                            wrapMode: Text.Wrap
                            color: "#815528"
                            font.family: window.systemFontFamily
                            font.pixelSize: window.fontPx(14)
                        }
                    }
                    Text {
                        width: parent.width
                        text: "偏好：" + journal.preferredInputName +
                              "\n目前收音：" + (journal.activeInputName || "尚未開始")
                        wrapMode: Text.Wrap
                        color: "#86796B"
                        font.family: window.systemFontFamily
                        font.pixelSize: window.fontPx(14)
                    }
                    RowLayout {
                        width: parent.width
                        TextButton {
                            id: microphoneRescanButton
                            objectName: "microphoneRescanButton"
                            Layout.fillWidth: true
                            text: "重新掃描"
                            enabled: !journal.microphoneTestRunning && !journal.inputSwitching
                            onClicked: journal.rescanMicrophones()
                        }
                        PaperButton {
                            id: microphoneTestButton
                            objectName: "microphoneTestButton"
                            Layout.fillWidth: true
                            text: journal.microphoneTestRunning ? "測試中…" : "測試所選麥克風"
                            enabled: journal.onboardingCompleted &&
                                     journal.selectedMicrophoneKey !== "" &&
                                     !journal.microphoneTestRunning && !journal.inputSwitching
                            onClicked: journal.testSelectedMicrophone()
                        }
                    }
                    Rectangle {
                        objectName: "microphoneTestMeter"
                        width: parent.width
                        height: 8
                        radius: 4
                        visible: journal.microphoneTestState !== "idle"
                        color: "#E1D7C8"
                        Rectangle {
                            width: parent.width * journal.microphoneTestLevel
                            height: parent.height
                            radius: parent.radius
                            color: journal.microphoneTestState === "error" ? "#B85C4A" :
                                   journal.microphoneTestState === "warning" ? "#B88647" : "#718C78"
                        }
                    }
                    Text {
                        objectName: "microphoneTestStatus"
                        width: parent.width
                        visible: journal.microphoneTestMessage !== ""
                        text: journal.microphoneTestMessage
                        wrapMode: Text.Wrap
                        color: journal.microphoneTestState === "error" ? "#A34739" :
                               journal.microphoneTestState === "warning" ? "#94622E" : "#5F7864"
                        font.family: window.systemFontFamily
                        font.pixelSize: window.fontPx(14)
                    }
                    PaperButton {
                        objectName: "retryPreferredInputButton"
                        width: parent.width
                        visible: journal.inputFallbackActive && journal.preferredInputAvailable
                        enabled: !journal.inputSwitching
                        text: "切回偏好麥克風"
                        onClicked: journal.retryPreferredInput()
                    }
                    PaperButton {
                        objectName: "retryRecordingEngineButton"
                        width: parent.width
                        visible: journal.recordingEngineNeedsStart
                        text: "重新啟動錄音引擎"
                        onClicked: journal.startControllerIfReady()
                    }
                    TextButton {
                        objectName: "deferMicrophoneAfterFailureButton"
                        width: parent.width
                        visible: journal.recordingEngineNeedsStart
                        text: "稍後設定麥克風，先進入主介面"
                        font.pixelSize: Math.min(window.fontPx(15), 22)
                        onClicked: journal.deferMicrophoneAfterStartFailure()
                    }
                    PaperButton {
                        objectName: "settingsSaveButton"
                        width: parent.width
                        enabled: !journal.inputSwitching && !journal.microphoneTestRunning &&
                                 journal.settingsMicrophoneSelectionValid
                        text: journal.inputSwitching ? "正在切換麥克風…" : "儲存設定"
                        onClicked: {
                            journal.applySettings(recordsField.text, previewSpin.value,
                                                  silenceSpin.value, maxSegmentSpin.value,
                                                  journal.selectedMicrophoneKey,
                                                  startupCheck.checked, updateCheck.checked)
                        }
                    }
                    Rectangle { width: parent.width; height: 1; color: "#D8CBBA" }
                    RowLayout {
                        width: parent.width
                        Text {
                            text: "最近設定紀錄"
                            color: "#695D50"
                            font.family: window.systemFontFamily
                            font.pixelSize: window.fontPx(16)
                            font.weight: Font.DemiBold
                        }
                        Item { Layout.fillWidth: true }
                        TextButton {
                            objectName: "openSettingsHistoryButton"
                            text: "開啟完整紀錄"
                            onClicked: journal.openSettingsHistoryFile()
                        }
                    }
                    Text {
                        objectName: "settingsHistoryEmpty"
                        visible: journal.settingsHistoryEntries.length === 0
                        width: parent.width
                        text: "尚無設定變更紀錄"
                        color: "#95887A"
                        font.family: window.systemFontFamily
                        font.pixelSize: window.fontPx(14)
                    }
                    Repeater {
                        model: journal.settingsHistoryEntries
                        delegate: Rectangle {
                            id: historyEntry
                            required property var modelData
                            objectName: "settingsHistoryEntry"
                            width: settingsForm.width
                            height: historyEntryText.implicitHeight + 20
                            radius: 8
                            color: "#55ECE3D4"
                            border.width: 1
                            border.color: "#D8CBBA"

                            Column {
                                id: historyEntryText
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.top: parent.top
                                anchors.margins: 10
                                spacing: 5
                                Text {
                                    width: parent.width
                                    text: historyEntry.modelData.summary
                                    wrapMode: Text.Wrap
                                    color: "#55493D"
                                    font.family: window.systemFontFamily
                                    font.pixelSize: window.fontPx(14)
                                    font.weight: Font.DemiBold
                                }
                                Text {
                                    width: parent.width
                                    text: historyEntry.modelData.details
                                    wrapMode: Text.WrapAnywhere
                                    color: "#776B5E"
                                    font.family: window.systemFontFamily
                                    font.pixelSize: window.fontPx(13)
                                    lineHeight: 1.2
                                }
                            }
                        }
                    }
                    }
                }

                Column {
                    id: systemSheet
                    objectName: "systemSheet"
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: drawerTabs.bottom
                    anchors.margins: 24
                    spacing: 13
                    visible: window.activeSheet === "system"

                    Text { text: journal.stateText; color: "#493F35"; font.family: window.systemFontFamily; font.pixelSize: window.fontPx(22) }
                    Text { width: parent.width; text: journal.statusMessage; wrapMode: Text.Wrap; color: "#6F6255"; font.family: window.systemFontFamily; font.pixelSize: window.fontPx(16) }
                    Rectangle { width: parent.width; height: 1; color: "#D8CBBA" }
                    Text { text: "場景狀態：" + journal.sceneKey; color: "#6F6255"; font.family: window.systemFontFamily; font.pixelSize: window.fontPx(16) }
                    Text { text: journal.backlogText; color: "#6F6255"; font.family: window.systemFontFamily; font.pixelSize: window.fontPx(16) }
                    Text {
                        objectName: "systemMicrophoneStatus"
                        text: "麥克風偏好：" + journal.preferredInputName +
                              "\n目前收音：" + (journal.activeInputName || "尚未開始") +
                              (journal.inputRouteNoticeText
                                  ? "\n" + journal.inputRouteNoticeText
                                  : "")
                        width: parent.width
                        wrapMode: Text.Wrap
                        color: journal.inputFallbackActive ? "#9A642E" : "#6F6255"
                        font.family: window.systemFontFamily
                        font.pixelSize: window.fontPx(16)
                    }
                    Text { text: "今日資料版本：" + journal.timelineRevision; color: "#6F6255"; font.family: window.systemFontFamily; font.pixelSize: window.fontPx(16) }
                    PaperButton { width: parent.width; text: "開啟紀錄資料夾"; onClicked: journal.openRecordsFolder() }
                    PaperButton { width: parent.width; text: "結束程式"; onClicked: window.askExit() }
                }

                Column {
                    id: hoursSheet
                    objectName: "hoursSheet"
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: drawerTabs.bottom
                    anchors.margins: 24
                    spacing: 13
                    visible: window.activeSheet === "hours"

                    Text {
                        width: parent.width
                        text: "永久刪除會同步清除 SQLite、重建輸出，並移除仍存在的暫存音訊。"
                        wrapMode: Text.Wrap
                        color: "#75685B"
                        font.family: window.systemFontFamily
                        font.pixelSize: window.fontPx(15)
                    }
                    ComboBox {
                        id: hourPicker
                        objectName: "hourPicker"
                        width: parent.width
                        model: window.hourOptions
                        font.family: window.systemFontFamily
                        font.pixelSize: window.fontPx(16)
                    }
                    Text {
                        visible: window.hourOptions.length === 0
                        text: "目前沒有可刪除的歷史時段"
                        color: "#95887A"
                        font.family: window.systemFontFamily
                        font.pixelSize: window.fontPx(16)
                    }
                    PaperButton {
                        width: parent.width
                        enabled: window.hourOptions.length > 0
                        text: "永久刪除選取時段"
                        onClicked: window.askDelete(hourPicker.currentText)
                    }
                }
            }
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
                                    if (journal.deleteHour(hour)) {
                                        window.hourOptions = journal.availableHours()
                                        hourPicker.currentIndex = window.hourOptions.length > 0 ? 0 : -1
                                    }
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
