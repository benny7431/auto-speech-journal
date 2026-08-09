import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: root
    objectName: "vocabularySheet"
    spacing: 13

    required property var journal

    signal clearVocabularyRequested()

    function px(size) {
        return Math.max(1, Math.round(size * journal.uiFontScale))
    }


    RowLayout {
        Layout.fillWidth: true
        Text {
            Layout.fillWidth: true
            text: "自動學習使用者修正"
            color: "#493F35"
            font.family: journal.systemFontFamily
            font.pixelSize: root.px(17)
            font.weight: Font.DemiBold
        }
        Switch {
            id: vocabularyLearningSwitch
            objectName: "vocabularyLearningSwitch"
            checked: journal.vocabularyLearningEnabled
            font.family: journal.systemFontFamily
            font.pixelSize: root.px(15)
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
        font.family: journal.systemFontFamily
        font.pixelSize: root.px(14)
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
        font.family: journal.systemFontFamily
        font.pixelSize: root.px(16)
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
                    font.family: journal.systemFontFamily
                    font.pixelSize: root.px(17)
                }
                Text {
                    objectName: "vocabularyCount"
                    text: vocabularyEntry.modelData.count + " 次"
                    color: "#776B5E"
                    font.family: journal.systemFontFamily
                    font.pixelSize: root.px(14)
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
        onClicked: root.clearVocabularyRequested()
    }
}
