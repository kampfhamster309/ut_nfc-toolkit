import QtQuick 2.12
import Lomiri.Components 1.3

Page {
    id: page
    header: PageHeader { title: "Write NDEF"; subtitle: root.tag ? (root.tag.model || root.tag.type) : "" }

    Column {
        anchors { left: parent.left; right: parent.right; top: page.header.bottom; margins: units.gu(2) }
        spacing: units.gu(2)

        Label { width: parent.width; wrapMode: Text.Wrap; color: "#8a8f99"
                text: "Writes one NDEF record to the current Type 2 tag (NTAG/Ultralight). This overwrites the tag's user memory." }

        OptionSelector { id: kind; width: units.gu(18); model: ["URL", "Text"] }

        TextField {
            id: value; width: parent.width
            placeholderText: kind.selectedIndex === 0 ? "https://example.com" : "Some text"
        }

        Button {
            width: parent.width; text: "Write to tag"; color: theme.palette.normal.positive
            enabled: value.text.length > 0 && root.tag && root.tag.cloneable
            onClicked: {
                root.send({ t: "write_ndef", kind: kind.selectedIndex === 0 ? "uri" : "text", value: value.text })
                pageStack.pop()
            }
        }
        Label { visible: !(root.tag && root.tag.cloneable); width: parent.width; wrapMode: Text.Wrap
                color: "#f5b623"; text: "No writable Type 2 tag present." }
    }
}
