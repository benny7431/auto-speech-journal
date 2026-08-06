import QtQuick

Item {
    id: root

    property real audioLevel: 0
    property bool vadActive: false
    property bool motionEnabled: visible
    property bool reducedMotion: false
    property color washColor: "#789682"
    property real minimumSpread: 4
    property real maximumSpread: 10
    property int dryDuration: 1600
    property real centerRatio: 0.52
    property real maximumOpacity: 0.08

    readonly property bool effectAllowed: visible && motionEnabled && !reducedMotion
    readonly property real boundedLevel: Math.max(0, Math.min(1, audioLevel))
    readonly property real spread: minimumSpread +
                                           (maximumSpread - minimumSpread) * smoothedLevel
    property real smoothedLevel: effectAllowed && vadActive ? boundedLevel : 0
    property real wetness: effectAllowed && vadActive ? 1 : 0

    function rgba(colorValue, alpha) {
        return "rgba(" + Math.round(colorValue.r * 255) + "," +
               Math.round(colorValue.g * 255) + "," +
               Math.round(colorValue.b * 255) + "," + alpha + ")"
    }

    Behavior on smoothedLevel {
        enabled: root.effectAllowed
        NumberAnimation {
            duration: root.vadActive ? 140 : root.dryDuration
            easing.type: root.vadActive ? Easing.OutCubic : Easing.InOutSine
        }
    }
    Behavior on wetness {
        enabled: root.effectAllowed
        NumberAnimation {
            duration: root.vadActive ? 180 : root.dryDuration
            easing.type: root.vadActive ? Easing.OutCubic : Easing.InOutSine
        }
    }

    Canvas {
        id: pigment
        anchors.fill: parent
        opacity: root.wetness
        visible: opacity > 0.001

        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            const center = height * root.centerRatio
            const spread = root.spread
            const bloomCount = Math.max(7, Math.ceil(width / 42))
            const bloomStep = width / bloomCount
            const strength = root.maximumOpacity * (0.55 + root.smoothedLevel * 0.45)

            for (let bloom = 0; bloom < bloomCount; ++bloom) {
                const jitterX = ((bloom * 19) % 13 - 6) * 0.7
                const jitterY = ((bloom * 23) % 11 - 5) * spread * 0.055
                const x = (bloom + 0.5) * bloomStep + jitterX
                const y = center + jitterY
                const radiusX = bloomStep * (0.72 + ((bloom * 7) % 5) * 0.055)
                const radiusY = spread * (0.72 + ((bloom * 11) % 7) * 0.045)

                ctx.save()
                ctx.translate(x, y)
                ctx.scale(1, radiusY / radiusX)
                const gradient = ctx.createRadialGradient(0, 0, 0, 0, 0, radiusX)
                gradient.addColorStop(0, root.rgba(root.washColor, strength))
                gradient.addColorStop(0.42, root.rgba(root.washColor, strength * 0.62))
                gradient.addColorStop(1, root.rgba(root.washColor, 0))
                ctx.fillStyle = gradient
                ctx.fillRect(-radiusX, -radiusX, radiusX * 2, radiusX * 2)
                ctx.restore()

                const fiberRadius = Math.max(1.2, spread * (0.16 + (bloom % 3) * 0.035))
                const fiberX = x + (((bloom * 17) % 9) - 4) * 1.1
                const fiberY = y + (((bloom * 13) % 7) - 3) * spread * 0.14
                const fiber = ctx.createRadialGradient(
                    fiberX, fiberY, 0, fiberX, fiberY, fiberRadius
                )
                fiber.addColorStop(0, root.rgba(root.washColor, strength * 0.72))
                fiber.addColorStop(1, root.rgba(root.washColor, 0))
                ctx.fillStyle = fiber
                ctx.fillRect(
                    fiberX - fiberRadius,
                    fiberY - fiberRadius,
                    fiberRadius * 2,
                    fiberRadius * 2
                )
            }
        }
    }

    onSmoothedLevelChanged: pigment.requestPaint()
    onWetnessChanged: pigment.requestPaint()
    onWashColorChanged: pigment.requestPaint()
}
