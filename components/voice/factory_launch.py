#!/usr/bin/env python3
"""Allowlisted local requests to the desktop's own callApp launch function."""

import argparse
import json
import socket
import time

import desktop_wake_state as state


SOCKET_PATH = "/var/run/rk3588-systemui-wake.sock"
PROTOCOL = "rk3588-desktop-wake/v1"
APP = "rk3588-ai"


def request_factory(action, path=SOCKET_PATH, timeout=4.0):
    if action not in ("check", "launch"):
        raise ValueError("Unsupported factory launch request")
    message = json.dumps({"protocol": PROTOCOL, "app": APP, "action": action},
                         separators=(",", ":")).encode("utf-8") + b"\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(path)
        connection.sendall(message)
        pending = bytearray()
        while b"\n" not in pending:
            block = connection.recv(min(1024, 4097 - len(pending)))
            if not block:
                raise RuntimeError("原厂启动桥接未返回完整应答")
            pending.extend(block)
            if len(pending) > 4096:
                raise RuntimeError("原厂启动桥接应答过长")
    response = json.loads(pending.split(b"\n", 1)[0].decode("utf-8"))
    if (not isinstance(response, dict) or response.get("protocol") != PROTOCOL
            or response.get("app") != APP or response.get("action") != action):
        raise RuntimeError("原厂启动桥接应答不匹配")
    if response.get("ok") is not True:
        raise RuntimeError("原厂启动桥接拒绝请求：" + str(response.get("error", "unknown error")))
    return response


class FactoryAppHandoff:
    """Every wake goes through the manufacturer's cold/hot launch bookkeeping."""
    def __init__(self, stop, clock=None, requester=None):
        self.stop = stop
        self.clock = clock or time.monotonic
        self.requester = requester or request_factory

    def reap(self):
        # The factory, not the wake daemon, owns the application's QProcess.
        pass

    def open_app(self):
        self.requester("launch")
        print("DESKTOP_APP_LAUNCH_FACTORY：原厂桌面已登记并启动助手。", flush=True)

    def wait(self, token, timeout=45):
        deadline = self.clock() + timeout
        requested = False
        try:
            while not self.stop.is_set() and self.clock() < deadline:
                request = state.read_json(state.REQUEST_PATH)
                if request.get("token") != token:
                    return False
                if request.get("status") == "claimed":
                    print("DESKTOP_WAKE_HANDOFF_OK：应用已接管本次唤醒。", flush=True)
                    return True
                if request.get("status") != "pending":
                    return False
                if not requested:
                    self.open_app()
                    requested = True
                self.stop.wait(0.2)
            return False
        finally:
            state.fail_request(token, "原厂启动交接超时或后台已停止")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choices = parser.add_mutually_exclusive_group(required=True)
    choices.add_argument("--check", action="store_true")
    choices.add_argument("--launch", action="store_true")
    args = parser.parse_args()
    try:
        request_factory("check" if args.check else "launch")
        print("FACTORY_LAUNCH_READY" if args.check else "FACTORY_LAUNCH_REQUEST_OK")
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print("FACTORY_LAUNCH_FAILED：" + str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
