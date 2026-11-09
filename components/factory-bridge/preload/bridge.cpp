#include "bridge.h"

#include <QFileInfo>
#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLocalServer>
#include <QLocalSocket>
#include <QLockFile>
#include <QMap>
#include <QMetaProperty>
#include <QPointer>
#include <QScopedPointer>
#include <QTimer>
#include <QVariant>
#include <QVariantMap>

#include <cerrno>
#include <sys/stat.h>
#include <unistd.h>

namespace SystemUiWake {
namespace {
const QString Protocol = QStringLiteral("rk3588-desktop-wake/v1");
const QString AppName = QStringLiteral("rk3588-ai");
const char CallSignature[] = "callApp(QVariant,QVariant,QVariant,QVariant,QVariant,QVariant,QVariant)";
const int MaximumLine = 1024;
const int MaximumClients = 8;

class Bridge : public QObject
{
public:
    Bridge(QObject *root, const QString &path, const LogSink &log)
        : QObject(root), m_root(root), m_path(path), m_log(log),
          m_lock(new QLockFile(path + QStringLiteral(".lock")))
    {
        m_lock->setStaleLockTime(0);
        m_server.setSocketOptions(QLocalServer::UserAccessOption);
        m_server.setMaxPendingConnections(MaximumClients);
        connect(&m_server, &QLocalServer::newConnection, this, [this]() { acceptClients(); });
    }

    ~Bridge() override
    {
        for (QLocalSocket *socket : m_clients.keys())
            socket->abort();
        m_server.close();
        record(QStringLiteral("BRIDGE_STOPPED"));
    }

    bool listen(QString *error)
    {
        if (!QFileInfo(m_path).isAbsolute() || m_path.contains(QChar('\0')))
            return fail(error, QStringLiteral("socket-path-must-be-absolute"));
        if (!hasCallApp(m_root.data()))
            return fail(error, QStringLiteral("callApp-method-unavailable"));
        if (QFileInfo(m_path).isSymLink() || QFileInfo(m_path + QStringLiteral(".lock")).isSymLink())
            return fail(error, QStringLiteral("refusing-symlink-socket-or-lock"));
        if (!m_lock->tryLock(0))
            return fail(error, QStringLiteral("bridge-lock-held-or-unavailable"));

        // Only remove a stale socket after acquiring our lock. Never remove a
        // regular file, symlink, or an endpoint that is still accepting clients.
        struct stat previous;
        const QByteArray encoded = QFile::encodeName(m_path);
        if (::lstat(encoded.constData(), &previous) == 0) {
            if (!S_ISSOCK(previous.st_mode) || previous.st_uid != ::geteuid())
                return fail(error, QStringLiteral("refusing-non-owned-socket-path"));
            QLocalSocket probe;
            probe.connectToServer(m_path);
            if (probe.waitForConnected(50)) {
                probe.abort();
                return fail(error, QStringLiteral("socket-already-active"));
            }
            if (probe.error() != QLocalSocket::ConnectionRefusedError
                    && probe.error() != QLocalSocket::ServerNotFoundError)
                return fail(error, QStringLiteral("cannot-prove-socket-stale"));
            struct stat current;
            if (::lstat(encoded.constData(), &current) == 0) {
                if (!S_ISSOCK(current.st_mode) || current.st_dev != previous.st_dev
                        || current.st_ino != previous.st_ino)
                    return fail(error, QStringLiteral("socket-path-changed"));
                if (!QLocalServer::removeServer(m_path))
                    return fail(error, QStringLiteral("stale-socket-remove-failed"));
            } else if (errno != ENOENT) {
                return fail(error, QStringLiteral("socket-path-recheck-failed"));
            }
        } else if (errno != ENOENT) {
            return fail(error, QStringLiteral("socket-path-stat-failed"));
        }
        if (!m_server.listen(m_path))
            return fail(error, QStringLiteral("listen-failed: ") + m_server.errorString());
        record(QStringLiteral("BRIDGE_READY socket=") + m_path);
        return true;
    }

private:
    struct Client {
        QByteArray buffer;
        bool answered = false;
    };

    bool fail(QString *error, const QString &message)
    {
        if (error)
            *error = message;
        record(QStringLiteral("BRIDGE_ERROR ") + message);
        return false;
    }

    void record(const QString &message)
    {
        if (m_log)
            m_log(message);
    }

    void reply(QLocalSocket *socket, bool ok, const QString &action, const QString &error)
    {
        auto item = m_clients.find(socket);
        if (item == m_clients.end() || item->answered)
            return;
        item->answered = true;
        QJsonObject value;
        value.insert(QStringLiteral("protocol"), Protocol);
        value.insert(QStringLiteral("app"), AppName);
        value.insert(QStringLiteral("action"), action);
        value.insert(QStringLiteral("ok"), ok);
        if (!ok)
            value.insert(QStringLiteral("error"), error);
        socket->write(QJsonDocument(value).toJson(QJsonDocument::Compact) + '\n');
        socket->flush();
        socket->disconnectFromServer();
        record(QStringLiteral("BRIDGE_REPLY action=%1 ok=%2 error=%3")
               .arg(action).arg(ok ? 1 : 0).arg(error));
    }

    void acceptClients()
    {
        while (m_server.hasPendingConnections()) {
            QLocalSocket *socket = m_server.nextPendingConnection();
            socket->setParent(this);
            socket->setReadBufferSize(MaximumLine + 1);
            if (m_clients.size() >= MaximumClients) {
                socket->abort();
                socket->deleteLater();
                continue;
            }
            m_clients.insert(socket, Client());
            QTimer *deadline = new QTimer(socket);
            deadline->setSingleShot(true);
            deadline->setInterval(2000);
            connect(deadline, &QTimer::timeout, this, [this, socket]() {
                if (m_clients.remove(socket)) {
                    socket->abort();
                    socket->deleteLater();
                }
            });
            connect(socket, &QLocalSocket::readyRead, this, [this, socket]() { receive(socket); });
            connect(socket, &QLocalSocket::disconnected, this, [this, socket]() {
                m_clients.remove(socket);
                socket->deleteLater();
            });
            deadline->start();
            if (socket->bytesAvailable())
                receive(socket);
        }
    }

