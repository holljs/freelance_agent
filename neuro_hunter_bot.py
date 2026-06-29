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

# База данных SQLite для хранения найденных маркетплейсов и обработанных моделей
DB_FILE = "neuro_hunter.db"
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
cursor = conn.cursor()

# Создаём таблицы, если их нет
cursor.executescript("""
CREATE TABLE IF NOT EXISTS marketplaces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE,
    url TEXT,
    accessible INTEGER DEFAULT 0,  -- 0=неизвестно, 1=да, -1=нет
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

# ---------- ТЕКУЩИЙ СТЕК (эталон) ----------
MY_STACK = {
    "image": {
        "model": "bytedance/hyper-flux-8step",
        "price": 0.003,  # примерная цена в USD за генерацию
        "category": "image"
    },
    "text": {
        "model": "openai/gpt-4o-mini",
        "price": 0.00015,  # за 1K токенов (условно)
        "category": "text"
    },
    "music": {
        "model": "minimax/music-1.5",
        "price": 0.01,
        "category": "audio"
    },
    "video": {
        "model": "wan-video/wan-2.2-i2v-fast",
        "price": 0.02,
        "category": "video"
    }
}
# Для GPT-анализа превращаем в читаемый JSON
STACK_JSON = json.dumps(MY_STACK, indent=2, ensure_ascii=False)

# ---------- ИСТОЧНИКИ RSS ДЛЯ ОХОТЫ ----------
RSS_SOURCES = [
    "https://rss.app/feeds/tqUoZ3bIQj1g6Uzo.xml",  # Product Hunt AI (пример, лучше подставить свой)
    "https://hnrss.org/newest?q=AI+model+hosting+marketplace",
    "https://github.com/trending/python?since=daily",  # опционально
]
# Для быстрого старта можно оставить пустым, тогда hunt будет работать только по явному запросу

# ---------- VK БОТ НА LONG POLL ----------
vk_session = VkApi(token=VK_TOKEN)
vk = vk_session.get_api()
longpoll = VkBotLongPoll(vk_session, group_id=GROUP_ID)

def send_message(peer_id, text):
    """Отправка сообщения в ВК"""
    try:
        vk.messages.send(
            peer_id=peer_id,
            message=text,
            random_id=get_random_id(),
            dont_parse_links=0
        )
    except Exception as e:
        print(f"Ошибка отправки: {e}")

# ---------- ФУНКЦИИ ДЛЯ GPT-4O-MINI ----------
def ask_gpt(system_prompt, user_prompt, max_tokens=800):
    """Обёртка вызова Replicate GPT-4o-mini"""
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
    """Сканирует RSS, просит GPT найти упоминания новых AI-маркетплейсов, проверяет доступность."""
    results = []
    system_prompt = (
        "Ты — скаут AI-платформ. Получаешь заголовок и описание новости. "
        "Определи, является ли она анонсом новой облачной платформы/маркетплейса, "
        "позволяющей запускать ИИ-модели через API (как Replicate). "
        "Ответь СТРОГО в JSON: {\"is_marketplace\": true/false, \"name\": \"название\", \"url\": \"ссылка\", \"reason\": \"...\"}. "
        "Учитывай, что платформа должна предоставлять serverless inference для чужих моделей."
    )
    for rss_url in RSS_SOURCES:
        try:
            feed = feedparser.parse(rss_url)
            for entry in feed.entries[:10]:  # анализируем 10 свежих новостей
                title = entry.get("title", "")
                desc = entry.get("description", "")[:500]
                link = entry.get("link", "")
                # Спрашиваем GPT
                answer = ask_gpt(system_prompt, f"Заголовок: {title}\nОписание: {desc}\nСсылка: {link}")
                if not answer:
                    continue
                try:
                    # Ожидаем JSON, иногда GPT отвечает с обрамлением
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
                    # Проверяем доступность
                    accessible = check_accessibility(url)
                    # Сохраняем в БД, если нет
                    cursor.execute("SELECT id FROM marketplaces WHERE url=?", (url,))
                    if not cursor.fetchone():
                        cursor.execute(
                            "INSERT OR IGNORE INTO marketplaces (name, url, accessible) VALUES (?,?,?)",
                            (name, url, 1 if accessible else -1)
                        )
                        conn.commit()
                        results.append(f"🔍 {name}\n🔗 {url}\n📡 Доступ из РФ: {'Да' if accessible else 'Нет'}\n💳 Оплата: проверь вручную")
        except Exception as e:
            print(f"Ошибка обработки RSS {rss_url}: {e}")
            continue
    if not results:
        return "Новых маркетплейсов не обнаружено. Можешь добавить RSS источники в настройки бота."
    return "🕵️ Найдены потенциальные маркетплейсы:\n\n" + "\n\n".join(results)

def check_accessibility(url):
    """Простая проверка, что сайт открывается (без VPN)."""
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        return resp.status_code == 200
    except:
        return False

# ---------- СБОР МОДЕЛЕЙ С ПЛАТФОРМ ----------
def fetch_replicate_models(limit=20):
    """Получает последние модели с Replicate."""
    url = "https://api.replicate.com/v1/models?sort=created&order=desc&limit=" + str(limit)
    headers = {"Authorization": f"Token {REPLICATE_API_TOKEN}"}
    resp = requests.get(url, headers=headers).json()
    models = []
    for m in resp.get("results", []):
        owner = m["owner"]
        name = m["name"]
        desc = m.get("description", "")[:300]
        price_raw = "не указана"
        # Иногда цена есть в description, попробуем выудить
        models.append({
            "id": f"{owner}/{name}",
            "platform": "replicate",
            "name": f"{owner}/{name}",
            "description": desc,
            "price_raw": price_raw,
            "url": f"https://replicate.com/{owner}/{name}",
            "created": m.get("created_at", "")
        })
    return models

def fetch_modelscope_models(limit=20):
    """ModelScope (Китай) – публичный API списка моделей."""
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

# ---------- СРАВНЕНИЕ МОДЕЛЕЙ ----------
def compare_models(models, peer_id):
    """Прогоняет каждую модель через GPT, сравнивая с текущим стеком."""
    found = 0
    for model in models:
        # Пропускаем, если уже уведомляли (защита от повторов)
        cursor.execute("SELECT model_id FROM scanned_models WHERE model_id=?", (model["id"],))
        if cursor.fetchone():
            continue

        system_prompt = f"""Ты — AI-аналитик. У нас есть текущий стек моделей и их цены:
{STACK_JSON}

Проанализируй новую модель. Если она **дешевле** при сопоставимом качестве, или **значительно лучше** (по описанию) при сравнимой цене, дай краткую рекомендацию заменить, с указанием цены и ссылки. 
Если цена не указана, предположи её из описания (обычно пишут "$X.XXXX per image"). 
Если модель не подходит или дороже/хуже, ответь СТРОГО "ИГНОР". 
Формат ответа (если не игнор): <текст рекомендации>"""
        
        user_prompt = f"Модель: {model['name']} ({model['platform']})\nОписание: {model['description']}\nЦена: {model['price_raw']}\nСсылка: {model['url']}"

        response = ask_gpt(system_prompt, user_prompt, max_tokens=500)
        if not response:
            continue

        if "ИГНОР" not in response:
            message = f"🔥 Новая модель достойна замены!\n{model['name']}\n{response}\n🔗 {model['url']}"
            send_message(peer_id, message)
            # Помечаем как обработанную
            cursor.execute("INSERT OR IGNORE INTO scanned_models (model_id, platform) VALUES (?,?)",
                           (model["id"], model["platform"]))
            conn.commit()
            found += 1
            time.sleep(1)  # небольшая пауза
    return found

# ---------- ОБРАБОТЧИК КОМАНД ----------
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
        else:
            # Можно добавить вызов custom из найденных маркетплейсов
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
            peer_id = msg_obj.get('peer_id')  # работа с беседами и личкой
            if text:
                try:
                    handle_command(peer_id, text)
                except Exception as e:
                    print(f"Ошибка обработки: {e}")
                    send_message(peer_id, f"⚠️ Произошла ошибка: {e}")

if __name__ == "__main__":
    main()
