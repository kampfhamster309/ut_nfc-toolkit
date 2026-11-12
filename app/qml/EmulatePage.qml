import QtQuick 2.12
import QtQuick.Layouts 1.12
import Lomiri.Components 1.3

Page {
    id: page
    header: PageHeader { title: "Emulate a tag"; subtitle: root.emulating ? "Active" : "Type 4 NDEF" }

    Column {
        anchors { left: parent.left; right: parent.right; top: page.header.bottom; margins: units.gu(2) }
        spacing: units.gu(2)

        Label {
            width: parent.width; wrapMode: Text.Wrap; color: "#8a8f99"
            text: "The phone emulates a Type 4 NDEF tag carrying one record. Another phone or reader can read it. " +
                  "The UID is random (the hardware can't present a chosen UID)."
        }

        Row {
            spacing: units.gu(1)
            OptionSelector {
                id: kind; width: units.gu(18)
                model: ["URL", "Text"]
            }
        }

        TextField {
            id: value; width: parent.width
            placeholderText: kind.selectedIndex === 0 ? "https://example.com" : "Hello from the phone"
            text: "https://ubports.com"
        }

        Button {
            width: parent.width
            text: root.emulating ? "Stop emulating" : "Start emulating"
            color: root.emulating ? theme.palette.normal.negative : theme.palette.normal.positive
            onClicked: {
                if (root.emulating) root.send({ t: "stop_emulate" })
                else root.send({ t: "emulate", kind: kind.selectedIndex === 0 ? "uri" : "text", value: value.text })
            }
        }

        Rectangle {
            visible: root.emulating
            width: parent.width; height: units.gu(6); radius: units.gu(1)
            color: "#16233a"; border.color: "#2f7cf6"; border.width: units.dp(1)
            Label { anchors.centerIn: parent; text: "Broadcasting — hold a reader to the phone"; color: "#cfe0ff" }
        }
    }
}
