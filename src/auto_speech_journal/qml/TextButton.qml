import QtQuick
import QtQuick.Controls

import "."

/*
 * The flat, low-emphasis button: drawer tabs and secondary actions.
 * Reads `journal` from the engine root context; see PaperButton.qml.
 */
Button {
    id: control

    readonly property bool contentWidthGuard: true

    implicitHeight: 36
    implicitWidth: Math.max(78, label.implicitWidth + leftPadding + rightPadding)
    leftPadding: journal.uiFontScale > Theme.denseFontScale ? 4 : 10
    rightPadding: leftPadding
    flat: true
    font.family: journal.systemFontFamily
    font.pixelSize: Math.max(1, Math.round(15 * journal.uiFontScale))

    contentItem: Text {
        id: label
        text: control.text
        color: control.enabled ? (control.down ? Theme.accentDeep : Theme.inkBody)
                           : Theme.inkFaint
        font: control.font
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }
    background: Rectangle {
        radius: Theme.radiusSm
        color: control.down ? Theme.wash(Theme.accent, 0.10)
               : control.hovered ? Theme.wash(Theme.accent, 0.05) : "transparent"
    }
}
