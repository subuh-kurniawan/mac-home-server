#!/usr/bin/env python3
"""
Google Assistant SDK (Embedded Assistant v1alpha2 gRPC) Text Command Client.
Mengirim perintah teks langsung ke akun Google Assistant Anda (tanpa mikrofon!),
sehingga bisa mengendalikan lampu/smart home, rutinitas Google Home, cuaca, dll.
"""

import json
import os
import subprocess
import sys
import time
import uuid

os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CLIENT_SECRET_FILE = os.path.join(BASE_DIR, "google_client_secret.json")
TOKEN_FILE = os.path.join(BASE_DIR, "google_assistant_token.json")
DEVICE_CONFIG_FILE = os.path.join(BASE_DIR, "google_assistant_device.json")

ASSISTANT_API_ENDPOINT = "embeddedassistant.googleapis.com"
SCOPES = ["https://www.googleapis.com/auth/assistant-sdk-prototype"]


def is_sdk_installed():
    try:
        import grpc  # noqa: F401
        import google.auth  # noqa: F401
        from google.assistant.embedded.v1alpha2 import embedded_assistant_pb2  # noqa: F401
        return True
    except Exception:
        return False


def _load_or_create_device_ids():
    if os.path.isfile(DEVICE_CONFIG_FILE):
        try:
            with open(DEVICE_CONFIG_FILE, "r") as f:
                d = json.load(f)
                if d.get("device_model_id") and d.get("device_id"):
                    return d["device_model_id"], d["device_id"]
        except Exception:
            pass

    project_id = "mac-remote-assistant"
    if os.path.isfile(CLIENT_SECRET_FILE):
        try:
            with open(CLIENT_SECRET_FILE, "r") as f:
                cs = json.load(f)
                inner = cs.get("installed") or cs.get("web") or {}
                if inner.get("project_id"):
                    project_id = inner["project_id"]
        except Exception:
            pass

    model_id = f"{project_id}-mac-remote-model"
    device_id = f"mac-remote-{uuid.uuid4().hex[:8]}"
    with open(DEVICE_CONFIG_FILE, "w") as f:
        json.dump({"device_model_id": model_id, "device_id": device_id, "project_id": project_id}, f, indent=2)
    return model_id, device_id


def get_status():
    return {
        "sdk_installed": is_sdk_installed(),
        "has_client_secret": os.path.isfile(CLIENT_SECRET_FILE),
        "is_authorized": os.path.isfile(TOKEN_FILE),
        "client_secret_path": CLIENT_SECRET_FILE,
        "token_path": TOKEN_FILE,
    }


def _load_credentials():
    if not os.path.isfile(TOKEN_FILE):
        return None
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    with open(TOKEN_FILE, "r") as f:
        info = json.load(f)
    creds = Credentials.from_authorized_user_info(info, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return creds


def authorize_interactive(port=8765):
    """Menjalankan 1x login OAuth Google di browser Mac untuk menyimpan token permanen."""
    if not os.path.isfile(CLIENT_SECRET_FILE):
        return {"status": "error", "message": f"File {CLIENT_SECRET_FILE} belum ada."}
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
    creds = flow.run_local_server(port=port, open_browser=True)
    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())
    return {"status": "success", "message": "✅ Otorisasi Google Assistant berhasil disimpan!"}


def send_text_command(text_query, language_code="", save_audio=True):
    """
    Mengirim perintah teks langsung ke Google Assistant SDK (gRPC).
    Contoh: 'nyalakan lampu ruang tamu', 'cuaca hari ini', 'broadcast makan malam siap'.
    """
    query = str(text_query or "").strip()
    if not query:
        return {"status": "error", "message": "Perintah teks tidak boleh kosong."}
    if not is_sdk_installed():
        return {"status": "error", "message": "Pustaka Google Assistant SDK belum terpasang."}
    if not os.path.isfile(TOKEN_FILE):
        return {
            "status": "need_auth",
            "message": "Google Assistant belum diotorisasi. Letakkan google_client_secret.json lalu jalankan 1x login OAuth.",
        }

    try:
        import grpc
        import google.auth.transport.grpc
        import google.auth.transport.requests
        from google.assistant.embedded.v1alpha2 import (
            embedded_assistant_pb2,
            embedded_assistant_pb2_grpc,
        )

        creds = _load_credentials()
        if not creds:
            return {"status": "need_auth", "message": "Token Google Assistant tidak valid. Lakukan login OAuth ulang."}

        http_request = google.auth.transport.requests.Request()
        channel = google.auth.transport.grpc.secure_authorized_channel(
            creds, http_request, ASSISTANT_API_ENDPOINT
        )
        stub = embedded_assistant_pb2_grpc.EmbeddedAssistantStub(channel)

        model_id, device_id = _load_or_create_device_ids()
        config = embedded_assistant_pb2.AssistConfig(
            audio_out_config=embedded_assistant_pb2.AudioOutConfig(
                encoding="MP3",
                sample_rate_hertz=16000,
                volume_percentage=100,
            ),
            dialog_state_in=embedded_assistant_pb2.DialogStateIn(
                language_code=language_code,
                is_new_conversation=True,
            ),
            device_config=embedded_assistant_pb2.DeviceConfig(
                device_id=device_id,
                device_model_id=model_id,
            ),
            text_query=query,
        )

        def _req_iter():
            yield embedded_assistant_pb2.AssistRequest(config=config)

        display_text = ""
        audio_chunks = bytearray()
        for resp in stub.Assist(_req_iter(), timeout=20):
            if resp.dialog_state_out.supplemental_display_text:
                display_text = resp.dialog_state_out.supplemental_display_text
            if resp.audio_out.audio_data:
                audio_chunks.extend(resp.audio_out.audio_data)

        channel.close()

        audio_path = None
        if save_audio and len(audio_chunks) > 256:
            audio_path = f"/tmp/ga_reply_{int(time.time() * 1000)}.mp3"
            with open(audio_path, "wb") as f:
                f.write(audio_chunks)

        return {
            "status": "success",
            "query": query,
            "reply": display_text or f'Perintah "{query}" berhasil dikirim ke Google Assistant!',
            "audio_path": audio_path,
        }
    except Exception as e:
        return {"status": "error", "message": f"Gagal kirim ke Google Assistant: {str(e)}"}


def send_text_command_safe(text_query, language_code=""):
    """Menjalankan send_text_command di subprocess terisolasi agar protobuf pure-python tidak bentrok dengan pychromecast."""
    if not os.path.isfile(TOKEN_FILE):
        return {
            "status": "need_auth",
            "message": "Google Assistant belum diotorisasi. Letakkan google_client_secret.json lalu jalankan login OAuth 1x.",
        }
    env = dict(os.environ)
    env["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
    try:
        proc = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--json", language_code, str(text_query)],
            capture_output=True, text=True, timeout=25, env=env,
        )
        out = (proc.stdout or "").strip()
        if out.startswith("{"):
            return json.loads(out)
        return {"status": "error", "message": (proc.stderr or out or "Gagal memanggil Google Assistant")[:300]}
    except Exception as e:
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "auth":
        print(authorize_interactive())
    elif len(sys.argv) > 3 and sys.argv[1] == "--json":
        print(json.dumps(send_text_command(sys.argv[3], language_code=sys.argv[2])))
    elif len(sys.argv) > 1:
        print(send_text_command(" ".join(sys.argv[1:])))
    else:
        print(json.dumps(get_status(), indent=2))
