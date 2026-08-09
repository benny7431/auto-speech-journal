import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Flickable {
    id: root
    objectName: "settingsSheet"
    clip: true
    contentWidth: width
    contentHeight: settingsForm.implicitHeight
    boundsBehavior: Flickable.StopAtBounds

    required property var journal

    signal closeRequested()

    function px(size) {
        return Math.max(1, Math.round(size * journal.uiFontScale))
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

    function microphoneIndex() {
        for (let index = 0; index < journal.microphoneOptions.length; ++index) {
            if (journal.microphoneOptions[index].key === journal.selectedMicrophoneKey)
                return index
        }
        return -1
    }

    function syncMicrophonePickers() {
        microphonePicker.currentIndex = microphoneIndex()
    }

    /*
     * Reopening the drawer restores every field from the view model, so an
     * abandoned edit never survives as a stale draft. tests/test_ui.py holds a
     * Python reference to recordsField across a close/reopen cycle and asserts
     * the same object was reset, which is why the sheets are never behind a
     * Loader.
     */
    function reload() {
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
        Qt.callLater(syncMicrophonePickers)
    }

    Connections {
        target: root.journal

        function onAppearanceChanged() {
            fontPicker.currentIndex = Math.max(0, fontPicker.find(root.journal.uiFontFamily))
            fontSizeSpin.value = root.journal.uiFontSize
        }
        function onAvailableFontsChanged() {
            fontPicker.currentIndex = Math.max(0, fontPicker.find(root.journal.uiFontFamily))
        }
        function onMicrophoneDevicesChanged() {
            root.syncMicrophonePickers()
        }
        function onMicrophoneSelectionChanged() {
            root.syncMicrophonePickers()
        }
    }

    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

    Column {
        id: settingsForm
        width: root.width - 10
        spacing: 13

    Text {
        text: "日記字體"
        color: "#695D50"
        font.family: journal.systemFontFamily
        font.pixelSize: root.px(16)
    }
    ComboBox {
        id: fontPicker
        objectName: "fontPicker"
        width: parent.width
        model: journal.availableFontFamilies
        displayText: root.fontDisplayName(currentText)
        font.family: currentText || journal.systemFontFamily
        font.pixelSize: root.px(16)
        delegate: ItemDelegate {
            required property var modelData
            width: fontPicker.width
            text: root.fontDisplayName(modelData)
            font.family: modelData
            font.pixelSize: root.px(16)
        }
        onActivated: journal.applyAppearance(currentText, fontSizeSpin.value)
    }
    Text {
        text: "日記字級"
        color: "#695D50"
        font.family: journal.systemFontFamily
        font.pixelSize: root.px(16)
    }
    SpinBox {
        id: fontSizeSpin
        objectName: "fontSizeSpin"
        width: parent.width
        from: journal.minUiFontSize
        to: journal.maxUiFontSize
        value: journal.uiFontSize
        editable: true
        font.family: journal.systemFontFamily
        font.pixelSize: root.px(16)
        onValueModified: journal.applyAppearance(fontPicker.currentText, value)
    }
    Text {
        width: parent.width
        text: "本機字體資料夾\n" + journal.fontFolder
        wrapMode: Text.WrapAnywhere
        color: "#86796B"
        font.family: journal.systemFontFamily
        font.pixelSize: root.px(13)
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
        font.family: journal.systemFontFamily
        font.pixelSize: root.px(16)
    }
    RowLayout {
        width: parent.width
        TextField {
            id: recordsField
            objectName: "recordsField"
            Layout.fillWidth: true
            text: journal.recordsRoot
            color: "#493F35"
            font.family: journal.systemFontFamily
            font.pixelSize: root.px(16)
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
        font.family: journal.systemFontFamily
        font.pixelSize: root.px(15)
    }
    CheckBox {
        id: updateCheck
        objectName: "updateCheck"
        width: parent.width
        checked: journal.updateCheckEnabled
        text: "最多每 24 小時檢查 GitHub 新版本"
        font.family: journal.systemFontFamily
        font.pixelSize: root.px(15)
    }
    Text {
        width: parent.width
        text: "更新只會顯示提示，不會背景下載或自動安裝。"
        wrapMode: Text.Wrap
        color: "#86796B"
        font.family: journal.systemFontFamily
        font.pixelSize: root.px(13)
    }
    Text {
        width: parent.width
        text: "Auto Speech Journal v" + journal.appVersion
        color: "#86796B"
        font.family: journal.systemFontFamily
        font.pixelSize: root.px(13)
    }
    PaperButton {
        objectName: "resumeOnboardingButton"
        width: parent.width
        visible: !journal.onboardingCompleted
        text: "繼續首次設定"
        onClicked: {
            root.closeRequested()
            journal.openOnboarding()
        }
    }
    Text { text: "預覽間隔（毫秒）"; color: "#695D50"; font.family: journal.systemFontFamily; font.pixelSize: root.px(16) }
    SpinBox { id: previewSpin; objectName: "previewSpin"; width: parent.width; from: 250; to: 10000; value: journal.previewInterval; editable: true; font.family: journal.systemFontFamily; font.pixelSize: root.px(16) }
    Text { text: "靜音分段（毫秒）"; color: "#695D50"; font.family: journal.systemFontFamily; font.pixelSize: root.px(16) }
    SpinBox { id: silenceSpin; objectName: "silenceSpin"; width: parent.width; from: 250; to: 10000; value: journal.endpointSilence; editable: true; font.family: journal.systemFontFamily; font.pixelSize: root.px(16) }
    Text { text: "片段最長（毫秒）"; color: "#695D50"; font.family: journal.systemFontFamily; font.pixelSize: root.px(16) }
    SpinBox { id: maxSegmentSpin; objectName: "maxSegmentSpin"; width: parent.width; from: 1000; to: 120000; value: journal.maxSegment; editable: true; stepSize: 1000; font.family: journal.systemFontFamily; font.pixelSize: root.px(16) }
    Rectangle { width: parent.width; height: 1; color: "#D8CBBA" }
    Text {
        text: "麥克風"
        color: "#695D50"
        font.family: journal.systemFontFamily
        font.pixelSize: root.px(16)
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
        font.family: journal.systemFontFamily
        font.pixelSize: root.px(15)
        delegate: ItemDelegate {
            required property var modelData
            width: microphonePicker.width
            enabled: Boolean(modelData.selectable)
            text: modelData.label
            font.family: journal.systemFontFamily
            font.pixelSize: root.px(15)
        }
        onActivated: {
            if (!journal.selectMicrophone(currentValue))
                Qt.callLater(root.syncMicrophonePickers)
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
        font.family: journal.systemFontFamily
        font.pixelSize: root.px(14)
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
            font.family: journal.systemFontFamily
            font.pixelSize: root.px(14)
        }
    }
    Text {
        width: parent.width
        text: "偏好：" + journal.preferredInputName +
              "\n目前收音：" + (journal.activeInputName || "尚未開始")
        wrapMode: Text.Wrap
        color: "#86796B"
        font.family: journal.systemFontFamily
        font.pixelSize: root.px(14)
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
        font.family: journal.systemFontFamily
        font.pixelSize: root.px(14)
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
        font.pixelSize: Math.min(root.px(15), 22)
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
            font.family: journal.systemFontFamily
            font.pixelSize: root.px(16)
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
        font.family: journal.systemFontFamily
        font.pixelSize: root.px(14)
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
                    font.family: journal.systemFontFamily
                    font.pixelSize: root.px(14)
                    font.weight: Font.DemiBold
                }
                Text {
                    width: parent.width
                    text: historyEntry.modelData.details
                    wrapMode: Text.WrapAnywhere
                    color: "#776B5E"
                    font.family: journal.systemFontFamily
                    font.pixelSize: root.px(13)
                    lineHeight: 1.2
                }
            }
        }
    }
    }
}
