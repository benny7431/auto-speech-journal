import QtQuick

import "."

/*
 * A titled group of form controls, drawn as one card.
 *
 * The settings drawer used to be ~35 controls stacked in a single column with
 * nothing but hairlines between them, which made it impossible to tell where one
 * concern ended and the next began. Grouping is the whole point of this file.
 *
 * Children are reparented into the card's inner column, so the `width:
 * parent.width` idiom the existing form controls rely on keeps working.
 */
Column {
    id: root

    property string title: ""
    default property alias content: body.data

    // Taken from the enclosing form rather than left implicit: a Column sizes to
    // its widest child, and the card below sizes to the Column, so an implicit
    // width would resolve the cycle at zero and silently collapse every control.
    width: parent.width
    spacing: Theme.spaceSm

    Text {
        text: root.title
        visible: root.title !== ""
        color: Theme.inkBody
        font.family: journal.systemFontFamily
        font.pixelSize: Math.max(1, Math.round(14 * journal.uiFontScale))
        font.weight: Font.DemiBold
    }

    Rectangle {
        width: parent.width
        height: body.implicitHeight + 2 * Theme.spaceLg
        radius: Theme.radiusMd
        color: Theme.paperRaised
        border.width: Theme.hairline
        border.color: Theme.line

        Column {
            id: body
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: Theme.spaceLg
            spacing: Theme.spaceMd
        }
    }
}
