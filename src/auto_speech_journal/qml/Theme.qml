pragma Singleton

import QtQuick

/*
 * The single source of truth for colour, spacing and rhythm in the journal.
 *
 * Pick the role that matches the meaning and let the palette decide the value.
 * The interface is a sheet of warm paper: content sits on raised cards, the page
 * itself never competes with the text, and sage is reserved for one idea only -
 * live audio. It marks the recording control, the active segment and focus
 * rings, and nothing decorative.
 *
 * There are no drop shadows anywhere. Baselines and CI both render through Qt's
 * software backend, where QtQuick.Effects blur is unreliable, so a card lifts off
 * the page with a hairline border and a tonal step instead. On paper stock that
 * reads more honestly than a blur would.
 */
QtObject {
    // -- paper ---------------------------------------------------------------
    readonly property color paper: "#F4EEE3"        // window and page ground
    readonly property color paperRaised: "#FFFDF8"  // cards lifted off the page
    readonly property color paperSunken: "#ECE3D4"  // inputs and wells
    readonly property color paperSheet: "#F8F5EE"   // drawer and overlay surfaces

    // -- ink -----------------------------------------------------------------
    readonly property color ink: "#3A342C"          // primary reading text
    readonly property color inkStrong: "#493F35"    // headings and brand
    readonly property color inkBody: "#695D50"      // secondary text
    readonly property color inkMuted: "#86796B"     // timestamps and metadata
    readonly property color inkFaint: "#A69B8E"     // placeholders and disabled

    // -- structure -----------------------------------------------------------
    readonly property color line: "#D8CBBA"         // card borders and dividers
    readonly property color lineSoft: "#E4DACA"     // the quietest hairline
    readonly property real hairline: 1

    // -- accent: live audio --------------------------------------------------
    readonly property color accent: "#718A78"
    readonly property color accentDeep: "#5F7864"   // pressed and emphasis
    readonly property color accentSoft: "#AAB9A8"   // resting borders
    readonly property color accentSurface: "#E6F0E5"

    // -- status --------------------------------------------------------------
    readonly property color danger: "#9C4F40"
    readonly property color dangerStrong: "#B85C4A"
    readonly property color warning: "#94622E"
    readonly property color warningStrong: "#D6AA6D"

    // -- rhythm --------------------------------------------------------------
    // Every margin, gap and padding in the redesigned surfaces comes from here.
    readonly property real spaceXs: 4
    readonly property real spaceSm: 8
    readonly property real spaceMd: 12
    readonly property real spaceLg: 16
    readonly property real spaceXl: 24
    readonly property real space2Xl: 32

    readonly property real radiusSm: 8
    readonly property real radiusMd: 12
    readonly property real radiusLg: 16

    /*
     * The widest a line of journal text is allowed to get. Without this the
     * cards stretch to the window and a maximised workspace runs ~60 characters
     * per line, which is past the point where the eye reliably finds the next
     * line. The column centres in whatever space is left over.
     */
    readonly property real readingMeasure: 880

    // Mirrors ui.py COMPACT_CORNER_RADIUS / EXPANDED_CORNER_RADIUS. Python stays
    // authoritative because it also builds the DWM and QRegion window masks; if
    // these drift, the painted corner stops matching the clipped one.
    readonly property real radiusCompactWindow: 14
    readonly property real radiusExpandedWindow: 18

    /*
     * Above this journal font scale the layouts switch to a denser arrangement:
     * padding shrinks, the compact partial transcript drops to one line, and
     * stacked labels replace side-by-side ones. Kept here so every site that
     * branches on it moves together.
     */
    readonly property real denseFontScale: 1.4

    /*
     * One tint per month, used at very low opacity behind the page and as the
     * hour marker. With the scene photographs gone this is the whole seasonal
     * signal: twelve values instead of 41 MB of generated art.
     */
    readonly property var monthTints: [
        "#AAB7C7", "#A58AB6", "#9FB88D", "#7894A8",
        "#5E8B70", "#657AA3", "#D6A143", "#3F8582",
        "#8794B3", "#C88D4E", "#8A668F", "#C7B99E"
    ]

    /* A translucent overlay of a role colour, for washes and pressed states. */
    function wash(role, alpha) {
        return Qt.rgba(role.r, role.g, role.b, alpha)
    }

    /* What a saved segment's state looks like. The view model decides meaning. */
    function segmentColor(state) {
        switch (state) {
        case "failed": return danger
        case "retry": return warning
        default: return accent
        }
    }
}
