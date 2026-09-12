import QtQuick 2.9
import QtGraphicalEffects 1.12

FocusScope {
    id: root

    property var collection: currentCollection
    property var favoritesArray: []
    property int columns: 5

    function rebuildFavorites() {
        favoritesArray = collection ? collection.games.toVarArray().filter(function (g) { return g.favorite; }) : [];
    }
    onCollectionChanged: rebuildFavorites()
    Component.onCompleted: rebuildFavorites()

    property var gridModel: showFavoritesOnly ? favoritesArray : (collection ? collection.games : null)

    Rectangle {
        anchors.fill: parent
        color: theme.main
    }

    Text {
        visible: !collection || (gridModel && gridModel.count === 0) || (showFavoritesOnly && favoritesArray.length === 0)
        anchors.centerIn: parent
        text: showFavoritesOnly ? "No favorites yet" : "No games found"
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
                        color: "white"
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
                onClicked: {
                    if (cell.active)
                        launchGame(modelData);
                    else
                        gv.currentIndex = index;
                }
            }
        }

        Keys.onUpPressed: {
            if (currentIndex < columns)
                topTabs.focus = true;
            else
                moveCurrentIndexUp();
        }
        Keys.onDownPressed: moveCurrentIndexDown()
        Keys.onLeftPressed: moveCurrentIndexLeft()
        Keys.onRightPressed: moveCurrentIndexRight()

        Keys.onPressed: {
            if (api.keys.isAccept(event) && !event.isAutoRepeat) {
                event.accepted = true;
                if (model && currentIndex >= 0)
                    launchGame(model.get ? model.get(currentIndex) : model[currentIndex]);
                return;
            }
            if (api.keys.isDetails(event) && !event.isAutoRepeat) {
                event.accepted = true;
                var g = model && model.get ? model.get(currentIndex) : (model ? model[currentIndex] : null);
                if (g) {
                    g.favorite = !g.favorite;
                    rebuildFavorites();
                }
                return;
            }
            if (api.keys.isFilters(event) && !event.isAutoRepeat) {
                event.accepted = true;
                toggleFavoritesOnly();
                return;
            }
        }
    }
}
