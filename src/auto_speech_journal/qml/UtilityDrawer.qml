import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

/*
 * The right-hand utility drawer: settings, status, vocabulary and hours.
 *
 * All four sheets are instantiated at once and switched with `visible`, never
 * behind a Loader. tests/test_ui.py sets activeSheet and reads a sheet back with
 * no wait, and separately holds a Python reference to a field across a
 * close/reopen cycle - both break if the sheets are destroyed and rebuilt.
 *
 * The drawer owns no application state. It reports intent upward through signals
 * and reads the active tab back down through `activeSheet`, so the window stays
 * the single owner of which sheet is open.
 */
Rectangle {
    id: root
    objectName: "sheetShade"
    anchors.fill: parent
    color: "#624B4036"
    z: 30

    required property var journal
    property string activeSheet: ""

    signal sheetRequested(string key)
    signal closeRequested()
    signal exitRequested()
    signal clearVocabularyRequested()
    signal deleteHourRequested(string hourKey)

    function px(size) {
        return Math.max(1, Math.round(size * journal.uiFontScale))
    }

    /*
     * The key is passed in rather than read off `activeSheet`. The window assigns
     * its own activeSheet and calls this from the same change handler, and the
     * binding that carries the value down here has not necessarily evaluated yet.
     */
    function reloadSheet(key) {
        if (key === "settings")
            settingsSheet.reload()
        else if (key === "hours")
            hoursSheet.reload()
        else if (key === "vocabulary")
            journal.refreshVocabulary()
    }

    function refreshHourOptions() {
        hoursSheet.reload()
    }

    MouseArea {
        anchors.fill: parent
        onClicked: root.closeRequested()
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
            font.family: journal.systemFontFamily
            font.pixelSize: root.px(28)
            font.weight: Font.DemiBold
        }

        IconButton {
            anchors.right: parent.right
            anchors.rightMargin: 16
            anchors.verticalCenter: sheetTitle.verticalCenter
            text: "×"
            onClicked: root.closeRequested()
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
                font.weight: root.activeSheet === "settings" ? Font.DemiBold : Font.Normal
                onClicked: root.sheetRequested("settings")
                background: Rectangle {
                    radius: 8
                    color: root.activeSheet === "settings" ? "#1B718A78" : "transparent"
                }
            }
            TextButton {
                objectName: "systemDrawerTab"
                Layout.fillWidth: true
                Layout.fillHeight: true
                text: "狀態"
                font.weight: root.activeSheet === "system" ? Font.DemiBold : Font.Normal
                onClicked: root.sheetRequested("system")
                background: Rectangle {
                    radius: 8
                    color: root.activeSheet === "system" ? "#1B718A78" : "transparent"
                }
            }
            TextButton {
                objectName: "vocabularyDrawerTab"
                Layout.fillWidth: true
                Layout.fillHeight: true
                text: "字典"
                font.weight: root.activeSheet === "vocabulary" ? Font.DemiBold : Font.Normal
                onClicked: root.sheetRequested("vocabulary")
                background: Rectangle {
                    radius: 8
                    color: root.activeSheet === "vocabulary" ? "#1B718A78" : "transparent"
                }
            }
            TextButton {
                objectName: "hoursDrawerTab"
                Layout.fillWidth: true
                Layout.fillHeight: true
                text: "時段"
                font.weight: root.activeSheet === "hours" ? Font.DemiBold : Font.Normal
                onClicked: root.sheetRequested("hours")
                background: Rectangle {
                    radius: 8
                    color: root.activeSheet === "hours" ? "#1B718A78" : "transparent"
                }
            }
        }

        VocabularySheet {
            id: vocabularySheet
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: drawerTabs.bottom
            anchors.bottom: parent.bottom
            anchors.margins: 24
            visible: root.activeSheet === "vocabulary"
            journal: root.journal
            onClearVocabularyRequested: root.clearVocabularyRequested()
        }

        SettingsSheet {
            id: settingsSheet
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: drawerTabs.bottom
            anchors.bottom: parent.bottom
            anchors.margins: 24
            visible: root.activeSheet === "settings"
            journal: root.journal
            onCloseRequested: root.closeRequested()
        }

        SystemSheet {
            id: systemSheet
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: drawerTabs.bottom
            anchors.margins: 24
            visible: root.activeSheet === "system"
            journal: root.journal
            onExitRequested: root.exitRequested()
        }

        HoursSheet {
            id: hoursSheet
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: drawerTabs.bottom
            anchors.margins: 24
            visible: root.activeSheet === "hours"
            journal: root.journal
            onDeleteHourRequested: function(hourKey) { root.deleteHourRequested(hourKey) }
        }
    }
}
