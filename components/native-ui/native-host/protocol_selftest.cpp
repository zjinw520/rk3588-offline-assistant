#include "protocol_selftest.h"
#include "systemui_bridge.h"

#include <QCoreApplication>
#include <QDataStream>
#include <QDebug>
#include <QEventLoop>
#include <QRemoteObjectHost>
#include <QTimer>
#include <functional>

// Model the two factory wire contracts at unique local URLs. These fixtures
// never connect to local:interfaces and create no display window. Literal
// commands intentionally do not use the bridge's private enum declarations.
class FourFactoryFixture : public QObject
{
    Q_OBJECT
public:
    int received = 0;
    int invalid = 0;
    int hides = 0;
    int backgrounds = 0;
    int heartbeats = 0;
    int home = 0;

signals:
    void serverSendVariant(const QString &appName, int type, int command, QVariant payload);

public slots:
    void onServerVariant(const QString &appName, int type, int command, QVariant payload)
    {
        ++received;
        // Factory control commands ignore payload. Qt 5 DynamicReplica may
        // deliver QVariant(QVariant, QVariant(Invalid)), which is itself valid.
        // Do not reject a correctly routed control command on that basis.
        if (appName != QStringLiteral("rk3588-ai") || type != 1) {
            ++invalid;
            return;
        }
        if (command == 5) {
            ++hides;
            qInfo() << "SYSTEMUI_SELFTEST_RX_HIDE" << appName << type << command
                    << "payload=" << payload;
            emit serverSendVariant(appName, 3, 0,
                QStringLiteral("property: 0 0 80 80 appicons/rk3588-ai.png 0 0"));
        } else if (command == 3) {
            ++backgrounds;
            emit serverSendVariant(appName, 1, 1, QVariant());
        } else if (command == 4) {
            ++heartbeats;
        } else if (command == 1) {
            ++home;
        } else {
            ++invalid;
        }
    }
};

static QByteArray packMap(const QVariantMap &map)
{
    QByteArray bytes;
    QDataStream stream(&bytes, QIODevice::WriteOnly);
    stream.setVersion(QDataStream::Qt_5_15);
    stream << quint32(map.size());
    for (auto it = map.constBegin(); it != map.constEnd(); ++it)
        stream << it.key() << it.value();
    return bytes;
}

class BytesFactoryFixture : public QObject
{
    Q_OBJECT
public:
    int active = 0;
    int backgrounds = 0;
    int home = 0;
    int invalid = 0;

signals:
    void serverSendVariant(const QByteArray &data);

public slots:
    void onServerVariant(const QByteArray &bytes)
    {
        QDataStream stream(bytes);
        stream.setVersion(QDataStream::Qt_5_15);
        quint32 count = 0;
        stream >> count;
        if (count > 64) {
            ++invalid;
            return;
        }
        QVariantMap map;
        for (quint32 i = 0; i < count; ++i) {
            QString key;
            QVariant value;
            stream >> key >> value;
            map.insert(key, value);
        }
        if (stream.status() != QDataStream::Ok
                || map.value(QStringLiteral("appName")).toString() != QStringLiteral("rk3588-ai")) {
            ++invalid;
            return;
        }
        const int state = map.value(QStringLiteral("appState")).toInt();
        if (state == 2 || state == 1) {
            if (state == 2)
                ++active;
            else
                ++backgrounds;
            emit serverSendVariant(packMap(map));
        } else if (map.value(QStringLiteral("command")).toInt() == 1) {
            ++home;
        } else {
            ++invalid;
        }
    }
};

namespace {
bool waitUntil(const std::function<bool()> &ready)
{
    if (ready())
        return true;
    QEventLoop loop;
    QTimer poll;
    QTimer timeout;
    timeout.setSingleShot(true);
    QObject::connect(&poll, &QTimer::timeout, &loop, [&]() {
        if (ready())
            loop.quit();
    });
    QObject::connect(&timeout, &QTimer::timeout, &loop, &QEventLoop::quit);
    poll.start(5);
    timeout.start(3000);
    loop.exec();
    return ready();
}

QUrl testUrl(const QString &suffix)
{
    return QUrl(QStringLiteral("local:rk3588-ai-selftest-%1-%2")
                .arg(QCoreApplication::applicationPid()).arg(suffix));
}

#define CHECK(condition, label) do { \
    if (!(condition)) { \
        qCritical() << "SYSTEMUI_SELFTEST_FAIL" << label; \
        return false; \
    } \
} while (false)

