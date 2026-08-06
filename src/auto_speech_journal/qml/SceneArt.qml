import QtQuick

Item {
    id: root

    property url sceneSource
    property string sceneKey: "listening"
    property bool cropMode: true
    property bool motionEnabled: visible
    property bool reducedMotion: false
    property bool stabilizeStates: true
    property color paperColor: "#F4EEE3"
    property color monthTint: "#9CB4A2"
    property bool featherRight: true
    property bool featherBottom: !cropMode
    property real rightFeatherStart: cropMode ? 0.43 : 0.58
    property real bottomFeatherStart: cropMode ? 0.82 : 0.54

    property bool showingA: true
    property url sourceA
    property url sourceB
    property bool pendingA: false
    property bool pendingB: false

    readonly property bool animationEnabled: visible && motionEnabled && !reducedMotion &&
                                             sceneKey !== "paused"
    readonly property url baseSceneSource: stabilizeStates ? listeningSourceFor(sceneSource) :
                                                               sceneSource
    readonly property real variantStrength: {
        if (!stabilizeStates || sceneKey === "listening")
            return 0
        if (sceneKey === "error")
            return 0.72
        if (sceneKey === "degraded")
            return 0.22
        if (sceneKey === "paused")
            return 0.18
        return 0.13
    }
    readonly property color stateWashColor: {
        if (sceneKey === "capturing")
            return "#789682"
        if (sceneKey === "finalizing")
            return paperColor
        if (sceneKey === "paused")
            return "#D9D1C4"
        return "transparent"
    }
    readonly property real stateWashOpacity: {
        if (sceneKey === "capturing")
            return 0.06
        if (sceneKey === "finalizing")
            return 0.08
        if (sceneKey === "paused")
            return 0.16
        return 0
    }

    clip: true

    function listeningSourceFor(source) {
        const value = source.toString()
        if (value === "" || sceneKey === "listening")
            return source
        const stateSuffix = "-" + sceneKey + ".webp"
        const suffixIndex = value.lastIndexOf(stateSuffix)
        if (suffixIndex < 0)
            return source
        return value.slice(0, suffixIndex) + "-listening.webp" +
               value.slice(suffixIndex + stateSuffix.length)
    }

    function acceptSource(nextSource) {
        const nextValue = nextSource.toString()
        if (nextValue === "")
            return
        const activeValue = (showingA ? sourceA : sourceB).toString()
        if (activeValue === nextValue)
            return
        if (sourceA.toString() === "" && sourceB.toString() === "") {
            sourceA = nextSource
            showingA = true
            return
        }
        if (showingA) {
            sourceB = nextSource
            pendingB = true
        } else {
            sourceA = nextSource
            pendingA = true
        }
    }

    function rgba(colorValue, alpha) {
        return "rgba(" + Math.round(colorValue.r * 255) + "," +
               Math.round(colorValue.g * 255) + "," +
               Math.round(colorValue.b * 255) + "," + alpha + ")"
    }

    onBaseSceneSourceChanged: acceptSource(baseSceneSource)
    Component.onCompleted: acceptSource(baseSceneSource)

    Rectangle {
        anchors.fill: parent
        color: root.paperColor
    }

    Canvas {
        id: fallbackWash
        anchors.fill: parent
        opacity: 0.72

        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            const washes = [
                [0.14, 0.15, 0.48, root.rgba(root.monthTint, 0.19)],
                [0.70, 0.22, 0.40, root.rgba(root.monthTint, 0.10)],
                [0.34, 0.68, 0.50, "rgba(209,147,111,0.08)"]
            ]
            for (let i = 0; i < washes.length; ++i) {
                const wash = washes[i]
                const radius = Math.max(width, height) * wash[2]
                const x = width * wash[0]
                const y = height * wash[1]
                const gradient = ctx.createRadialGradient(x, y, 0, x, y, radius)
                gradient.addColorStop(0, wash[3])
                gradient.addColorStop(1, "rgba(255,255,255,0)")
                ctx.fillStyle = gradient
                ctx.fillRect(0, 0, width, height)
            }
        }
    }

    Image {
        id: imageA
        x: -8
        y: -8
        width: parent.width + 16
        height: parent.height + 16
        source: root.sourceA
        fillMode: Image.PreserveAspectCrop
        asynchronous: true
        cache: true
        smooth: true
        mipmap: true
        opacity: root.showingA && status === Image.Ready ? 1 : 0
        onStatusChanged: {
            if (status === Image.Ready && root.pendingA) {
                root.pendingA = false
                root.showingA = true
            }
        }

        Behavior on opacity {
            enabled: root.visible && root.motionEnabled
            NumberAnimation {
                duration: root.reducedMotion ? 180 : 700
                easing.type: Easing.InOutCubic
            }
        }
        SequentialAnimation on scale {
            running: root.animationEnabled
            loops: Animation.Infinite
            NumberAnimation {
                from: 1.0
                to: 1.01
                duration: 15000
                easing.type: Easing.InOutSine
            }
            NumberAnimation {
                from: 1.01
                to: 1.0
                duration: 15000
                easing.type: Easing.InOutSine
            }
        }
    }

    Image {
        id: imageB
        x: -8
        y: -8
        width: parent.width + 16
        height: parent.height + 16
        source: root.sourceB
        fillMode: Image.PreserveAspectCrop
        asynchronous: true
        cache: true
        smooth: true
        mipmap: true
        opacity: !root.showingA && status === Image.Ready ? 1 : 0
        onStatusChanged: {
            if (status === Image.Ready && root.pendingB) {
                root.pendingB = false
                root.showingA = false
            }
        }

        Behavior on opacity {
            enabled: root.visible && root.motionEnabled
            NumberAnimation {
                duration: root.reducedMotion ? 180 : 700
                easing.type: Easing.InOutCubic
            }
        }
        SequentialAnimation on scale {
            running: root.animationEnabled
            loops: Animation.Infinite
            NumberAnimation {
                from: 1.002
                to: 1.012
                duration: 16000
                easing.type: Easing.InOutSine
            }
            NumberAnimation {
                from: 1.012
                to: 1.002
                duration: 16000
                easing.type: Easing.InOutSine
            }
        }
    }

    // State images are translucent ink changes over a stable monthly listening scene.
    Image {
        id: stateInk
        x: -8
        y: -8
        width: parent.width + 16
        height: parent.height + 16
        source: root.sceneSource
        fillMode: Image.PreserveAspectCrop
        asynchronous: true
        cache: true
        smooth: true
        mipmap: true
        opacity: status === Image.Ready ? root.variantStrength : 0

        Behavior on opacity {
            enabled: root.visible && root.motionEnabled
            NumberAnimation {
                duration: root.reducedMotion ? 180 : 700
                easing.type: Easing.InOutCubic
            }
        }
    }

    Rectangle {
        anchors.fill: parent
        color: root.stateWashColor
        opacity: root.stateWashOpacity

        Behavior on opacity {
            enabled: root.visible && root.motionEnabled
            NumberAnimation {
                duration: root.reducedMotion ? 180 : 700
                easing.type: Easing.InOutCubic
            }
        }
    }

    Repeater {
        model: 7
        delegate: Rectangle {
            required property int index
            property real baseX: ((index * 47 + 11) % 101) / 100
            property real baseY: ((index * 37 + 7) % 53) / 100

            x: root.width * baseX
            y: root.height * baseY
            width: 1.5 + (index % 3)
            height: width
            radius: width / 2
            color: index % 2 ? "#38FFF7D7" : "#30FFFFFF"
            visible: root.animationEnabled

            SequentialAnimation on y {
                running: root.animationEnabled
                loops: Animation.Infinite
                NumberAnimation {
                    from: root.height * baseY
                    to: root.height * baseY - 6 - (index % 3) * 3
                    duration: 6800 + index * 310
                    easing.type: Easing.InOutSine
                }
                NumberAnimation {
                    to: root.height * baseY
                    duration: 6800 + index * 310
                    easing.type: Easing.InOutSine
                }
            }
        }
    }

    Canvas {
        id: paperMist
        anchors.fill: parent

        function boundaryOffset(position, layer) {
            return Math.sin(position * 0.031 + layer * 1.71) * 7 +
                   Math.sin(position * 0.011 + layer * 0.83) * 5 +
                   Math.sin(position * 0.071 + layer * 2.17) * 2
        }

        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            const layers = 18
            const step = 6

            if (root.featherRight) {
                const start = width * Math.max(0, Math.min(0.94, root.rightFeatherStart))
                const span = Math.max(1, width - start)
                for (let layer = 0; layer < layers; ++layer) {
                    const depth = layer / (layers - 1)
                    const base = start + span * depth
                    ctx.beginPath()
                    ctx.moveTo(width + 2, -2)
                    ctx.lineTo(base + boundaryOffset(0, layer), -2)
                    for (let y = 0; y <= height + step; y += step)
                        ctx.lineTo(base + boundaryOffset(y, layer), y)
                    ctx.lineTo(width + 2, height + 2)
                    ctx.closePath()
                    ctx.fillStyle = root.rgba(root.paperColor, 0.18)
                    ctx.fill()
                }
                ctx.fillStyle = root.rgba(root.paperColor, 1)
                ctx.fillRect(width - 2, 0, 2, height)
            }

            if (root.featherBottom) {
                const start = height * Math.max(0, Math.min(0.94, root.bottomFeatherStart))
                const span = Math.max(1, height - start)
                for (let layer = 0; layer < layers; ++layer) {
                    const depth = layer / (layers - 1)
                    const base = start + span * depth
                    ctx.beginPath()
                    ctx.moveTo(-2, height + 2)
                    ctx.lineTo(-2, base + boundaryOffset(0, layer))
                    for (let x = 0; x <= width + step; x += step)
                        ctx.lineTo(x, base + boundaryOffset(x, layer))
                    ctx.lineTo(width + 2, height + 2)
                    ctx.closePath()
                    ctx.fillStyle = root.rgba(root.paperColor, 0.18)
                    ctx.fill()
                }
                ctx.fillStyle = root.rgba(root.paperColor, 1)
                ctx.fillRect(0, height - 2, width, 2)
            }

            ctx.strokeStyle = root.rgba(root.paperColor, 0.18)
            ctx.lineWidth = 0.7
            for (let fiber = 0; fiber < 16; ++fiber) {
                const y = height * ((fiber * 37 + 9) % 97) / 97
                ctx.beginPath()
                ctx.moveTo(0, y)
                for (let x = 0; x <= width + 10; x += 12)
                    ctx.lineTo(x, y + Math.sin(x * 0.041 + fiber) * 1.4)
                ctx.stroke()
            }
        }
    }

    Connections {
        target: root
        function onPaperColorChanged() { paperMist.requestPaint() }
        function onFeatherRightChanged() { paperMist.requestPaint() }
        function onFeatherBottomChanged() { paperMist.requestPaint() }
        function onRightFeatherStartChanged() { paperMist.requestPaint() }
        function onBottomFeatherStartChanged() { paperMist.requestPaint() }
        function onMonthTintChanged() { fallbackWash.requestPaint() }
    }

}
