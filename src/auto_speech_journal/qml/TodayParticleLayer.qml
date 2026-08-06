import QtQuick
import QtQuick.Particles

Item {
    id: root
    objectName: "todayParticleLayer"

    property string sceneKey: "listening"
    property bool speechActive: false
    property real audioLevel: 0
    property bool effectEnabled: visible

    readonly property real boundedAudioLevel: Math.max(0, Math.min(1, audioLevel))
    readonly property real speechIntensity:
        speechActive ? 0.25 + boundedAudioLevel * 0.75 : 0
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
    readonly property color mistColor: {
        if (sceneKey === "error")
            return "#D69A91"
        if (sceneKey === "degraded")
            return "#D1B07C"
        if (sceneKey === "paused" || sceneKey === "stopped")
            return "#BDB9AF"
        if (sceneKey === "finalizing")
            return "#C7B78E"
        return "#91B8AC"
    }
    readonly property bool running: riverParticles.running
    readonly property int particleBudget: 104

    readonly property url mistTexture:
        Qt.resolvedUrl("../assets/particles/mist-mote.png")
    readonly property url glowTexture:
        Qt.resolvedUrl("../assets/particles/glow-mote.png")
    readonly property url rippleTexture:
        Qt.resolvedUrl("../assets/particles/soft-ripple.png")

    clip: true

    ParticleSystem {
        id: riverParticles
        objectName: "todayParticleSystem"
        anchors.fill: parent
        running: root.visible && root.effectEnabled
    }

    ImageParticle {
        system: riverParticles
        groups: ["mist"]
        source: root.mistTexture
        color: root.mistColor
        colorVariation: 0.08
        alpha: root.sceneKey === "stopped" ? 0.16 : 0.28
        alphaVariation: 0.12
        entryEffect: ImageParticle.Fade
    }

    ImageParticle {
        system: riverParticles
        groups: ["glow", "voiceSeed"]
        source: root.glowTexture
        color: root.sceneKey === "error" ? "#F2C1B8" : "#F6E8CB"
        colorVariation: 0.05
        alpha: 0.34
        alphaVariation: 0.12
        entryEffect: ImageParticle.Scale
    }

    ImageParticle {
        system: riverParticles
        groups: ["ripples"]
        source: root.rippleTexture
        color: root.sceneKey === "error" ? "#D98379" : "#9DC6B9"
        colorVariation: 0.04
        alpha: 0.30 + root.speechIntensity * 0.18
        alphaVariation: 0.08
        entryEffect: ImageParticle.Fade
    }

    Emitter {
        id: mistEmitter
        system: riverParticles
        group: "mist"
        x: root.width * 0.08
        y: root.height + 8
        width: root.width * 0.30
        height: 1
        enabled: root.effectEnabled
        emitRate: root.effectEnabled ? Math.max(1, Math.round(4 * root.statePace)) : 0
        lifeSpan: Math.round(7600 / Math.max(0.35, root.statePace))
        lifeSpanVariation: 900
        maximumEmitted: 44
        size: 9
        sizeVariation: 5
        endSize: 3
        velocity: PointDirection {
            x: 0
            xVariation: root.sceneKey === "error" ? 10 : 4
            y: -(8 + 13 * root.statePace)
            yVariation: 4
        }
    }

    Emitter {
        id: glowEmitter
        system: riverParticles
        group: "glow"
        x: root.width * 0.11
        y: root.height * 0.18
        width: root.width * 0.25
        height: root.height * 0.68
        enabled: root.effectEnabled
        emitRate: root.effectEnabled ? Math.max(1, Math.round(1.5 * root.statePace)) : 0
        lifeSpan: Math.round(5200 / Math.max(0.35, root.statePace))
        lifeSpanVariation: 700
        maximumEmitted: 18
        size: 4
        sizeVariation: 2
        endSize: 1
        velocity: PointDirection {
            x: 0
            xVariation: 3
            y: -(4 + 7 * root.statePace)
            yVariation: 2
        }
    }

    Emitter {
        id: voiceSeedEmitter
        system: riverParticles
        group: "voiceSeed"
        x: root.width * 0.13
        y: root.height * 0.58
        width: root.width * 0.20
        height: root.height * 0.08
        enabled: root.effectEnabled && root.speechActive
        emitRate: enabled ? Math.round(2 + root.speechIntensity * 3) : 0
        lifeSpan: 720
        lifeSpanVariation: 120
        maximumEmitted: 4
        size: 5 + root.speechIntensity * 3
        sizeVariation: 2
        endSize: 2
        velocity: PointDirection {
            x: 0
            xVariation: 5
            y: -(10 + root.speechIntensity * 10)
            yVariation: 3
        }
    }

    TrailEmitter {
        id: speechTrail
        system: riverParticles
        follow: "voiceSeed"
        group: "ripples"
        enabled: root.effectEnabled && root.speechActive
        emitRatePerParticle: enabled ? Math.round(8 + root.speechIntensity * 6) : 0
        lifeSpan: 660
        lifeSpanVariation: 100
        maximumEmitted: 38
        emitWidth: 5 + root.speechIntensity * 3
        emitHeight: 5 + root.speechIntensity * 3
        size: 8 + root.speechIntensity * 7
        sizeVariation: 3
        endSize: 2
        velocity: PointDirection {
            x: 0
            xVariation: 7
            y: -2
            yVariation: 3
        }
    }

    Wander {
        system: riverParticles
        groups: ["mist"]
        enabled: root.effectEnabled
        affectedParameter: Wander.Velocity
        pace: root.sceneKey === "error" ? 34 : 16
        xVariance: root.sceneKey === "error" ? 11 : 5
        yVariance: root.sceneKey === "error" ? 4 : 2
    }
}
