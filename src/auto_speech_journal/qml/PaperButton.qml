import QtQuick
import QtQuick.Controls

import "."

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
    leftPadding: journal.uiFontScale > Theme.denseFontScale ? 8 : 14
    rightPadding: leftPadding
    font.family: journal.systemFontFamily
    font.pixelSize: Math.max(1, Math.round(16 * journal.uiFontScale))

    contentItem: Text {
        id: label
        text: control.text
        color: control.enabled ? Theme.inkStrong : Theme.inkFaint
        font: control.font
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }
    background: Rectangle {
        radius: Theme.radiusSm
        color: control.down ? Theme.wash(Theme.ink, 0.12)
               : control.hovered ? Theme.paperRaised : Theme.paperSunken
        border.width: Theme.hairline
        border.color: Theme.line
    }
}