bool fourArgumentTest()
{
    FourFactoryFixture factory;
    QRemoteObjectHost host;
    const QUrl url = testUrl(QStringLiteral("four"));
    CHECK(host.setHostUrl(url) && host.enableRemoting(&factory, QStringLiteral("SystemUICommonApi")),
          "four-argument fixture listen");
    SystemUiBridge bridge(nullptr, false, url);
    int shows = 0;
    int quits = 0;
    QObject::connect(&bridge, &SystemUiBridge::showRequested, [&]() { ++shows; });
    QObject::connect(&bridge, &SystemUiBridge::quitRequested, [&]() { ++quits; });
    const bool started = waitUntil([&]() { return factory.hides == 1 && shows == 1; });
    if (!started)
        qCritical() << "SYSTEMUI_SELFTEST_STARTUP_COUNTS received=" << factory.received
                    << "rejected=" << factory.invalid << "hides=" << factory.hides
                    << "shows=" << shows;
    CHECK(started, "initial Hide and acknowledgement");

    // A valid final heartbeat drains the preceding foreign messages, too.
    emit factory.serverSendVariant(QStringLiteral("other-app"), 1, 4, QVariant());
    emit factory.serverSendVariant(QStringLiteral("rk3588-ai"), 2, 4, QVariant());
    for (int i = 1; i <= 3; ++i) {
        emit factory.serverSendVariant(QStringLiteral("rk3588-ai"), 1, 4, QVariant());
        CHECK(waitUntil([&]() { return factory.heartbeats == i; }), "heartbeat reply");
    }
    CHECK(factory.received == 4 && shows == 1, "heartbeat must not refocus or answer another app");

    emit factory.serverSendVariant(QStringLiteral("rk3588-ai"), 1, 3, QVariant());
    CHECK(waitUntil([&]() { return factory.backgrounds == 1 && factory.hides == 2 && shows == 2; }),
          "hot launch probe, Show, Hide, acknowledgement");
    emit factory.serverSendVariant(QStringLiteral("rk3588-ai"), 1, 2, QVariant());
    CHECK(waitUntil([&]() { return quits == 1; }), "factory Quit");
    bridge.returnToHome();
    CHECK(waitUntil([&]() { return factory.home == 1; }), "return Home");
    CHECK(factory.invalid == 0, "four-argument app name, control type and command values");
    qInfo() << "SYSTEMUI_SELFTEST_PASS four-arguments: startup, 3 heartbeats, hot launch, quit, Home";
    return true;
}

bool byteArrayTest()
{
    BytesFactoryFixture factory;
    QRemoteObjectHost host;
    const QUrl url = testUrl(QStringLiteral("bytes"));
    CHECK(host.setHostUrl(url) && host.enableRemoting(&factory, QStringLiteral("SystemUICommonApi")),
          "byte-array fixture listen");
    SystemUiBridge bridge(nullptr, false, url);
    int shows = 0;
    int quits = 0;
    QObject::connect(&bridge, &SystemUiBridge::showRequested, [&]() { ++shows; });
    QObject::connect(&bridge, &SystemUiBridge::quitRequested, [&]() { ++quits; });
    CHECK(waitUntil([&]() { return factory.active == 1 && shows == 1; }), "byte-array startup");
    QVariantMap message;
    message.insert(QStringLiteral("appName"), QStringLiteral("rk3588-ai"));
    message.insert(QStringLiteral("appState"), 3);
    emit factory.serverSendVariant(packMap(message));
    CHECK(waitUntil([&]() { return factory.backgrounds == 1 && shows == 2; }), "byte-array hot launch");
    message.remove(QStringLiteral("appState"));
    message.insert(QStringLiteral("command"), 2);
    emit factory.serverSendVariant(packMap(message));
    CHECK(waitUntil([&]() { return quits == 1; }), "byte-array Quit");
    bridge.returnToHome();
    CHECK(waitUntil([&]() { return factory.home == 1; }), "byte-array Home");
    CHECK(factory.invalid == 0, "byte-array wire values");
    qInfo() << "SYSTEMUI_SELFTEST_PASS byte-array compatibility";
    return true;
}

bool inspectionTest()
{
    FourFactoryFixture factory;
    QRemoteObjectHost host;
    const QUrl url = testUrl(QStringLiteral("inspect"));
    CHECK(host.setHostUrl(url) && host.enableRemoting(&factory, QStringLiteral("SystemUICommonApi")),
          "inspection fixture listen");
    SystemUiBridge bridge(nullptr, true, url);
    bool done = false;
    QObject::connect(&bridge, &SystemUiBridge::inspectionFinished, [&]() { done = true; });
    CHECK(waitUntil([&]() { return done; }), "read-only inspection completes");
    bridge.returnToHome();
    bool drained = false;
    QTimer::singleShot(50, &bridge, [&]() { drained = true; });
    CHECK(waitUntil([&]() { return drained; }), "inspection drain");
    CHECK(factory.received == 0, "inspection sends no application command");
    qInfo() << "SYSTEMUI_SELFTEST_PASS read-only inspection";
    return true;
}
}

int runSystemUiSelfTest(int argc, char *argv[])
{
    QCoreApplication app(argc, argv);
    QCoreApplication::setApplicationName(QStringLiteral("rk3588-ai-selftest"));
    if (!fourArgumentTest() || !byteArrayTest() || !inspectionTest())
        return 1;
    qInfo() << "SYSTEMUI_SELFTEST_OK v0.4.3";
    return 0;
}

#include "protocol_selftest.moc"
