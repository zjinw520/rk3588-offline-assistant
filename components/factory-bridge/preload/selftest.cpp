#include "bridge.h"

#include <QCoreApplication>
#include <QEventLoop>
#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJSValue>
#include <QLocalSocket>
#include <QQmlComponent>
#include <QQmlEngine>
#include <QQmlError>
#include <QScopedPointer>
#include <QStringList>
#include <QTemporaryDir>
#include <QTextStream>
#include <QTimer>
#include <QUrl>
#include <QVariant>
#include <QVariantList>

#include <cstdio>
#include <sys/stat.h>

namespace {
void output(const QString &message)
{
    QTextStream stream(stdout);
    stream << message << '\n';
    stream.flush();
}

bool waitForReady(QQmlComponent &component, int milliseconds = 5000)
{
    if (component.isLoading()) {
        QEventLoop loop;
        QTimer deadline;
        deadline.setSingleShot(true);
        QObject::connect(&component, &QQmlComponent::statusChanged, &loop,
                         [&loop](QQmlComponent::Status status) {
            if (status != QQmlComponent::Loading)
                loop.quit();
        });
        QObject::connect(&deadline, &QTimer::timeout, &loop, &QEventLoop::quit);
        deadline.start(milliseconds);
        if (component.isLoading())
            loop.exec();
        deadline.stop();
    }
    return component.isReady();
}

QString qmlDiagnostic(const QQmlComponent &component, const QQmlEngine &engine)
{
    QStringList details;
    details.append(QStringLiteral("status=%1 url=%2")
                   .arg(static_cast<int>(component.status())).arg(component.url().toString()));
    const auto errors = component.errors();
    for (const QQmlError &error : errors)
        details.append(error.toString());
    if (errors.isEmpty())
        details.append(QStringLiteral("no-QML-errors-reported"));
    details.append(QStringLiteral("importPaths=") + engine.importPathList().join(QChar(';')));
    return details.join(QChar('\n'));
}

QByteArray packet(const QString &action, const QString &app = QStringLiteral("rk3588-ai"),
                  const QString &protocol = QStringLiteral("rk3588-desktop-wake/v1"))
{
    QJsonObject value;
    value.insert(QStringLiteral("protocol"), protocol);
    value.insert(QStringLiteral("app"), app);
    value.insert(QStringLiteral("action"), action);
    return QJsonDocument(value).toJson(QJsonDocument::Compact) + '\n';
}

QJsonObject exchange(const QString &path, const QByteArray &request, QString *error)
{
    error->clear();
    QLocalSocket socket;
    QEventLoop loop;
    QTimer timeout;
    QByteArray response;
    timeout.setSingleShot(true);
    timeout.setInterval(1500);
    QObject::connect(&socket, &QLocalSocket::connected, &loop, [&]() {
        socket.write(request);
        socket.flush();
    });
    QObject::connect(&socket, &QLocalSocket::readyRead, &loop, [&]() {
        response += socket.readAll();
        if (response.contains('\n'))
            loop.quit();
    });
    QObject::connect(&socket, &QLocalSocket::disconnected, &loop, [&]() {
        response += socket.readAll();
        loop.quit();
    });
    QObject::connect(&socket, &QLocalSocket::errorOccurred, &loop, [&]() { loop.quit(); });
    QObject::connect(&timeout, &QTimer::timeout, &loop, &QEventLoop::quit);
    timeout.start();
    socket.connectToServer(path);
    loop.exec();
    timeout.stop();
    socket.abort();
    const int newline = response.indexOf('\n');
    if (newline < 0) {
        *error = QStringLiteral("no-newline-response: ") + socket.errorString();
        return QJsonObject();
    }
    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(response.left(newline), &parseError);
    if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
        *error = QStringLiteral("invalid-response-json");
        return QJsonObject();
    }
    return document.object();
}

