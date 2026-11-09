.pragma library

// QML's XMLHttpRequest is asynchronous. A QML Timer supplies the timeout on
// Qt 5 builds that do not implement the browser's xhr.timeout property.
function request(owner, method, path, body, callback, timeoutMs, baseUrl) {
    var xhr = new XMLHttpRequest();
    var completed = false;
    var timer = null;

    function finish(error, data) {
        if (completed) return;
        completed = true;
        if (timer) {
            timer.stop();
            timer.destroy();
        }
        callback(error, data);
    }

    try {
        timer = owner.makeTimeout(timeoutMs || 5000, function() {
            finish("本地服务请求超时", null);
            xhr.abort();
        });
        xhr.onreadystatechange = function() {
            if (completed || xhr.readyState !== XMLHttpRequest.DONE) return;
            var data = null;
            try {
                data = xhr.responseText ? JSON.parse(xhr.responseText) : {};
                if (data === null || typeof data !== "object")
                    throw new Error("Expected JSON object");
            } catch (error) {
                finish("服务没有返回有效 JSON", null);
                return;
            }
            if (xhr.status < 200 || xhr.status >= 300) {
                finish(data.error || ("HTTP " + xhr.status + "，请检查本地服务"), data);
                return;
            }
            finish(null, data);
        };
        xhr.open(method, (baseUrl || "http://127.0.0.1:8081") + path);
        if (method === "POST") xhr.setRequestHeader("Content-Type", "application/json");
        xhr.send(body === null || body === undefined ? null : JSON.stringify(body));
    } catch (error) {
        finish(String(error), null);
    }
}
