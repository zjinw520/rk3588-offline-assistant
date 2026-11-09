#ifndef SYSTEMUI_BRIDGE_H
#define SYSTEMUI_BRIDGE_H

#include <QObject>
#include <QRemoteObjectNode>
#include <QSharedPointer>
#include <QUrl>
#include <QVariantMap>

class QRemoteObjectDynamicReplica;

// Match the manufacturer's local SystemUICommonApi protocol, without requiring
// the generated .rep header or registering its proprietary QML component.
class SystemUiBridge : public QObject
{
    Q_OBJECT
public:
    explicit SystemUiBridge(QObject *parent = nullptr, bool inspectOnly = false,
                            const QUrl &source = QUrl(QStringLiteral("local:interfaces")));
    void returnToHome();

signals:
    void showRequested();
    void quitRequested();
    void inspectionFinished();

private slots:
    void onInitialized();
    void onMessage(const QByteArray &data);
    void onFourMessage(const QString &appName, int type, int command, QVariant payload);

private:
    bool send(const QVariantMap &message);
    bool sendFour(int type, int command, const QVariant &payload = QVariant());
    void announceActive();
    void dumpProtocol();

    QRemoteObjectNode m_node;
    QSharedPointer<QRemoteObjectDynamicReplica> m_replica;
    bool m_signalConnected = false;
    bool m_announced = false;
    bool m_inspectOnly = false;
    bool m_protocolLogged = false;
    enum Protocol { Unknown, Bytes, FourArguments };
    Protocol m_protocol = Unknown;
    bool m_acknowledged = false;
    bool m_closing = false;
    unsigned int m_heartbeatReplies = 0;
};

#endif
