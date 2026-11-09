#include "systemui_bridge.h"

#include <QDataStream>
#include <QDebug>
#include <QMetaMethod>
#include <QMetaProperty>
#include <QRemoteObjectDynamicReplica>
#include <QStringList>
#include <QTimer>
#include <QUrl>

namespace {
const QString AppName = QStringLiteral("rk3588-ai");
enum AppState { Background = 1, Active = 2, WhichState = 3 };
enum Command { Show = 1, Quit = 2 };

// The four-argument protocol predates the byte-array map protocol. These
// numbers come from the manufacturer's IosIDL/Command enums, not AppState.
// See the pinned source references in README.md.
namespace Four {
enum Type { ActivateControl = 1, Messages = 3 };
enum Command { Show = 1, Quit = 2, Background = 3, IsActive = 4, Hide = 5 };
}

QByteArray encode(const QVariantMap &message)
{
    QByteArray data;
    QDataStream stream(&data, QIODevice::WriteOnly);
    // Factory Qt 5.15 code uses the default stream version and sorted QMap
    // iteration. Pin that format explicitly; this is not JSON or DBus.
    stream.setVersion(QDataStream::Qt_5_15);
    stream << quint32(message.size());
    for (auto it = message.constBegin(); it != message.constEnd(); ++it)
        stream << it.key() << it.value();
    return data;
}

bool decode(const QByteArray &data, QVariantMap *message)
{
    if (data.size() > 4 * 1024 * 1024)
        return false;
    QDataStream stream(data);
    stream.setVersion(QDataStream::Qt_5_15);
    quint32 count = 0;
    stream >> count;
    if (count > 64)
        return false;
    for (quint32 i = 0; i < count; ++i) {
        QString key;
        QVariant value;
        stream >> key >> value;
        if (stream.status() != QDataStream::Ok)
            return false;
        message->insert(key, value);
    }
    return stream.status() == QDataStream::Ok && stream.atEnd();
}
}

SystemUiBridge::SystemUiBridge(QObject *parent, bool inspectOnly, const QUrl &source)
    : QObject(parent), m_inspectOnly(inspectOnly)
{
    m_node.connectToNode(source);
    m_replica.reset(m_node.acquireDynamic(QStringLiteral("SystemUICommonApi")));
    connect(m_replica.data(), &QRemoteObjectReplica::initialized,
            this, &SystemUiBridge::onInitialized);
    connect(m_replica.data(), &QRemoteObjectReplica::stateChanged, this,
            [this](QRemoteObjectReplica::State state, QRemoteObjectReplica::State) {
        qInfo() << "SYSTEMUI_STATE" << int(state);
        if (state == QRemoteObjectReplica::Valid) {
            // Also re-announce after a SystemUI restart; do not grab focus on
            // ordinary application activation changes.
            QTimer::singleShot(0, this, &SystemUiBridge::onInitialized);
        } else {
            m_announced = false;
            m_acknowledged = false;
        }
    });
    QTimer::singleShot(5000, this, [this]() {
        if (!m_inspectOnly && !m_acknowledged)
            qWarning() << "SYSTEMUI_HANDSHAKE_PENDING: local:interfaces / SystemUICommonApi";
    });
}

void SystemUiBridge::onInitialized()
{
    if (!m_replica->isReplicaValid())
        return;
    dumpProtocol();
    if (m_inspectOnly) {
        emit inspectionFinished();
        return;
    }
    if (!m_signalConnected) {
        // A DynamicReplica has no remote meta-methods until initialized.
        const QMetaObject *meta = m_replica->metaObject();
        const bool four = meta->indexOfSignal("serverSendVariant(QString,int,int,QVariant)") >= 0
                && meta->indexOfMethod("onServerVariant(QString,int,int,QVariant)") >= 0;
        const bool bytes = meta->indexOfSignal("serverSendVariant(QByteArray)") >= 0
                && meta->indexOfMethod("onServerVariant(QByteArray)") >= 0;
        if (four) {
            m_protocol = FourArguments;
            m_signalConnected = bool(connect(m_replica.data(),
                SIGNAL(serverSendVariant(QString,int,int,QVariant)),
                this, SLOT(onFourMessage(QString,int,int,QVariant))));
        } else if (bytes) {
            m_protocol = Bytes;
            m_signalConnected = bool(connect(m_replica.data(), SIGNAL(serverSendVariant(QByteArray)),
                                             this, SLOT(onMessage(QByteArray))));
        } else {
            qCritical() << "SYSTEMUI_PROTOCOL_MISMATCH: neither supported signature pair found";
            return;
        }
        if (!m_signalConnected)
            return;
        qInfo() << "SYSTEMUI_CONNECTED protocol=" << (four ? "four-arguments" : "byte-array");
    }
    if (!m_announced)
        announceActive();
}

void SystemUiBridge::dumpProtocol()
{
    if (m_protocolLogged)
        return;
    m_protocolLogged = true;
    const QMetaObject *meta = m_replica->metaObject();
    qInfo().noquote() << "SYSTEMUI_API class=" << meta->className()
                      << "methods=" << meta->methodCount();
    // Include inherited entries so a difference in the dynamic object's
    // inheritance or API layout is visible, too. Read the metadata only.
    for (int i = 0; i < meta->methodCount(); ++i) {
        const QMetaMethod method = meta->method(i);
        const char *kind = "method";
        if (method.methodType() == QMetaMethod::Signal)
            kind = "signal";
        else if (method.methodType() == QMetaMethod::Slot)
            kind = "slot";
        QStringList parameterNames;
        for (const QByteArray &name : method.parameterNames())
            parameterNames.append(QString::fromLatin1(name));
        qInfo().noquote() << "SYSTEMUI_API_METHOD" << i << kind
                          << method.methodSignature() << "return=" << method.typeName()
                          << "parameters=" << parameterNames.join(',');
    }
    for (int i = 0; i < meta->propertyCount(); ++i) {
        const QMetaProperty property = meta->property(i);
        qInfo().noquote() << "SYSTEMUI_API_PROPERTY" << property.name()
                          << "type=" << property.typeName();
    }
    qInfo() << "SYSTEMUI_API_END";
}

