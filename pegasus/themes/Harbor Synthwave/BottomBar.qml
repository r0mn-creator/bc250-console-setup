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
        property color badgeColor: theme.accent
        property color badgeTextColor: theme.onAccent
        spacing: vpx(10)

        Rectangle {
            width: vpx(30); height: width; radius: width / 2
            color: badgeColor
            anchors.verticalCenter: parent.verticalCenter
            Text {
                anchors.centerIn: parent
                text: letter
                color: badgeTextColor
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

        Badge { letter: "Y"; label: "Favorites"; badgeColor: buttonColor("Y"); badgeTextColor: buttonTextColor("Y") }
    }

    Row {
        anchors { right: parent.right; rightMargin: vpx(28); verticalCenter: parent.verticalCenter }
        spacing: vpx(28)

        Badge { letter: "A"; label: "Hold: Favorite"; badgeColor: buttonColor("A"); badgeTextColor: buttonTextColor("A") }
        Badge { letter: "A"; label: "Play"; badgeColor: buttonColor("A"); badgeTextColor: buttonTextColor("A") }
    }

    // Centered status line + progress bar - polls a small JSON file that
    // background tools (e.g. cover-art-fetcher) write to, so their progress
    // shows up here without the theme knowing anything about what produced
    // it beyond the {running, phase, system, done, total} shape. Both
    // disappear the moment the file says running:false, or if it goes
    // stale (no update in 10s - covers a killed process, not just a clean
    // exit).
    property real statusProgress: 0
    property bool statusHasProgress: false

    Column {
        anchors.centerIn: parent
        spacing: vpx(6)

        Text {
            id: statusText
            anchors.horizontalCenter: parent.horizontalCenter
            color: theme.textDim
            font.pixelSize: vpx(14)
            visible: false
            elide: Text.ElideRight
        }

        Rectangle {
            id: progressTrack
            anchors.horizontalCenter: parent.horizontalCenter
            width: vpx(220)
            height: vpx(4)
            radius: height / 2
            color: Qt.rgba(1, 1, 1, 0.15)
            visible: statusText.visible && statusHasProgress

            Rectangle {
                width: parent.width * Math.max(0, Math.min(1, statusProgress))
                height: parent.height
                radius: height / 2
                color: theme.accent
                Behavior on width { NumberAnimation { duration: 150 } }
            }
        }
    }

    function formatStatus(data) {
        var sys = data.system ? data.system.toUpperCase() : "";
        if (data.phase === "scanning")
            return "Cover art: scanning " + sys + "…";
        if (data.phase === "fetching")
            return "Cover art: " + sys + " " + data.done + "/" + data.total;
        if (data.phase === "starting")
            return "Cover art: starting…";
        return "";
    }

    Timer {
        interval: 2000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: {
            var xhr = new XMLHttpRequest();
            xhr.onreadystatechange = function() {
                if (xhr.readyState !== XMLHttpRequest.DONE)
                    return;
                try {
                    var data = JSON.parse(xhr.responseText);
                    var ageSeconds = Date.now() / 1000 - data.updated_at;
                    if (data.running && ageSeconds < 10) {
                        var msg = formatStatus(data);
                        statusText.text = msg;
                        statusText.visible = msg.length > 0;
                        statusHasProgress = !!(data.total && data.total > 0);
                        statusProgress = statusHasProgress ? (data.done / data.total) : 0;
                    } else {
                        statusText.visible = false;
                        statusHasProgress = false;
                    }
                } catch (e) {
                    statusText.visible = false;
                    statusHasProgress = false;
                }
            };
            xhr.open("GET", "file:///home/R0mn/.config/cover-art-fetcher/status.json");
            xhr.send();
        }
    }
}
