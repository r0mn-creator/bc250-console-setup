import QtQuick 2.9

Item {
    id: root

    Rectangle {
        anchors.fill: parent
        color: theme.panel
    }

    Rectangle {
        anchors { left: parent.left; right: parent.right; top: parent.top }
        height: vpx(1)
        color: Qt.rgba(1, 1, 1, 0.06)
    }

    component Badge: Row {
        property string letter: "A"
        property string label: ""
        spacing: vpx(10)

        Rectangle {
            width: vpx(30); height: width; radius: width / 2
            color: theme.accent
            anchors.verticalCenter: parent.verticalCenter
            Text {
                anchors.centerIn: parent
                text: letter
                color: theme.onAccent
                font.bold: true
                font.pixelSize: vpx(15)
            }
        }
        Text {
            text: label
            color: theme.text
            font.pixelSize: vpx(16)
            anchors.verticalCenter: parent.verticalCenter
        }
    }

    Row {
        anchors { left: parent.left; leftMargin: vpx(28); verticalCenter: parent.verticalCenter }
        spacing: vpx(28)

        Badge { letter: "Y"; label: "Favorites" }
    }

    Row {
        anchors { right: parent.right; rightMargin: vpx(28); verticalCenter: parent.verticalCenter }
        spacing: vpx(28)

        Badge { letter: "A"; label: "Hold: Favorite" }
        Badge { letter: "A"; label: "Play" }
    }
}
