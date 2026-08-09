import QtQuick
import QtQuick.Controls

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
        color: "#5C5044"
        font: control.font
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }
    background: Rectangle {
        radius: 8
        color: control.down ? "#DDD2C0" : control.hovered ? "#EFE5D6" : "transparent"
    }
}
