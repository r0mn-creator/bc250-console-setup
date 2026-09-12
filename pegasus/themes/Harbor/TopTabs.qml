import QtQuick 2.9

FocusScope {
    id: root

    Rectangle {
        anchors.fill: parent
        color: theme.main
    }

    Rectangle {
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        height: vpx(1)
        color: Qt.rgba(1, 1, 1, 0.06)
    }

    Rectangle {
        id: l1
        width: vpx(56); height: width; radius: width / 2
        color: theme.accent
        anchors { left: parent.left; leftMargin: vpx(24); verticalCenter: parent.verticalCenter }

        Text {
            anchors.centerIn: parent
            text: "L1"
            color: "white"
            font.pixelSize: vpx(16)
            font.bold: true
        }
        MouseArea { anchors.fill: parent; onClicked: changeCollection(-1) }
    }

    Rectangle {
        id: r1
        width: vpx(56); height: width; radius: width / 2
        color: theme.accent
        anchors { right: parent.right; rightMargin: vpx(24); verticalCenter: parent.verticalCenter }

        Text {
            anchors.centerIn: parent
            text: "R1"
            color: "white"
            font.pixelSize: vpx(16)
            font.bold: true
        }
        MouseArea { anchors.fill: parent; onClicked: changeCollection(1) }
    }

    ListView {
        id: tabList
        focus: true
        anchors {
            left: l1.right; leftMargin: vpx(20)
            right: r1.left; rightMargin: vpx(20)
            verticalCenter: parent.verticalCenter
        }
        height: vpx(56)
        clip: true
        orientation: ListView.Horizontal
        spacing: vpx(14)
        model: tabs
        currentIndex: currentCollectionIndex
        highlightMoveDuration: 150
        preferredHighlightBegin: width * 0.08
        preferredHighlightEnd: width * 0.92
        highlightRangeMode: ListView.ApplyRange

        Keys.onLeftPressed: changeCollection(-1)
        Keys.onRightPressed: changeCollection(1)
        Keys.onDownPressed: grid.focus = true
        Keys.onPressed: {
            if (api.keys.isAccept(event) && !event.isAutoRepeat) {
                event.accepted = true;
                grid.focus = true;
            }
        }

        delegate: Item {
            id: tabItem
            property bool active: index === currentCollectionIndex
            width: Math.max(vpx(72), label.implicitWidth + vpx(36))
            height: tabList.height

            Rectangle {
                anchors.fill: parent
                radius: height / 2
                color: tabItem.active ? theme.pill : "transparent"
                border.width: tabItem.active ? vpx(2) : 0
                border.color: theme.accent
                Behavior on color { ColorAnimation { duration: 120 } }
            }

            Text {
                id: label
                anchors.centerIn: parent
                text: (modelData.shortName || modelData.name).toUpperCase()
                color: tabItem.active ? "white" : theme.textDim
                font.pixelSize: vpx(18)
                font.bold: tabItem.active
            }

            MouseArea {
                anchors.fill: parent
                onClicked: selectTabIndex(index)
            }
        }
    }
}
