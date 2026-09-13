// Harbor for Pegasus
// A from-scratch Pegasus theme recreating the look and navigation of the
// "Harbor" Android launcher: a top row of console tabs (with L1/R1 badges),
// a spaced-out box-art grid, and a bottom button-legend bar.
//
// Two pseudo-collections are pinned to the front of the tab list:
// "Recently Played" (only once at least one game has playCount > 0 - this
// makes it the de-facto home screen after the first play) and "Favorites"
// (always present, built from api.allGames rather than a real ROM folder).

import QtQuick 2.9
import SortFilterProxyModel 0.2

FocusScope {
    id: root
    focus: true

    readonly property var theme: ({
        main:      "#1a1a1a",
        panel:     "#0d0d0d",
        pill:      "#2a2a2a",
        accent:    "#00c3e3",
        text:      "#ffffff",
        textDim:   "#8f8f8f",
        onAccent:  "#062026"
    })

    readonly property string buttonTextOnColor: "#161616"
    function buttonColor(name) {
        return (theme.buttons && theme.buttons[name]) || theme.accent;
    }
    function buttonTextColor(name) {
        return (theme.buttons && theme.buttons[name]) ? buttonTextOnColor : theme.onAccent;
    }

    readonly property string recentKey: "__recent__"
    readonly property string favoritesKey: "__favorites__"

    SortFilterProxyModel {
        id: recentlyPlayedGames
        sourceModel: api.allGames
        filters: ExpressionFilter { expression: playCount > 0 }
        sorters: RoleSorter { roleName: "lastPlayed"; sortOrder: Qt.DescendingOrder }
    }

    SortFilterProxyModel {
        id: favoriteGames
        sourceModel: api.allGames
        filters: ValueFilter { roleName: "favorite"; value: true }
        sorters: RoleSorter { roleName: "sort_title"; sortOrder: Qt.AscendingOrder }
    }

    readonly property var recentlyPlayedCollection: ({
        name: "Recently Played", shortName: "RECENT", key: recentKey, games: recentlyPlayedGames, isFavorites: false
    })
    readonly property var favoritesCollection: ({
        name: "Favorites", shortName: "FAV", key: favoritesKey, games: favoriteGames, isFavorites: true
    })

    property var tabs: {
        var list = [];
        if (recentlyPlayedGames.count > 0)
            list.push(recentlyPlayedCollection);
        if (favoriteGames.count > 0)
            list.push(favoritesCollection);
        var count = api.collections.count;
        for (var i = 0; i < count; i++)
            list.push(api.collections.get(i));
        return list;
    }

    property string currentTabKey: ""
    Component.onCompleted: {
        if (tabs.length > 0)
            currentTabKey = tabs[0].shortName || tabs[0].name;
    }

    function tabKeyOf(tab) {
        return tab.key || tab.shortName || tab.name;
    }

    property var currentCollection: {
        for (var i = 0; i < tabs.length; i++) {
            if (tabKeyOf(tabs[i]) === currentTabKey)
                return tabs[i];
        }
        return tabs.length > 0 ? tabs[0] : null;
    }

    property int currentCollectionIndex: {
        for (var i = 0; i < tabs.length; i++) {
            if (tabKeyOf(tabs[i]) === currentTabKey)
                return i;
        }
        return 0;
    }

    function changeCollection(delta) {
        var count = tabs.length;
        if (count <= 0)
            return;
        var idx = (currentCollectionIndex + delta + count) % count;
        currentTabKey = tabKeyOf(tabs[idx]);
    }

    function selectTabIndex(i) {
        if (i >= 0 && i < tabs.length)
            currentTabKey = tabKeyOf(tabs[i]);
    }

    function jumpToFavorites() {
        currentTabKey = favoritesKey;
    }

    function toggleFavorite(game) {
        if (game)
            game.favorite = !game.favorite;
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

    // L1/R1 (isPrevPage/isNextPage) switch tabs no matter which child has
    // focus - grid and topTabs only accept the keys they specifically handle,
    // so this always sees the event if neither of them claimed it first.
    Keys.onPressed: {
        if (api.keys.isNextPage(event) && !event.isAutoRepeat) {
            event.accepted = true;
            changeCollection(1);
        } else if (api.keys.isPrevPage(event) && !event.isAutoRepeat) {
            event.accepted = true;
            changeCollection(-1);
        }
    }
}
