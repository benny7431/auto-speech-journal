import QtQuick
import QtQuick.Controls

import "."

/*
 * The square glyph button: window close and sheet dismiss.
 * Reads `journal` from the engine root context; see PaperButton.qml.
 *
 * Its font size is deliberately fixed rather than scaled, so the close
 * affordance keeps the same optical weight at every journal font size.
 */
Button {
    id: control

    implicitWidth: 30
    implicitHeight: 28
    font.family: journal.systemFontFamily
    font.pixelSize: 16

    contentItem: Text {
        text: control.text
        color: Theme.inkBody
        font: control.font
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }
    background: Rectangle {
        radius: Theme.radiusSm
        color: control.down ? Theme.wash(Theme.ink, 0.12)
               : control.hovered ? Theme.wash(Theme.ink, 0.06) : "transparent"
    }
}
