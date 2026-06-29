import os
import json
import time
import sqlite3
import feedparser
import requests
from datetime import datetime, timedelta
from replicate import Client
from dotenv import load_dotenv
from vk_api import VkApi
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
from vk_api.utils import get_random_id

# ---------- НАСТРОЙКИ ----------
load_dotenv()
VK_TOKEN = os.getenv("VK_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN")

# Клиент Replicate
rep_client = Client(api_token=REPLICATE_API_TOKEN)

# База данных SQLite
DB_FILE = "neuro_hunter.db"
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
cursor = conn.cursor()

cursor.executescript("""
CREATE TABLE IF NOT EXISTS marketplaces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE,
    url TEXT,
    accessible INTEGER DEFAULT 0,
    payment_info TEXT,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scanned_models (
    model_id TEXT UNIQUE,
    platform TEXT,
    notified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
""")
conn.commit()

# ---------- ТЕКУЩИЙ СТЕК (ВСЕ ТВОИ МОДЕЛИ) ----------
MY_STACK = {
    "hyper-flux-8step": {
        "model": "bytedance/hyper-flux-8step",
        "price": 0.003,          # примерная цена в USD
        "category": "image"
    },
    "qwen-image-edit": {
        "model": "qwen/qwen-image-edit-2511",
        "price": 0.004,
        "category": "image_edit"
    },
    "nano-banana": {
        "model": "google/nano-banana",
        "price": 0.002,
        "category": "image"
    },
    "nano-banana-2": {
        "model": "google/nano-banana-2",
        "price": 0.0025,
        "category": "image"
    },
    "gpt-image-2": {
        "model": "openai/gpt-image-2",   # Уточни точный model_id на Replicate, если есть
        "price": 0.004,
        "category": "image"
    },
    "wan-i2v-fast": {
        "model": "wan-video/wan-2.2-i2v-fast",
        "price": 0.02,
        "category": "video"
    },
    "wan-t2v-fast": {
        "model": "wan-video/wan-2.2-t2v-fast",
        "price": 0.015,
        "category": "video"
    },
    "wan-animate-replace": {
        "model": "wan-video/wan-2.2-animate-replace",
        "price": 0.025,
        "category": "video"
    },
    "wan-s2v": {
        "model": "wan-video/wan-2.2-s2v",
        "price": 0.03,
        "category": "video"
    },
    "gpt4o-mini": {
        "model": "openai/gpt-4o-mini",
        "price": 0.00015,
        "category": "text"
    },
    "music-1.5": {
        "model": "minimax/music-1.5",
        "price": 0.01,
        "category": "audio"
    }
}
STACK_JSON = json.dumps(MY_STACK, indent=2, ensure_ascii=False)

# ---------- ОБНОВЛЕННЫЕ ИСТОЧНИКИ ДЛЯ РФ/КИТАЙ ОХОТЫ ----------
RSS_SOURCES = [
    "https://habr.com/ru/rss/hub/artificial_intelligence/all/", # Главный ИИ-хаб на Хабре
    "https://vc.ru/rss/crypto",                                 # Технологии и ИИ на VC
    "https://huggingface.co/blog/feed.xml",                     # Мировые опенсорс релизы
]
# Если RSS пуст, /hunt просто вернёт сообщение. Можешь добавить позже.

# ---------- VK БОТ LONG POLL ----------
vk_session = VkApi(token=VK_TOKEN)
vk = vk_session.get_api()
longpoll = VkBotLongPoll(vk_session, group_id=GROUP_ID)

def send_message(peer_id, text):
    try:
        vk.messages.send(
            peer_id=peer_id,
            message=text,
            random_id=get_random_id(),
            dont_parse_links=0
        )
    except Exception as e:
        print(f"Ошибка отправки: {e}")

# ---------- GPT-4O-MINI ОБЁРТКА ----------
def ask_gpt(system_prompt, user_prompt, max_tokens=800):
    try:
        output = rep_client.run(
            "openai/gpt-4o-mini",
            input={
                "prompt": user_prompt,
                "system_prompt": system_prompt,
                "max_tokens": max_tokens,
                "temperature": 0.2
            }
        )
        return "".join(output).strip()
    except Exception as e:
        print(f"GPT error: {e}")
        return None

