import QtQuick

import "."

/*
 * A small bar of the current input level.
 *
 * This replaces the ink-bleed and particle layers as the visual answer to "is it
 * hearing me". Those were atmospheric but ambiguous; a level bar says the same
 * thing in less space and reads at compact size. It is driven directly by the
 * view model, so it stays honest when the microphone goes quiet.
 */
Item {
    id: root

    property real level: 0
    property bool active: false
    property color accentColor: Theme.accent
    property bool motionEnabled: true

    readonly property real clamped: Math.max(0, Math.min(1, level))

    implicitWidth: 64
    implicitHeight: 12

    Rectangle {
        anchors.fill: parent
        radius: height / 2
        color: Theme.wash(Theme.inkFaint, 0.22)
    }

    Rectangle {
        id: fill
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        width: Math.max(height, parent.width * root.clamped)
        height: parent.height
        radius: height / 2
        color: root.accentColor
        opacity: root.active ? 0.95 : 0.5

        Behavior on width {
            enabled: root.motionEnabled
            NumberAnimation { duration: 90; easing.type: Easing.OutQuad }
        }
        Behavior on opacity {
            enabled: root.motionEnabled
            NumberAnimation { duration: 200 }
        }
    }
}
