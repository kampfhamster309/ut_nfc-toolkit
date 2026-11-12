import QtQuick 2.12
import QtQuick.Layouts 1.12
import Lomiri.Components 1.3

Page {
    id: page
    header: PageHeader {
        title: "Library"
        subtitle: "Saved .nfc files in ~/Documents/NFC"
        trailingActionBar.actions: [
            Action { iconName: "reload"; text: "Refresh"; onTriggered: root.send({ t: "library" }) }
        ]
    }

    property var items: ListModel {}

    Connections {
        target: root
        function onLibraryList(list) {
            page.items.clear()
            for (var i = 0; i < list.length; i++) page.items.append(list[i])
        }
    }

    Component.onCompleted: root.send({ t: "library" })

    Label {
        anchors.centerIn: parent; visible: page.items.count === 0
        text: "No saved tags yet.\nRead a tag and tap Save."; horizontalAlignment: Text.AlignHCenter; color: "#8a8f99"
    }

    ListView {
        anchors { left: parent.left; right: parent.right; top: page.header.bottom; bottom: parent.bottom }
        model: page.items; clip: true
        delegate: ListItem {
            height: units.gu(9)
            Column {
                anchors { left: parent.left; right: cloneBtn.left; verticalCenter: parent.verticalCenter; leftMargin: units.gu(2); rightMargin: units.gu(1) }
                spacing: units.gu(0.3)
                Label { text: model.name || ""; elide: Text.ElideRight; width: parent.width; color: "#fff" }
                Label { text: (model.device_type || "–") + (model.uid ? "  ·  " + model.uid : "") + (model.pages ? "  ·  " + model.pages + " pages" : "")
                        textSize: Label.Small; color: "#8a8f99"; elide: Text.ElideRight; width: parent.width }
            }
            Button {
                id: cloneBtn
                anchors { right: parent.right; verticalCenter: parent.verticalCenter; rightMargin: units.gu(2) }
                text: "Clone →"; visible: model.pages > 0
                onClicked: { root.send({ t: "clone", file: model.name }); pageStack.pop() }
            }
        }
    }
}
