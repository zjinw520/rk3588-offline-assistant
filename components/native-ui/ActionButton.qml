import QtQuick 2.12

Rectangle {
    id: button
    property string label: ""
    property string detail: ""
    property bool selected: false
    property bool danger: false
    signal clicked()

    implicitWidth: 160
    implicitHeight: 56
    radius: 14
    opacity: enabled ? 1.0 : 0.45
    color: touch.pressed ? "#2b4258" : (danger ? "#622c39" : (selected ? "#185d56" : "#233244"))
    border.width: 1
    border.color: selected ? "#59d6b7" : (danger ? "#a35063" : "#39495d")
    activeFocusOnTab: true

    Column {
        anchors.centerIn: parent
        width: parent.width - 16
        spacing: 4
        Text {
            width: parent.width
            text: button.label
            color: "#f0f5fa"
            font.pixelSize: 20
            font.bold: true
            horizontalAlignment: Text.AlignHCenter
            elide: Text.ElideRight
        }
        Text {
            visible: button.detail.length > 0
            width: parent.width
            text: button.detail
            color: "#b5c8d6"
            font.pixelSize: 14
            horizontalAlignment: Text.AlignHCenter
            elide: Text.ElideRight
        }
    }
    MouseArea {
        id: touch
        anchors.fill: parent
        onClicked: button.clicked()
    }
    Keys.onReturnPressed: clicked()
    Keys.onSpacePressed: clicked()
}
