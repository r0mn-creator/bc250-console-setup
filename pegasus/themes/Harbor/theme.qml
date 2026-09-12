// Harbor for Pegasus
// A from-scratch Pegasus theme recreating the look and navigation of the
// "Harbor" Android launcher: a top row of console tabs (with L1/R1 badges),
// a spaced-out box-art grid, and a bottom button-legend bar.

import QtQuick 2.9

FocusScope {
    id: root
    focus: true

    readonly property var theme: ({
        main:      "#120f25",
        panel:     "#0a0711",
        pill:      "#52133f",
        accent:    "#ea337e",
        text:      "#ffffff",
        textDim:   "#9f95c0"
    })

    property int currentCollectionIndex: 0
    property var currentCollection: api.collections.count > 0 ? api.collections.get(currentCollectionIndex) : null
    property bool showFavoritesOnly: false

    function changeCollection(delta) {
        var count = api.collections.count;
        if (count <= 0)
            return;
        currentCollectionIndex = (currentCollectionIndex + delta + count) % count;
    }

    function toggleFavoritesOnly() {
        showFavoritesOnly = !showFavoritesOnly;
    }

    function launchGame(game) {
        if (game)
            game.launch();
    }

    Rectangle {
        anchors.fill: parent
        color: theme.main
    }

    TopTabs {
        id: topTabs
        anchors { left: parent.left; right: parent.right; top: parent.top }
        height: vpx(104)
    }

    GameGrid {
        id: grid
        anchors {
            left: parent.left; right: parent.right
            top: topTabs.bottom
            bottom: bottomBar.top
        }
        focus: true
    }

    BottomBar {
        id: bottomBar
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        height: vpx(64)
    }
}
