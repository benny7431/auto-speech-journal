import QtQuick
import QtQuick.Controls

/*
 * The flat, low-emphasis button: drawer tabs and secondary actions.
 * Reads `journal` from the engine root context; see PaperButton.qml.
 */
Button {
    id: control

    readonly property bool contentWidthGuard: true

    implicitHeight: 36
    implicitWidth: Math.max(78, label.implicitWidth + leftPadding + rightPadding)
    leftPadding: journal.uiFontScale > 1.4 ? 4 : 10
    rightPadding: leftPadding
    flat: true
    font.family: journal.systemFontFamily
    font.pixelSize: Math.max(1, Math.round(15 * journal.uiFontScale))

    contentItem: Text {
        id: label
        text: control.text
        color: control.enabled ? (control.down ? "#354D3D" : "#655A4F") : "#A69B8E"
        font: control.font
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }
    background: Rectangle {
        radius: 8
        color: control.down ? "#14789682" :
               control.hovered ? "#0B789682" : "transparent"
    }
}
