import QtQuick
import QtQuick.Controls

/*
 * The solid paper-stock button: compact actions, drawer forms, wizard primaries.
 *
 * Leaf controls read `journal` straight off the engine root context rather than
 * taking it as a required property. At 26 call sites the threading would be pure
 * noise, and it lets every call site stay identical to the inline component this
 * replaced. The cost is that these controls cannot be instantiated in a bare
 * QQmlEngine with no context properties.
 *
 * `contentWidthGuard` is a test contract, not decoration. tests/test_ui.py
 * collects every guarded button and asserts its label still fits at the largest
 * font size across all installed handwriting families. Removing the flag would
 * silently drop this control from that sweep.
 */
Button {
    id: control

    readonly property bool contentWidthGuard: true

    implicitHeight: 38
    implicitWidth: Math.max(90, label.implicitWidth + leftPadding + rightPadding)
    leftPadding: journal.uiFontScale > 1.4 ? 8 : 14
    rightPadding: leftPadding
    font.family: journal.systemFontFamily
    font.pixelSize: Math.max(1, Math.round(16 * journal.uiFontScale))

    contentItem: Text {
        id: label
        text: control.text
        color: control.enabled ? "#493F35" : "#A69B8E"
        font: control.font
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }
    background: Rectangle {
        radius: 9
        color: control.down ? "#DDD2C0" : control.hovered ? "#F5EEE1" : "#ECE3D4"
        border.width: 1
        border.color: "#D0C3AF"
    }
}
