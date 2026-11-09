#!/bin/sh

# Keep the factory QProcess alive until the native window has closed.
# Never stop SystemUI and never launch this entry with nohup/backgrounding.
exec /bin/sh /userdata/voice-assistant/rkllama-qml/run_qml_assistant.sh --from-systemui "$@"
