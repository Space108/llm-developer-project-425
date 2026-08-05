import json
import os
import sys
import urllib.error
import urllib.request

token = os.environ["YC_IAM_TOKEN"]
folder = os.environ["YC_FOLDER_ID"]
question = sys.argv[1] if len(sys.argv) > 1 else "как оформить командировку?"

payload = {
    "model": f"gpt://{folder}/yandexgpt/latest",
    "instructions": (
        "Ты Help Desk агент. Перед ответом ВСЕГДА вызывай tool file_search. "
        "Если нашёл ответ — кратко + название документа. "
        "Если нет — честно скажи «не знаю» и предложи создать тикет."
    ),
    "input": question,
    "tools": [
        {
            "type": "file_search",
            "vector_store_ids": ["fvthf4vtg4l1e1vmca9g"],
        }
    ],
    "tool_choice": {"type": "file_search"},
}
req = urllib.request.Request(
    "https://rest-assistant.api.cloud.yandex.net/v1/responses",
    data=json.dumps(payload).encode(),
    headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "x-folder-id": folder,
    },
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read().decode())
    print("status", data.get("status"))
    types = [i.get("type") for i in (data.get("output") or [])]
    print("output_types", types)
    for item in data.get("output") or []:
        if item.get("type") == "file_search_call":
            print("file_search_queries", item.get("queries"))
        if item.get("type") == "message":
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("text"):
                    print("ANSWER:", part["text"])
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read().decode(errors="replace")[:2000])
except Exception as e:
    print("ERR", e)
