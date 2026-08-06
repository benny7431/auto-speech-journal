pragma ComponentBehavior: Bound

import QtQuick

Item {
    id: root
    objectName: "ambientSoundRiver"

    property string sceneKey: "listening"
    property bool speechActive: false
    property real audioLevel: 0
    property bool motionEnabled: visible
    property bool reducedMotion: false

    readonly property real boundedAudioLevel: Math.max(0, Math.min(1, audioLevel))
    readonly property real statePace: {
        if (sceneKey === "error")
            return 1.35
        if (sceneKey === "degraded")
            return 0.72
        if (sceneKey === "paused")
            return 0.32
        if (sceneKey === "stopped")
            return 0.18
        if (sceneKey === "starting" || sceneKey === "finalizing")
            return 0.62
        return 1.0
    }
    readonly property color stateColor: {
        if (sceneKey === "error")
            return "#C98278"
        if (sceneKey === "degraded")
            return "#C6A16D"
        if (sceneKey === "paused" || sceneKey === "stopped")
            return "#AAA69D"
        if (sceneKey === "finalizing")
            return "#B6A67F"
        return "#7FA79A"
    }
    readonly property url fallbackTexture:
        Qt.resolvedUrl("../assets/particles/mist-mote.png")

    // Qt Quick Particles is not supported by the software scene graph. Keep its
    // import behind this Loader so the software renderer never creates it.
    readonly property bool particleBackendAllowed:
        GraphicsInfo.api === GraphicsInfo.OpenGL ||
        GraphicsInfo.api === GraphicsInfo.Direct3D11 ||
        GraphicsInfo.api === GraphicsInfo.Direct3D12 ||
        GraphicsInfo.api === GraphicsInfo.Vulkan ||
        GraphicsInfo.api === GraphicsInfo.Metal
    readonly property bool effectRunning: visible && motionEnabled && !reducedMotion
    readonly property bool fallbackAnimationRunning:
        effectRunning && !particleBackendAllowed
    readonly property bool particleLayerLoaded: particleLoader.item !== null
    readonly property int fallbackMoteCount: 10
    readonly property int particleBudget: 104

    clip: true

    Loader {
        id: particleLoader
        objectName: "todayParticleLoader"
        anchors.fill: parent
        active: root.effectRunning && root.particleBackendAllowed
        asynchronous: false
        source: "TodayParticleLayer.qml"
    }

    Binding {
        target: particleLoader.item
        property: "sceneKey"
        value: root.sceneKey
        when: particleLoader.item !== null
    }
    Binding {
        target: particleLoader.item
        property: "speechActive"
        value: root.speechActive
        when: particleLoader.item !== null
    }
    Binding {
        target: particleLoader.item
        property: "audioLevel"
        value: root.boundedAudioLevel
        when: particleLoader.item !== null
    }
    Binding {
        target: particleLoader.item
        property: "effectEnabled"
        value: root.effectRunning
        when: particleLoader.item !== null
    }

    Item {
        id: softwareFallback
        objectName: "softwareRiverFallback"
        anchors.fill: parent
        visible: root.reducedMotion || !root.particleBackendAllowed

        Repeater {
            model: root.fallbackMoteCount

            delegate: Image {
                id: mote

                required property int index

                property real restX: root.width * (0.10 + ((index * 29) % 27) / 100)
                property real restY: root.height * (0.08 + ((index * 37) % 84) / 100)
                property real moteSize: 2 + index % 4

                objectName: "softwareRiverMote"
                x: restX
                y: restY
                width: moteSize
                height: moteSize
                source: root.fallbackTexture
                sourceSize.width: 32
                sourceSize.height: 32
                smooth: true
                mipmap: true
                opacity: root.reducedMotion ? 0.13 : 0.08 + (index % 3) * 0.035

                SequentialAnimation on y {
                    running: root.fallbackAnimationRunning
                    loops: Animation.Infinite

                    NumberAnimation {
                        from: mote.restY
                        to: mote.restY - root.height * (0.04 + (mote.index % 4) * 0.012)
                        duration: Math.round((6200 + mote.index * 190) / root.statePace)
                        easing.type: Easing.InOutSine
                    }
                    NumberAnimation {
                        to: mote.restY
                        duration: Math.round((6200 + mote.index * 190) / root.statePace)
                        easing.type: Easing.InOutSine
                    }
                }

                SequentialAnimation on opacity {
                    running: root.fallbackAnimationRunning
                    loops: Animation.Infinite

                    NumberAnimation {
                        from: 0.06
                        to: root.speechActive ? 0.25 + root.boundedAudioLevel * 0.12 : 0.16
                        duration: Math.round((2300 + mote.index * 110) / root.statePace)
                        easing.type: Easing.InOutSine
                    }
                    NumberAnimation {
                        to: 0.06
                        duration: Math.round((2300 + mote.index * 110) / root.statePace)
                        easing.type: Easing.InOutSine
                    }
                }
            }
        }
    }
}
