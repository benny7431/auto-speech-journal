import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "."

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
    color: Theme.wash(Theme.ink, 0.38)
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
        color: Theme.paperSheet
        border.width: Theme.hairline
        border.color: Theme.line

        MouseArea { anchors.fill: parent }

        Text {
            id: sheetTitle
            objectName: "sheetTitle"
            anchors.left: parent.left
            anchors.leftMargin: Theme.spaceXl
            anchors.top: parent.top
            anchors.topMargin: Theme.spaceXl
            text: "今日工具"
            color: Theme.inkStrong
            font.family: journal.systemFontFamily
            font.pixelSize: root.px(22)
            font.weight: Font.DemiBold
        }

        IconButton {
            anchors.right: parent.right
            anchors.rightMargin: Theme.spaceLg
            anchors.verticalCenter: sheetTitle.verticalCenter
            text: "×"
            onClicked: root.closeRequested()
        }

        RowLayout {
            id: drawerTabs
            objectName: "utilityDrawerTabs"
            anchors.left: parent.left
            anchors.leftMargin: Theme.spaceXl
            anchors.right: parent.right
            anchors.rightMargin: Theme.spaceXl
            anchors.top: sheetTitle.bottom
            anchors.topMargin: Theme.spaceLg
            height: 36
            spacing: Theme.spaceXs

            TextButton {
                objectName: "settingsDrawerTab"
                Layout.fillWidth: true
                Layout.fillHeight: true
                text: "設定"
                font.weight: root.activeSheet === "settings" ? Font.DemiBold : Font.Normal
                onClicked: root.sheetRequested("settings")
                background: Rectangle {
                    radius: Theme.radiusSm
                    color: root.activeSheet === "settings"
                           ? Theme.paperRaised : "transparent"
                    border.width: root.activeSheet === "settings" ? Theme.hairline : 0
                    border.color: Theme.line
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
                    radius: Theme.radiusSm
                    color: root.activeSheet === "system"
                           ? Theme.paperRaised : "transparent"
                    border.width: root.activeSheet === "system" ? Theme.hairline : 0
                    border.color: Theme.line
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
                    radius: Theme.radiusSm
                    color: root.activeSheet === "vocabulary"
                           ? Theme.paperRaised : "transparent"
                    border.width: root.activeSheet === "vocabulary" ? Theme.hairline : 0
                    border.color: Theme.line
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
                    radius: Theme.radiusSm
                    color: root.activeSheet === "hours"
                           ? Theme.paperRaised : "transparent"
                    border.width: root.activeSheet === "hours" ? Theme.hairline : 0
                    border.color: Theme.line
                }
            }
        }

        // Separates the fixed header from the scrolling sheet below it.
        Rectangle {
            id: headerRule
            objectName: "drawerHeaderRule"
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: drawerTabs.bottom
            anchors.topMargin: Theme.spaceLg
            height: Theme.hairline
            color: Theme.line
        }

        VocabularySheet {
            id: vocabularySheet
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: headerRule.bottom
            anchors.bottom: parent.bottom
            anchors.margins: Theme.spaceXl
            visible: root.activeSheet === "vocabulary"
            journal: root.journal
            onClearVocabularyRequested: root.clearVocabularyRequested()
        }

        SettingsSheet {
            id: settingsSheet
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: headerRule.bottom
            anchors.bottom: parent.bottom
            anchors.margins: Theme.spaceXl
            visible: root.activeSheet === "settings"
            journal: root.journal
            onCloseRequested: root.closeRequested()
        }

        SystemSheet {
            id: systemSheet
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: headerRule.bottom
            anchors.bottom: parent.bottom
            anchors.margins: Theme.spaceXl
            visible: root.activeSheet === "system"
            journal: root.journal
            onExitRequested: root.exitRequested()
        }

        HoursSheet {
            id: hoursSheet
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: headerRule.bottom
            anchors.bottom: parent.bottom
            anchors.margins: Theme.spaceXl
            visible: root.activeSheet === "hours"
            journal: root.journal
            onDeleteHourRequested: function(hourKey) { root.deleteHourRequested(hourKey) }
        }
    }
}