bool SystemUiBridge::send(const QVariantMap &message)
{
    if (m_inspectOnly || !m_replica->isReplicaValid() || m_protocol != Bytes)
        return false;
    const QByteArray bytes = encode(message);
    const bool invoked = QMetaObject::invokeMethod(
                m_replica.data(), "onServerVariant", Qt::DirectConnection,
                Q_ARG(QByteArray, bytes));
    if (!invoked)
        qWarning() << "SYSTEMUI_SEND_FAILED";
    return invoked;
}

bool SystemUiBridge::sendFour(int type, int command, const QVariant &payload)
{
    if (m_inspectOnly || !m_replica->isReplicaValid() || m_protocol != FourArguments)
        return false;
    const bool invoked = QMetaObject::invokeMethod(
                m_replica.data(), "onServerVariant", Qt::DirectConnection,
                Q_ARG(QString, AppName), Q_ARG(int, type), Q_ARG(int, command),
                Q_ARG(QVariant, payload));
    if (!invoked)
        qWarning() << "SYSTEMUI_SEND_FAILED four-arguments command=" << command;
    return invoked;
}

void SystemUiBridge::announceActive()
{
    if (m_protocol == FourArguments) {
        // Factory client timerTimeOut() sends ActivateControl/Hide after
        // startup. IsActive is a later heartbeat reply, not the start packet.
        m_announced = sendFour(Four::ActivateControl, Four::Hide);
        if (m_announced)
            qInfo() << "SYSTEMUI_HIDE_SENT";
        return;
    }
    QVariantMap message;
    message.insert(QStringLiteral("appName"), AppName);
    message.insert(QStringLiteral("appState"), int(Active));
    m_announced = send(message);
    if (m_announced)
        qInfo() << "SYSTEMUI_ACTIVE_SENT";
}

void SystemUiBridge::onMessage(const QByteArray &data)
{
    if (m_closing)
        return;
    QVariantMap message;
    if (!decode(data, &message)) {
        qWarning() << "SYSTEMUI_BAD_MESSAGE";
        return;
    }
    if (message.value(QStringLiteral("appName")).toString() != AppName)
        return;
    const int command = message.value(QStringLiteral("command")).toInt();
    if (command == Quit) {
        qInfo() << "SYSTEMUI_QUIT_REQUEST";
        emit quitRequested();
        return;
    }
    const int state = message.value(QStringLiteral("appState")).toInt();
    if (state == WhichState) {
        // Factory's hot-launch probe: replying stops its 50 ms spawn timer.
        QVariantMap reply;
        reply.insert(QStringLiteral("appName"), AppName);
        reply.insert(QStringLiteral("appState"), int(Background));
        send(reply);
        qInfo() << "SYSTEMUI_EXISTING_REPLY";
    } else if (state == Background || state == Active) {
        // The server has acknowledged and hidden Home. Activate only here,
        // rather than repeatedly competing with Home in onActiveChanged.
        m_acknowledged = true;
        qInfo() << "SYSTEMUI_SHOW_ACK protocol=byte-array";
        emit showRequested();
    }
}

void SystemUiBridge::onFourMessage(const QString &appName, int type, int command, QVariant payload)
{
    if (m_closing || appName != AppName)
        return;
    if (type == Four::ActivateControl) {
        if (command == Four::Background) {
            if (sendFour(Four::ActivateControl, Four::Background))
                qInfo() << "SYSTEMUI_EXISTING_REPLY protocol=four-arguments";
        } else if (command == Four::IsActive) {
            // The factory checks this about every two seconds. Reply without
            // raising the window, including when another window has focus.
            if (sendFour(Four::ActivateControl, Four::IsActive)) {
                ++m_heartbeatReplies;
                if (m_heartbeatReplies == 1 || m_heartbeatReplies % 30 == 0)
                    qInfo() << "SYSTEMUI_HEARTBEAT_REPLY count=" << m_heartbeatReplies;
            }
        } else if (command == Four::Show) {
            // A hot launch asks the existing app to show. Ask Home to hide;
            // its property message below is the activation acknowledgement.
            sendFour(Four::ActivateControl, Four::Hide);
        } else if (command == Four::Quit) {
            qInfo() << "SYSTEMUI_QUIT_REQUEST";
            emit quitRequested();
        }
    } else if (type == Four::Messages && payload.userType() == QMetaType::QString
               && payload.toString().startsWith(QStringLiteral("property: "))) {
        // The factory sends its seven launch properties after accepting Hide.
        m_acknowledged = true;
        qInfo() << "SYSTEMUI_SHOW_ACK protocol=four-arguments";
        emit showRequested();
    }
}

void SystemUiBridge::returnToHome()
{
    if (m_inspectOnly)
        return;
    m_closing = true;
    if (m_protocol == FourArguments) {
        sendFour(Four::ActivateControl, Four::Show);
        qInfo() << "SYSTEMUI_RETURN_HOME protocol=four-arguments";
        return;
    }
    QVariantMap message;
    message.insert(QStringLiteral("appName"), AppName);
    message.insert(QStringLiteral("command"), int(Show));
    send(message);
    qInfo() << "SYSTEMUI_RETURN_HOME";
    // The factory QProcess finished handler is the fallback if this last
    // asynchronous message cannot be flushed before this process exits.
}