int tests()
{
    QTemporaryDir directory;
    if (!directory.isValid()) {
        output(QStringLiteral("SYSTEMUI_WAKE_SELFTEST_FAIL temporary-directory"));
        return 1;
    }
    QQmlEngine engine;
    const QByteArray fixture = QByteArrayLiteral(
        "import QtQml 2.12\n"
        "QtObject {\n"
        " property int calls: 0\n"
        " property var received: []\n"
        " property bool simulateMismatch: false\n"
        " property QtObject factory: QtObject { property string currtentLauchAppName: 'null' }\n"
        " function callApp(appName, x, y, size, icon, type, page) {\n"
        "  calls = calls + 1;\n"
        "  received = [appName, x, y, size.width, size.height, icon, type, page];\n"
        "  if (!simulateMismatch) factory.currtentLauchAppName = appName;\n"
        " }\n"
        "}\n");
    const QString fixturePath = directory.path() + QStringLiteral("/wake-selftest.qml");
    QFile fixtureFile(fixturePath);
    if (!fixtureFile.open(QIODevice::WriteOnly) || fixtureFile.write(fixture) != fixture.size()) {
        output(QStringLiteral("SYSTEMUI_WAKE_SELFTEST_FAIL write-QML-fixture: ")
               + fixtureFile.errorString());
        return 1;
    }
    fixtureFile.close();
    QQmlComponent component(&engine);
    component.loadUrl(QUrl::fromLocalFile(fixturePath), QQmlComponent::PreferSynchronous);
    // Local imports can still be asynchronous. Never create a Loading component.
    if (!waitForReady(component)) {
        output(QStringLiteral("SYSTEMUI_WAKE_SELFTEST_FAIL QML-load:\n")
               + qmlDiagnostic(component, engine));
        return 1;
    }
    QScopedPointer<QObject> root(component.create());
    if (root.isNull()) {
        output(QStringLiteral("SYSTEMUI_WAKE_SELFTEST_FAIL QML-create:\n")
               + qmlDiagnostic(component, engine));
        return 1;
    }

    // Exercise the readiness wait on a separately loaded asynchronous fixture.
    const QString asyncPath = directory.path() + QStringLiteral("/wake-async.qml");
    if (!QFile::copy(fixturePath, asyncPath)) {
        output(QStringLiteral("SYSTEMUI_WAKE_SELFTEST_FAIL copy-async-fixture"));
        return 1;
    }
    QQmlComponent asynchronous(&engine);
    asynchronous.loadUrl(QUrl::fromLocalFile(asyncPath), QQmlComponent::Asynchronous);
    if (!waitForReady(asynchronous)) {
        output(QStringLiteral("SYSTEMUI_WAKE_SELFTEST_FAIL QML-async:\n")
               + qmlDiagnostic(asynchronous, engine));
        return 1;
    }
    QScopedPointer<QObject> asyncRoot(asynchronous.create());
    if (asyncRoot.isNull()) {
        output(QStringLiteral("SYSTEMUI_WAKE_SELFTEST_FAIL QML-async-create:\n")
               + qmlDiagnostic(asynchronous, engine));
        return 1;
    }
    QQmlComponent invalid(&engine);
    invalid.setData(QByteArrayLiteral("import QtQml 2.12\nQtObject { property int broken: }\n"),
                    QUrl::fromLocalFile(directory.path() + QStringLiteral("/invalid.qml")));
    if (waitForReady(invalid) || !invalid.isError() || invalid.errors().isEmpty()) {
        output(QStringLiteral("SYSTEMUI_WAKE_SELFTEST_FAIL QML-invalid-not-rejected:\n")
               + qmlDiagnostic(invalid, engine));
        return 1;
    }
    output(QStringLiteral("SYSTEMUI_WAKE_SELFTEST_QML_LOADER_OK v1.1"));
    const QString path = directory.path() + QStringLiteral("/wake.sock");
    QString error;
    QObject *bridge = SystemUiWake::createBridge(root.data(), path, &error);
    if (!bridge) {
        output(QStringLiteral("SYSTEMUI_WAKE_SELFTEST_FAIL create: ") + error);
        return 1;
    }
    auto fail = [](const QString &name) {
        output(QStringLiteral("SYSTEMUI_WAKE_SELFTEST_FAIL ") + name);
        return 1;
    };
    struct stat socketInfo;
    if (::lstat(QFile::encodeName(path).constData(), &socketInfo) != 0
            || !S_ISSOCK(socketInfo.st_mode) || (socketInfo.st_mode & 0077) != 0)
        return fail(QStringLiteral("user-only-socket-permissions"));
    QJsonObject response = exchange(path, packet(QStringLiteral("check")), &error);
    if (!error.isEmpty() || !response.value(QStringLiteral("ok")).toBool()
            || root->property("calls").toInt() != 0)
        return fail(QStringLiteral("check-is-read-only ") + error);
    if (response.value(QStringLiteral("protocol")).toString() != QStringLiteral("rk3588-desktop-wake/v1")
            || response.value(QStringLiteral("app")).toString() != QStringLiteral("rk3588-ai"))
        return fail(QStringLiteral("response-identity"));

    response = exchange(path, packet(QStringLiteral("launch")), &error);
    if (!error.isEmpty() || !response.value(QStringLiteral("ok")).toBool()
            || root->property("calls").toInt() != 1)
        return fail(QStringLiteral("launch ") + error);
    QVariant received = root->property("received");
    if (received.userType() == qMetaTypeId<QJSValue>())
        received = received.value<QJSValue>().toVariant();
    const QVariantList args = received.toList();
    if (args.size() != 8 || args[0].toString() != QStringLiteral("rk3588-ai")
            || args[1].toInt() != 0 || args[2].toInt() != 0
            || args[3].toInt() != 80 || args[4].toInt() != 80
            || args[5].toString() != QStringLiteral("appicons/rk3588-ai.png")
            || args[6].toInt() != 0 || args[7].toInt() != 0)
        return fail(QStringLiteral("seven-variant-arguments-and-icon-map"));

    const QByteArray invalidRequests[] = {
        packet(QStringLiteral("launch"), QStringLiteral("camera")),
        packet(QStringLiteral("launch"), QStringLiteral("rk3588-ai"), QStringLiteral("wrong")),
        packet(QStringLiteral("quit")),
        QByteArrayLiteral("not json\n"),
        QByteArray(1025, 'x') + '\n'
    };
    for (const QByteArray &request : invalidRequests) {
        response = exchange(path, request, &error);
        if (!error.isEmpty() || response.isEmpty() || response.value(QStringLiteral("ok")).toBool()
                || response.value(QStringLiteral("error")).toString().isEmpty()
                || root->property("calls").toInt() != 1)
            return fail(QStringLiteral("invalid-request-rejected ") + error);
    }
    QObject *second = SystemUiWake::createBridge(root.data(), path, &error);
    if (second) {
        delete second;
        return fail(QStringLiteral("singleton-lock"));
    }
    response = exchange(path, packet(QStringLiteral("check")), &error);
    if (!error.isEmpty() || !response.value(QStringLiteral("ok")).toBool())
        return fail(QStringLiteral("singleton-does-not-remove-live-socket ") + error);

    const QString ordinary = directory.path() + QStringLiteral("/ordinary");
    QFile file(ordinary);
    if (!file.open(QIODevice::WriteOnly))
        return fail(QStringLiteral("regular-file-fixture"));
    file.write("keep");
    file.close();
    QObject *bad = SystemUiWake::createBridge(root.data(), ordinary, &error);
    if (bad) {
        delete bad;
        return fail(QStringLiteral("regular-file-not-rejected"));
    }
    if (!file.open(QIODevice::ReadOnly) || file.readAll() != QByteArrayLiteral("keep"))
        return fail(QStringLiteral("regular-file-modified"));
    file.close();
    const QString link = directory.path() + QStringLiteral("/link.sock");
    if (!QFile::link(ordinary, link))
        return fail(QStringLiteral("symlink-fixture"));
    bad = SystemUiWake::createBridge(root.data(), link, &error);
    if (bad) {
        delete bad;
        return fail(QStringLiteral("symlink-not-rejected"));
    }
    const QVariant factoryValue = root->property("factory");
    QObject *factory = factoryValue.value<QObject *>();
    if (!factory && factoryValue.userType() == qMetaTypeId<QJSValue>())
        factory = factoryValue.value<QJSValue>().toQObject();
    if (!factory)
        return fail(QStringLiteral("factory-property-fixture"));
    factory->setProperty("currtentLauchAppName", QStringLiteral("null"));
    root->setProperty("simulateMismatch", true);
    response = exchange(path, packet(QStringLiteral("launch")), &error);
    if (!error.isEmpty() || response.value(QStringLiteral("ok")).toBool()
            || response.value(QStringLiteral("error")).toString() != QStringLiteral("factory-current-app-mismatch")
            || root->property("calls").toInt() != 2)
        return fail(QStringLiteral("mismatched-factory-state-rejected ") + error);
    output(QStringLiteral("SYSTEMUI_WAKE_SELFTEST_OK v1.1"));
    return 0;
}
}

int main(int argc, char *argv[])
{
    QCoreApplication app(argc, argv);
    QCoreApplication::setApplicationName(QStringLiteral("systemui-wake-selftest"));
    output(QStringLiteral("SYSTEMUI_WAKE_SELFTEST_START v1.1"));
    QTimer::singleShot(0, &app, [&app]() { app.exit(tests()); });
    return app.exec();
}