    void receive(QLocalSocket *socket)
    {
        auto item = m_clients.find(socket);
        if (item == m_clients.end() || item->answered)
            return;
        item->buffer += socket->readAll();
        const int newline = item->buffer.indexOf('\n');
        if ((newline < 0 && item->buffer.size() > MaximumLine)
                || (newline >= 0 && newline + 1 > MaximumLine)) {
            reply(socket, false, QString(), QStringLiteral("request-too-large"));
            return;
        }
        if (newline < 0)
            return;
        QJsonParseError parseError;
        const QJsonDocument document = QJsonDocument::fromJson(item->buffer.left(newline), &parseError);
        if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
            reply(socket, false, QString(), QStringLiteral("invalid-json"));
            return;
        }
        const QJsonObject request = document.object();
        if (request.value(QStringLiteral("protocol")).toString() != Protocol) {
            reply(socket, false, QString(), QStringLiteral("unsupported-protocol"));
            return;
        }
        if (request.value(QStringLiteral("app")).toString() != AppName) {
            reply(socket, false, QString(), QStringLiteral("unsupported-app"));
            return;
        }
        const QString action = request.value(QStringLiteral("action")).toString();
        if (action == QStringLiteral("check")) {
            reply(socket, hasCallApp(m_root.data()), action,
                  hasCallApp(m_root.data()) ? QString() : QStringLiteral("root-unavailable"));
            return;
        }
        if (action != QStringLiteral("launch")) {
            reply(socket, false, QString(), QStringLiteral("unsupported-action"));
            return;
        }
        if (!hasCallApp(m_root.data())) {
            reply(socket, false, action, QStringLiteral("root-unavailable"));
            return;
        }
        QVariantMap iconSize;
        iconSize.insert(QStringLiteral("width"), 80);
        iconSize.insert(QStringLiteral("height"), 80);
        const QVariant args[] = {QVariant(AppName), QVariant(0), QVariant(0), QVariant(iconSize),
                                 QVariant(QStringLiteral("appicons/rk3588-ai.png")), QVariant(0), QVariant(0)};
        const QPointer<QObject> lifetime(this);
        const bool invoked = QMetaObject::invokeMethod(
            m_root.data(), "callApp", Qt::DirectConnection,
            Q_ARG(QVariant, args[0]), Q_ARG(QVariant, args[1]), Q_ARG(QVariant, args[2]),
            Q_ARG(QVariant, args[3]), Q_ARG(QVariant, args[4]), Q_ARG(QVariant, args[5]),
            Q_ARG(QVariant, args[6]));
        if (lifetime.isNull())
            return;
        QString launchError;
        const bool canonical = invoked && inspectFactoryState(&launchError);
        reply(socket, canonical, action, !invoked ? QStringLiteral("callApp-invoke-failed") : launchError);
    }

    bool inspectFactoryState(QString *error)
    {
        if (m_root.isNull()) {
            *error = QStringLiteral("root-unavailable");
            return false;
        }
        QObject *flagsOwner = m_root.data();
        while (flagsOwner && flagsOwner->metaObject()->indexOfProperty("flags") < 0)
            flagsOwner = flagsOwner->parent();
        const QVariant flags = flagsOwner ? flagsOwner->property("flags") : QVariant();
        bool flagsValid = false;
        const qulonglong flagBits = flags.toULongLong(&flagsValid);
        const QString flagText = flagsValid ? QStringLiteral("0x") + QString::number(flagBits, 16)
                                           : QStringLiteral("unavailable");
        QList<QObject *> objects;
        objects.append(m_root.data());
        objects.append(m_root->findChildren<QObject *>());
        for (QObject *object : objects) {
            const int propertyIndex = object->metaObject()->indexOfProperty("currtentLauchAppName");
            if (propertyIndex < 0 || !object->metaObject()->property(propertyIndex).isReadable())
                continue;
            const QString current = object->property("currtentLauchAppName").toString();
            record(QStringLiteral("BRIDGE_FACTORY_STATE current=%1 rootFlags=%2 class=%3")
                   .arg(current.left(120), flagText, QString::fromLatin1(object->metaObject()->className())));
            if (current != AppName) {
                *error = QStringLiteral("factory-current-app-mismatch");
                return false;
            }
            return true;
        }
        // This property is not exported by QtRO and may be held in a context
        // object outside the root's child tree. Missing diagnostics must not
        // replace or mutate the factory's own callApp routing.
        record(QStringLiteral("BRIDGE_FACTORY_STATE current=unavailable rootFlags=") + flagText);
        return true;
    }

    QPointer<QObject> m_root;
    QString m_path;
    LogSink m_log;
    QScopedPointer<QLockFile> m_lock;
    QLocalServer m_server;
    QMap<QLocalSocket *, Client> m_clients;
};
}

bool hasCallApp(QObject *root)
{
    return root && root->metaObject()->indexOfMethod(CallSignature) >= 0;
}

QObject *createBridge(QObject *root, const QString &socketPath, QString *error, const LogSink &log)
{
    if (error)
        error->clear();
    if (!root) {
        if (error)
            *error = QStringLiteral("root-unavailable");
        return nullptr;
    }
    Bridge *bridge = new Bridge(root, socketPath, log);
    if (!bridge->listen(error)) {
        delete bridge;
        return nullptr;
    }
    return bridge;
}
}
