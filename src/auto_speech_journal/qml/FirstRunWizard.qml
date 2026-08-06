import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root
    objectName: "onboardingOverlay"

    required property var viewModel
    required property var hostWindow

    onVisibleChanged: {
        if (visible) {
            onboardingRecordsField.text = viewModel.onboardingRecordsRoot
            onboardingStartupCheck.checked = viewModel.onboardingStartupEnabled
            onboardingUpdateCheck.checked = viewModel.onboardingUpdateCheckEnabled
        }
    }

    function microphoneIndex() {
        for (let index = 0; index < viewModel.microphoneOptions.length; ++index) {
            if (viewModel.microphoneOptions[index].key === viewModel.selectedMicrophoneKey)
                return index
        }
        return -1
    }

    Rectangle {
        anchors.fill: parent
        color: "#F4EEE3"
    }

    MouseArea {
        anchors.fill: parent
        preventStealing: true
        onClicked: function(mouse) { mouse.accepted = true }
    }

    Rectangle {
        anchors.fill: parent
        anchors.margins: 18
        radius: 16
        color: "#FFF9EE"
        border.width: 1
        border.color: "#CDBEAA"

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 20
            spacing: 12

            RowLayout {
                Layout.fillWidth: true
                Text {
                    Layout.fillWidth: true
                    text: "聲跡日記首次設定"
                    color: "#493F35"
                    font.family: hostWindow.diaryFontFamily
                    font.pixelSize: hostWindow.fontPx(23)
                    font.weight: Font.DemiBold
                }
                Text {
                    text: (viewModel.onboardingStep + 1) + " / 5"
                    color: "#86796B"
                    font.family: hostWindow.systemFontFamily
                    font.pixelSize: hostWindow.fontPx(14)
                }
            }

            ProgressBar {
                Layout.fillWidth: true
                from: 0
                to: 5
                value: viewModel.onboardingStep + 1
            }

            StackLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                currentIndex: viewModel.onboardingStep

                Item {
                    ColumnLayout {
                        anchors.fill: parent
                        spacing: 12
                        Text {
                            Layout.fillWidth: true
                            text: "先確認隱私與錄音方式"
                            color: "#493F35"
                            font.family: hostWindow.systemFontFamily
                            font.pixelSize: hostWindow.fontPx(20)
                            font.weight: Font.DemiBold
                        }
                        Text {
                            Layout.fillWidth: true
                            text: "語音、轉錄結果、資料庫與日記都保存在你的電腦。首次設定會從 Hugging Face 下載語音模型，但不會上傳錄音。"
                            wrapMode: Text.Wrap
                            color: "#6F6255"
                            font.family: hostWindow.systemFontFamily
                            font.pixelSize: hostWindow.fontPx(15)
                        }
                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: privacyNotice.implicitHeight + 24
                            radius: 9
                            color: "#F1E9DB"
                            Text {
                                id: privacyNotice
                                anchors.fill: parent
                                anchors.margins: 12
                                text: "程式現在不會啟用麥克風。只有最後按下「開始錄音」後，才會儲存以下選擇並啟動錄音 worker。"
                                wrapMode: Text.Wrap
                                color: "#5F7864"
                                font.family: hostWindow.systemFontFamily
                                font.pixelSize: hostWindow.fontPx(15)
                            }
                        }
                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: modelStatusColumn.implicitHeight + 20
                            radius: 9
                            color: viewModel.onboardingModelsReady ? "#E6F0E5" : "#FFF0D8"
                            border.width: 1
                            border.color: viewModel.onboardingModelsReady ? "#A8C4A8" : "#D6B77E"
                            ColumnLayout {
                                id: modelStatusColumn
                                anchors.fill: parent
                                anchors.margins: 10
                                spacing: 6
                                Text {
                                    objectName: "onboardingModelStatus"
                                    Layout.fillWidth: true
                                    text: viewModel.onboardingModelStatusText
                                    wrapMode: Text.Wrap
                                    color: viewModel.onboardingModelsReady ? "#4E6754" : "#815F2C"
                                    font.family: hostWindow.systemFontFamily
                                    font.pixelSize: hostWindow.fontPx(13)
                                }
                                ProgressBar {
                                    objectName: "onboardingModelProgress"
                                    Layout.fillWidth: true
                                    visible: viewModel.onboardingModelBusy
                                    indeterminate: viewModel.onboardingModelProgress <= 0
                                    from: 0
                                    to: 1
                                    value: viewModel.onboardingModelProgress
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    visible: !viewModel.onboardingModelsReady
                                    Text {
                                        Layout.fillWidth: true
                                        visible: viewModel.onboardingModelProgressText !== ""
                                        text: viewModel.onboardingModelProgressText
                                        color: "#86796B"
                                        font.family: hostWindow.systemFontFamily
                                        font.pixelSize: hostWindow.fontPx(12)
                                    }
                                    Button {
                                        objectName: "onboardingModelRepairButton"
                                        enabled: !viewModel.onboardingModelBusy
                                        text: viewModel.onboardingModelState === "error"
                                              ? "重試下載" : "下載語音模型"
                                        onClicked: viewModel.repairOnboardingModels()
                                    }
                                }
                            }
                        }
                        Item { Layout.fillHeight: true }
                    }
                }

                Item {
                    ColumnLayout {
                        anchors.fill: parent
                        spacing: 10
                        Text {
                            text: "選擇日記資料夾"
                            color: "#493F35"
                            font.family: hostWindow.systemFontFamily
                            font.pixelSize: hostWindow.fontPx(20)
                            font.weight: Font.DemiBold
                        }
                        Text {
                            Layout.fillWidth: true
                            text: "我們會建立一個短暫測試檔並立即刪除，以確認此位置可寫入。"
                            wrapMode: Text.Wrap
                            color: "#6F6255"
                            font.family: hostWindow.systemFontFamily
                            font.pixelSize: hostWindow.fontPx(14)
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            TextField {
                                id: onboardingRecordsField
                                objectName: "onboardingRecordsField"
                                Layout.fillWidth: true
                                text: viewModel.onboardingRecordsRoot
                                selectByMouse: true
                            }
                            Button {
                                objectName: "onboardingBrowseButton"
                                text: "瀏覽…"
                                onClicked: onboardingRecordsField.text =
                                    viewModel.chooseRecordsFolder(onboardingRecordsField.text)
                            }
                        }
                        Text {
                            Layout.fillWidth: true
                            visible: viewModel.onboardingRecordsTested
                            text: "資料夾已通過寫入測試"
                            color: "#5F7864"
                            font.family: hostWindow.systemFontFamily
                            font.pixelSize: hostWindow.fontPx(14)
                        }
                        Item { Layout.fillHeight: true }
                    }
                }

                Item {
                    ColumnLayout {
                        anchors.fill: parent
                        spacing: 10
                        Text {
                            text: "啟動與更新偏好"
                            color: "#493F35"
                            font.family: hostWindow.systemFontFamily
                            font.pixelSize: hostWindow.fontPx(20)
                            font.weight: Font.DemiBold
                        }
                        CheckBox {
                            id: onboardingStartupCheck
                            objectName: "onboardingStartupCheck"
                            Layout.fillWidth: true
                            checked: viewModel.onboardingStartupEnabled
                            text: "登入 Windows 後自動啟動（預設關閉）"
                        }
                        Text {
                            Layout.fillWidth: true
                            text: "關閉時只會在你手動開啟聲跡日記後錄音。"
                            wrapMode: Text.Wrap
                            color: "#86796B"
                            font.family: hostWindow.systemFontFamily
                            font.pixelSize: hostWindow.fontPx(14)
                        }
                        CheckBox {
                            id: onboardingUpdateCheck
                            objectName: "onboardingUpdateCheck"
                            Layout.fillWidth: true
                            checked: viewModel.onboardingUpdateCheckEnabled
                            text: "允許檢查 GitHub 新版本（預設關閉）"
                        }
                        Text {
                            Layout.fillWidth: true
                            text: "啟用後最多每 24 小時檢查一次，只顯示提示；不會背景下載或自動安裝。"
                            wrapMode: Text.Wrap
                            color: "#86796B"
                            font.family: hostWindow.systemFontFamily
                            font.pixelSize: hostWindow.fontPx(14)
                        }
                        Item { Layout.fillHeight: true }
                    }
                }

                Item {
                    ColumnLayout {
                        anchors.fill: parent
                        spacing: 8
                        Text {
                            text: "選擇麥克風"
                            color: "#493F35"
                            font.family: hostWindow.systemFontFamily
                            font.pixelSize: hostWindow.fontPx(20)
                            font.weight: Font.DemiBold
                        }
                        ComboBox {
                            id: onboardingMicrophonePicker
                            objectName: "onboardingMicrophonePicker"
                            Layout.fillWidth: true
                            model: viewModel.microphoneOptions
                            textRole: "label"
                            valueRole: "key"
                            currentIndex: root.microphoneIndex()
                            displayText: currentIndex >= 0 ? currentText : "請選擇，不會預先替你決定"
                            delegate: ItemDelegate {
                                required property var modelData
                                width: onboardingMicrophonePicker.width
                                enabled: Boolean(modelData.selectable)
                                text: modelData.label
                            }
                            onActivated: viewModel.selectMicrophone(currentValue)
                        }
                        Text {
                            Layout.fillWidth: true
                            text: "此步驟只選擇裝置；按下「開始錄音」前不會保存或開啟麥克風。"
                            wrapMode: Text.Wrap
                            color: "#5F7864"
                            font.family: hostWindow.systemFontFamily
                            font.pixelSize: hostWindow.fontPx(14)
                        }
                        Button {
                            objectName: "onboardingMicrophoneRescanButton"
                            Layout.fillWidth: true
                            text: "重新掃描"
                            onClicked: viewModel.rescanMicrophones()
                        }
                        Text {
                            Layout.fillWidth: true
                            visible: viewModel.microphoneScanError !== ""
                            text: viewModel.microphoneScanError
                            wrapMode: Text.Wrap
                            color: "#A34739"
                            font.family: hostWindow.systemFontFamily
                            font.pixelSize: hostWindow.fontPx(13)
                        }
                        Item { Layout.fillHeight: true }
                    }
                }

                Item {
                    ColumnLayout {
                        anchors.fill: parent
                        spacing: 10
                        Text {
                            text: "準備開始"
                            color: "#493F35"
                            font.family: hostWindow.systemFontFamily
                            font.pixelSize: hostWindow.fontPx(20)
                            font.weight: Font.DemiBold
                        }
                        Text {
                            Layout.fillWidth: true
                            text: "日記資料夾\n" + viewModel.onboardingRecordsRoot +
                                  "\n\n登入自啟：" + (viewModel.onboardingStartupEnabled ? "開啟" : "關閉") +
                                  "\n版本提示：" + (viewModel.onboardingUpdateCheckEnabled ? "開啟" : "關閉") +
                                  "\n麥克風：" + viewModel.selectedMicrophoneLabel +
                                  "\n語音模型：" + (viewModel.onboardingModelsReady ? "已就緒" : "尚未就緒")
                            wrapMode: Text.WrapAnywhere
                            color: "#6F6255"
                            font.family: hostWindow.systemFontFamily
                            font.pixelSize: hostWindow.fontPx(14)
                        }
                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 52
                            radius: 9
                            color: "#EEF3EC"
                            Text {
                                anchors.fill: parent
                                anchors.margins: 10
                                text: "按下「開始錄音」才會儲存同意與設定，並開啟麥克風。"
                                wrapMode: Text.Wrap
                                color: "#4E6754"
                                font.family: hostWindow.systemFontFamily
                                font.pixelSize: hostWindow.fontPx(14)
                            }
                        }
                        Button {
                            objectName: "onboardingSummaryModelRepairButton"
                            Layout.fillWidth: true
                            visible: !viewModel.onboardingModelsReady
                            enabled: !viewModel.onboardingModelBusy
                            text: viewModel.onboardingModelState === "error"
                                  ? "重新嘗試下載" : "下載語音模型"
                            onClicked: viewModel.repairOnboardingModels()
                        }
                        Item { Layout.fillHeight: true }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Button {
                    objectName: "onboardingBackButton"
                    visible: viewModel.onboardingStep > 0
                    text: "上一步"
                    onClicked: viewModel.retreatOnboarding()
                }
                Item { Layout.fillWidth: true }
                Button {
                    objectName: "onboardingDeferButton"
                    enabled: true
                    text: "稍後設定"
                    onClicked: viewModel.deferOnboarding()
                }
                Button {
                    id: onboardingNextButton
                    objectName: "onboardingNextButton"
                    visible: viewModel.onboardingStep < 4
                    enabled: viewModel.onboardingStep !== 3 ||
                             viewModel.onboardingMicrophoneReady
                    text: viewModel.onboardingStep === 3 ? "查看摘要" : "下一步"
                    onClicked: viewModel.advanceOnboarding(
                        onboardingRecordsField.text,
                        onboardingStartupCheck.checked,
                        onboardingUpdateCheck.checked
                    )
                }
                Button {
                    objectName: "onboardingStartButton"
                    visible: viewModel.onboardingStep === 4
                    enabled: viewModel.onboardingRecordsTested &&
                             viewModel.onboardingMicrophoneReady &&
                             viewModel.onboardingModelsReady &&
                             !viewModel.onboardingModelBusy
                    text: "開始錄音"
                    onClicked: viewModel.startOnboardingRecording()
                }
            }
        }
    }
}