# ---------- ОХОТА ЗА МАРКЕТПЛЕЙСАМИ ----------
def hunt_marketplaces():
    results = []
    system_prompt = (
        "Ты — скаут AI-платформ. Анализируй новости ИИ. Мы ищем новые облачные сервисы, "
        "китайские или российские маркетплейсы моделей, новые API-хабы (аналоги Replicate/OpenRouter), "
        "которые работают без VPN и предоставляют доступ к генерации фото, видео, тексту или музыке. "
        "Ответь СТРОГО в формате JSON без лишнего текста: "
        '{"is_marketplace": true/false, "name": "название сервиса", "url": "ссылка", "reason": "чем полезен и какие модели есть"}.'
    )
    for rss_url in RSS_SOURCES:
        try:
            feed = feedparser.parse(rss_url)
            for entry in feed.entries[:10]:
                title = entry.get("title", "")
                desc = entry.get("description", "")[:500]
                link = entry.get("link", "")
                answer = ask_gpt(system_prompt, f"Заголовок: {title}\nОписание: {desc}\nСсылка: {link}")
                if not answer:
                    continue
                try:
                    if "{" in answer:
                        json_str = answer[answer.find("{"):answer.rfind("}")+1]
                        info = json.loads(json_str)
                    else:
                        continue
                except:
                    continue

                if info.get("is_marketplace"):
                    name = info.get("name", "Безымянный")
                    url = info.get("url", link)
                    accessible = check_accessibility(url)
                    cursor.execute("SELECT id FROM marketplaces WHERE url=?", (url,))
                    if not cursor.fetchone():
                        cursor.execute(
                            "INSERT OR IGNORE INTO marketplaces (name, url, accessible) VALUES (?,?,?)",
                            (name, url, 1 if accessible else -1)
                        )
                        conn.commit()
                        results.append(f"🔍 {name}\n🔗 {url}\n📡 Доступ из РФ: {'Да' if accessible else 'Нет'}\n💳 Оплата: проверь вручную")
        except Exception as e:
            print(f"Ошибка RSS {rss_url}: {e}")
            continue
    if not results:
        return "Новых маркетплейсов не обнаружено. Добавь RSS источники или пришли ссылку вручную."
    return "🕵️ Найдены потенциальные маркетплейсы:\n\n" + "\n\n".join(results)

def check_accessibility(url):
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        return resp.status_code == 200
    except:
        return False

