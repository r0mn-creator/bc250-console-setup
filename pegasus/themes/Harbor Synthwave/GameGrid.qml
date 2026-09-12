import QtQuick 2.9
import QtGraphicalEffects 1.12

FocusScope {
    id: root

    property var collection: currentCollection
    property int columns: 5
    property var gridModel: collection ? collection.games : null

    function currentGame() {
        if (!gridModel || gv.currentIndex < 0)
            return null;
        return gridModel.get ? gridModel.get(gv.currentIndex) : gridModel[gv.currentIndex];
    }

    Rectangle {
        anchors.fill: parent
        color: theme.main
    }

    Text {
        visible: !collection || !gridModel || gridModel.count === 0
        anchors.centerIn: parent
        text: collection && collection.isFavorites ? "No favorites yet - hold A on a game to add one" : "No games found"
        color: theme.textDim
        font.pixelSize: vpx(22)
    }

    GridView {
        id: gv
        focus: true
        anchors.fill: parent
        anchors.margins: vpx(28)
        clip: true
        cellWidth: width / columns
        cellHeight: cellWidth * 1.42 + vpx(30)
        model: gridModel

        Component.onCompleted: currentIndex = 0
        onModelChanged: currentIndex = 0

        delegate: Item {
            id: cell
            width: gv.cellWidth
            height: gv.cellHeight
            property bool active: GridView.isCurrentItem && gv.focus
            property bool longFired: false

            function boxArt(g) {
                if (!g)
                    return "";
                if (g.assets.boxFront)
                    return g.assets.boxFront;
                if (g.assets.logo)
                    return g.assets.logo;
                return "";
            }

            Item {
                id: art
                anchors { left: parent.left; right: parent.right; top: parent.top; margins: vpx(12) }
                height: width * 1.42
                scale: cell.active ? 1.04 : 1.0
                Behavior on scale { NumberAnimation { duration: 120 } }

                Rectangle {
                    anchors.fill: parent
                    color: theme.panel
                    radius: vpx(10)
                    visible: image.status !== Image.Ready
                }

                Image {
                    id: image
                    anchors.fill: parent
                    asynchronous: true
                    fillMode: Image.PreserveAspectCrop
                    source: cell.boxArt(modelData)
                    visible: false
                }

                Rectangle {
                    id: mask
                    anchors.fill: parent
                    radius: vpx(10)
                    visible: false
                }

                OpacityMask {
                    anchors.fill: parent
                    source: image
                    maskSource: mask
                    visible: image.status === Image.Ready
                }

                Rectangle {
                    anchors.fill: parent
                    radius: vpx(10)
                    color: "transparent"
                    border.width: vpx(4)
                    border.color: theme.accent
                    visible: cell.active
                }

                Rectangle {
                    anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
                    height: vpx(34)
                    radius: vpx(10)
                    color: theme.accent
                    visible: cell.active
                    Text {
                        anchors.centerIn: parent
                        width: parent.width - vpx(16)
                        text: modelData ? modelData.title : ""
                        color: theme.onAccent
                        font.pixelSize: vpx(14)
                        font.bold: true
                        elide: Text.ElideRight
                        horizontalAlignment: Text.AlignHCenter
                    }
                }

                Rectangle {
                    width: vpx(18); height: width; radius: width / 2
                    color: theme.accent
                    anchors { right: parent.right; rightMargin: vpx(6); top: parent.top; topMargin: vpx(6) }
                    visible: modelData && modelData.favorite
                }
            }

            Text {
                anchors { top: art.bottom; topMargin: vpx(4); left: parent.left; right: parent.right }
                horizontalAlignment: Text.AlignHCenter
                text: modelData ? modelData.title : ""
                color: theme.textDim
                font.pixelSize: vpx(13)
                elide: Text.ElideRight
                visible: !cell.active
            }

            MouseArea {
                anchors.fill: parent
                onPressed: cell.longFired = false
                onPressAndHold: {
                    cell.longFired = true;
                    gv.currentIndex = index;
                    toggleFavorite(modelData);
                }
                onClicked: {
                    if (cell.longFired)
                        return;
                    if (cell.active)
                        launchGame(modelData);
                    else
                        gv.currentIndex = index;
                }
            }
        }

        // D-pad/stick Up never escapes to the tab bar - only L1/R1 (handled
        // at the theme root) or a mouse click change the active tab.
        Keys.onUpPressed: moveCurrentIndexUp()
        Keys.onDownPressed: moveCurrentIndexDown()
        Keys.onLeftPressed: moveCurrentIndexLeft()
        Keys.onRightPressed: moveCurrentIndexRight()

        property bool acceptHeld: false
        property bool acceptLongFired: false

        Timer {
            id: longPressTimer
            interval: 550
            repeat: false
            onTriggered: {
                if (gv.acceptHeld) {
                    gv.acceptLongFired = true;
                    toggleFavorite(currentGame());
                }
            }
        }

        Keys.onPressed: {
            if (api.keys.isAccept(event)) {
                event.accepted = true;
                if (!event.isAutoRepeat) {
                    acceptHeld = true;
                    acceptLongFired = false;
                    longPressTimer.start();
                }
                return;
            }
            if (api.keys.isFilters(event) && !event.isAutoRepeat) {
                event.accepted = true;
                jumpToFavorites();
                return;
            }
        }

        Keys.onReleased: {
            if (api.keys.isAccept(event) && !event.isAutoRepeat) {
                event.accepted = true;
                longPressTimer.stop();
                if (acceptHeld && !acceptLongFired)
                    launchGame(currentGame());
                acceptHeld = false;
            }
        }
    }
}
