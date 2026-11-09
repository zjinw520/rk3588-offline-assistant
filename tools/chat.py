"""Minimal streaming CLI for this project's local text API (Python standard library)."""
import argparse
import json
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def chat(base, model, question, output, timeout=300):
    payload = {"model": model, "messages": [{"role": "user", "content": question}], "stream": True}
    request = Request(base.rstrip("/") + "/api/chat",
                      data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        for line in response:
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get("error"):
                raise RuntimeError(event["error"])
            text = event.get("message", {}).get("content", "")
            if text:
                output.write(text)
                output.flush()
            if event.get("done") is True:
                output.write("\n")
                output.flush()
                return event
    raise RuntimeError("Stream ended without a successful done event")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:18080")
    parser.add_argument("--model")
    parser.add_argument("--question")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if not args.list and (not args.model or not args.question):
        parser.error("use --list, or both --model and --question")
    try:
        if args.list:
            with urlopen(args.url.rstrip("/") + "/api/tags", timeout=10) as response:
                print(json.dumps(json.load(response), ensure_ascii=False, indent=2))
        else:
            chat(args.url, args.model, args.question, sys.stdout)
    except HTTPError as error:
        parser.exit(1, "HTTP {}: {}\n".format(error.code, error.read(4096).decode("utf-8", "replace")))
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, "CHAT_FAILED: " + str(error) + "\n")


if __name__ == "__main__":
    main()