# ---------- СБОР МОДЕЛЕЙ ----------
def fetch_replicate_models(limit=20):
    # Используем базовый эндпоинт, который гарантированно отдаёт список моделей
    url = f"https://api.replicate.com/v1/models"
    headers = {"Authorization": f"Token {REPLICATE_API_TOKEN}"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        print(f"[DEBUG] Replicate status: {resp.status_code}")
        
        if resp.status_code != 200:
            print(f"[ERROR] Replicate API error: {resp.text}")
            return []
            
        data = resp.json()
        # API Replicate возвращает список в ключе 'results'
        if not isinstance(data, dict) or "results" not in data:
            print(f"[ERROR] Unexpected Replicate response: {data}")
            return []
            
        models = []
        # Берём первые N моделей из выдачи
        for m in data["results"][:limit]:
            owner = m.get("owner", "unknown")
            name = m.get("name", "unknown")
            desc = m.get("description", "") if m.get("description") else "Нет описания"
            desc = desc[:300]
            
            models.append({
                "id": f"{owner}/{name}",
                "platform": "replicate",
                "name": f"{owner}/{name}",
                "description": desc,
                "price_raw": "расчёт по факту работы",
                "url": f"https://replicate.com/{owner}/{name}",
                "created": m.get("created_at", "")
            })
        print(f"[DEBUG] Replicate models fetched successfully: {len(models)}")
        return models
    except Exception as e:
        print(f"[ERROR] Replicate fetch exception: {e}")
        return []
        
def fetch_modelscope_models(limit=20):
    url = "https://modelscope.cn/api/v1/models"
    params = {
        "PageSize": limit,
        "PageNumber": 1,
        "SortBy": "GmtModified",
        "Target": "inference"
    }
    try:
        resp = requests.get(url, params=params, timeout=10).json()
        models = []
        for item in resp.get("Data", {}).get("Models", []):
            name = item["ModelName"]
            m_id = item["ModelId"]
            desc = item.get("Description", "")[:300]
            models.append({
                "id": m_id,
                "platform": "modelscope",
                "name": name,
                "description": desc,
                "price_raw": "смотри на странице",
                "url": f"https://modelscope.cn/models/{m_id}",
                "created": item.get("GmtModified", "")
            })
        return models
    except Exception as e:
        print(f"ModelScope fetch error: {e}")
        return []

def fetch_siliconflow_models(limit=20):
    url = "https://api.siliconflow.cn/v1/models"
    sf_token = os.getenv("SILICONFLOW_API_TOKEN")
    
    if not sf_token:
        print("[ERROR] SILICONFLOW_API_TOKEN не найден в .env")
        return []
        
    headers = {"Authorization": f"Bearer {sf_token}"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        print(f"[DEBUG] SiliconFlow status: {resp.status_code}")
        
        if resp.status_code != 200:
            print(f"[ERROR] SiliconFlow API error: {resp.text}")
            return []
            
        data = resp.json()
        if not isinstance(data, dict) or "data" not in data:
            print(f"[ERROR] Unexpected SiliconFlow response: {data}")
            return []
            
        models = []
        for m in data["data"][:limit]:
            m_id = m.get("id", "unknown")
            models.append({
                "id": m_id,
                "platform": "siliconflow",
                "name": m_id,
                "description": f"Модель на китайском API-хабе. Тип: {m.get('object', 'model')}",
                "price_raw": "Цены в юанях/долларах (демпинг)",
                "url": "https://www.siliconflow.com/models",
                "created": ""
            })
        print(f"[DEBUG] SiliconFlow models fetched successfully: {len(models)}")
        return models
    except Exception as e:
        print(f"[ERROR] SiliconFlow fetch exception: {e}")
        return []

# ---------- СРАВНЕНИЕ МОДЕЛЕЙ ----------
def compare_models(models, peer_id):
def compare_models(models, peer_id):
    found = 0
    for model in models:
        cursor.execute("SELECT model_id FROM scanned_models WHERE model_id=?", (model["id"],))
        if cursor.fetchone():
            continue

        system_prompt = f"""Ты — AI-аналитик. У нас есть следующий стек моделей и их примерные цены (USD):
{STACK_JSON}

Проанализируй новую модель. Если она **дешевле** при сопоставимом качестве или **значительно лучше** при сравнимой цене, дай краткую рекомендацию заменить (2-3 предложения) с ценой и ссылкой.
Если модель — новая версия одной из наших (например, nano-banana-3 или GPT-IMAGE-3), обязательно скажи об этом.
Если цена не указана, предположи её из описания.
Если модель не подходит, ответь строго "ИГНОР".
Формат ответа (если не игнор): <текст рекомендации>"""

        user_prompt = f"Модель: {model['name']} ({model['platform']})\nОписание: {model['description']}\nЦена: {model['price_raw']}\nСсылка: {model['url']}"

        response = ask_gpt(system_prompt, user_prompt, max_tokens=500)
        if not response:
            continue

        if "ИГНОР" not in response:
            message = f"🔥 Новая модель достойна замены!\n{model['name']}\n{response}\n🔗 {model['url']}"
            send_message(peer_id, message)
            cursor.execute("INSERT OR IGNORE INTO scanned_models (model_id, platform) VALUES (?,?)",
                           (model["id"], model["platform"]))
            conn.commit()
            found += 1
            time.sleep(1)
    return found

# ---------- ОБРАБОТКА КОМАНД ----------
def handle_command(peer_id, text):
    text = text.strip().lower()
    if text.startswith("/hunt"):
        send_message(peer_id, "🔎 Запускаю охоту за маркетплейсами...")
        result = hunt_marketplaces()
        send_message(peer_id, result)

    elif text.startswith("/scan"):
        parts = text.split()
        platform = parts[1] if len(parts) > 1 else "replicate"
        send_message(peer_id, f"🔍 Сканирую {platform} и сравниваю со стеком...")
        if platform == "replicate":
            models = fetch_replicate_models()
        elif platform == "modelscope":
            models = fetch_modelscope_models()
        elif platform == "siliconflow":  # <-- Добавили проверку на Китай
            models = fetch_siliconflow_models()
        else:
            models = []
            send_message(peer_id, "Платформа не поддерживается или не найдена.")
        if models:
            count = compare_models(models, peer_id)
            if count == 0:
                send_message(peer_id, "Ничего подходящего для замены не найдено.")
            else:
                send_message(peer_id, f"✅ Обработано, предложено замен: {count}")
        else:
            send_message(peer_id, "Не удалось получить модели.")

    elif text.startswith("/list"):
        cursor.execute("SELECT name, url, accessible FROM marketplaces ORDER BY added_at DESC")
        rows = cursor.fetchall()
        if rows:
            msg = "📋 Найденные маркетплейсы:\n"
            for name, url, acc in rows:
                status = "🟢 Доступен" if acc == 1 else ("🔴 Недоступен" if acc == -1 else "❓ Не проверен")
                msg += f"{name}: {url} ({status})\n"
            send_message(peer_id, msg)
        else:
            send_message(peer_id, "Список пуст. Запусти /hunt.")

    elif text.startswith("/help"):
        help_text = (
            "Команды:\n"
            "/hunt — искать новые маркетплейсы AI\n"
            "/scan replicate — сравнить модели Replicate с твоим стеком\n"
            "/scan modelscope — то же для ModelScope\n"
            "/list — показать найденные маркетплейсы\n"
            "/help — это сообщение"
        )
        send_message(peer_id, help_text)

# ---------- ГЛАВНЫЙ ЦИКЛ ----------
def main():
    print("Бот запущен. Жду команд в группе...")
    for event in longpoll.listen():
        if event.type == VkBotEventType.MESSAGE_NEW:
            msg_obj = event.obj.message
            text = msg_obj.get('text', '')
            peer_id = msg_obj.get('peer_id')
            if text:
                try:
                    handle_command(peer_id, text)
                except Exception as e:
                    print(f"Ошибка: {e}")
                    send_message(peer_id, f"⚠️ Ошибка: {e}")

if __name__ == "__main__":
    main()
