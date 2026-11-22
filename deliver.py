"""Deliver exactly one report archive to the configured personal Telegram chat."""
import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, build_opener
from uuid import uuid4

from sources import NoRedirect, SourceError, require


def api(token, method, data, content_type):
    req = Request("https://api.telegram.org/bot" + token + "/" + method, data=data,
                  headers={"Content-Type": content_type})
    with build_opener(NoRedirect()).open(req, timeout=90) as response:
        obj = json.load(response)
    require(obj.get("ok") is True, "전송 API 응답 오류")
    return obj["result"]


def send(folder, token, chat_id):
    require(token and re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token), "봇 설정 오류")
    require(chat_id and re.fullmatch(r"[1-9][0-9]*", chat_id), "개인 대화 ID 필요")
    chat = api(token, "getChat", urlencode({"chat_id": chat_id}).encode(),
               "application/x-www-form-urlencoded")
    require(chat.get("type") == "private" and str(chat.get("id")) == chat_id, "개인 대화 확인 실패")
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(folder))
    require(data.tell() < 49_000_000, "보고서 용량 초과")
    boundary = uuid4().hex
    day = json.loads((folder / "summary.json").read_text(encoding="utf-8"))["date"]
    require(re.fullmatch(r"[0-9]{8}", day), "기준일 오류")
    parts = []
    for name, value in (("chat_id", chat_id), ("protect_content", "true")):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="document"; '
                  f'filename="kr-report-{day}.zip"\r\nContent-Type: application/zip\r\n\r\n'.encode(),
                  data.getvalue(), f"\r\n--{boundary}--\r\n".encode()])
    message = api(token, "sendDocument", b"".join(parts), "multipart/form-data; boundary=" + boundary)
    require(str(message["chat"]["id"]) == chat_id and "document" in message, "전송 확인 실패")


if __name__ == "__main__":
    try:
        send(Path(sys.argv[1]), os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID"))
    except Exception:
        # Provider exceptions may contain a token-bearing URL. Never print the exception.
        sys.exit("비공개 보고서 전송 실패")
