import os
import json
import time
import sqlite3
import requests
import httpx
import asyncio
from dotenv import load_dotenv
from vk_api import VkApi
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
from vk_api.utils import get_random_id

# ---------- НАСТРОЙКИ ----------
load_dotenv()
VK_TOKEN = os.getenv("VK_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))
SILICONFLOW_API_TOKEN = os.getenv("SILICONFLOW_API_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN") # Для обхода лимитов GitHub API

# ---------- VK БОТ LONG POLL ----------
vk_session = VkApi(token=VK_TOKEN)
vk = vk_session.get_api()
longpoll = VkBotLongPoll(vk_session, group_id=GROUP_ID)

# База данных SQLite только для репозиториев
DB_FILE = "neuro_hunter.db"
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
cursor = conn.cursor()

cursor.executescript("""
CREATE TABLE IF NOT EXISTS scanned_repos (
    repo_id TEXT UNIQUE,
    repo_name TEXT,
    url TEXT,
    notified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
""")
conn.commit()

def send_message(peer_id, text):
    try:
        vk.messages.send(
            peer_id=peer_id,
            message=text,
            random_id=get_random_id(),
            dont_parse_links=0
        )
    except Exception as e:
        print(f"Ошибка отправки сообщения в ВК: {e}")

# ---------- СВЕРХДЕШЕВЫЙ И УМНЫЙ ФИЛЬТР НА DEEPSEEK-V3 ----------
def ask_deepseek_sync(system_prompt, user_prompt):
    """Синхронная обертка для вызова DeepSeek (так как LongPoll работает в синхронном цикле)"""
    if not SILICONFLOW_API_TOKEN:
        print("❌ Ошибка: SILICONFLOW_API_TOKEN не настроен в .env")
        return None
        
    url = "https://api.siliconflow.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {SILICONFLOW_API_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "deepseek-ai/DeepSeek-V3",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": 400,
        "temperature": 0.1,
        "stream": False
    }
    
    try:
        # Используем стандартный requests для синхронного выполнения внутри handle_command
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"].strip()
        else:
            print(f"⚠️ SiliconFlow ошибка: {resp.status_code} - {resp.text}")
            return None
    except Exception as e:
        print(f"❌ Ошибка вызова DeepSeek: {e}")
        return None

# ---------- ПОИСК НА GITHUB ЗА БАЗАМИ ЗАДАНИЙ ----------
def fetch_github_olympiads(limit=40):
    url = "https://api.github.com/search/repositories"
    
    search_queries = [
        "vpr json", "впр математика", "впр русский", "задания впр",
        "olymp json", "olympiad sirius", "олимпиада сириус", "олимпиада курчатов",
        "олимпиада ломоносов", "высшая проба задания", "конкурс кенгуру", "конкурс чип",
        "всош задания", "vosh dataset"
    ]
    
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
        
    discovered_repos = []
    seen_urls = set()
    
    for q in search_queries:
        params = {"q": f"{q} in:name,description,readme", "sort": "stars", "order": "desc", "per_page": 15}
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("items", []):
                    repo_url = item.get("html_url")
                    if repo_url not in seen_urls:
                        seen_urls.add(repo_url)
                        discovered_repos.append({
                            "id": str(item.get("id")),
                            "name": item.get("full_name", "unknown"),
                            "description": item.get("description", "") if item.get("description") else "Нет описания",
                            "url": repo_url
                        })
            time.sleep(1) 
        except Exception as e:
            print(f"Ошибка поиска GitHub по запросу '{q}': {e}")
            
    return discovered_repos[:limit]

# ---------- АНАЛИЗ НАЙДЕННЫХ РЕПОЗИТОРИЕВ ----------
def process_hunting(peer_id):
    send_message(peer_id, "🏹 Начинаю глубокое сканирование GitHub в поисках ВПР и Олимпиад через DeepSeek-V3...")
    repos = fetch_github_olympiads()
    
    if not repos:
        send_message(peer_id, "⚠️ Не удалось получить данные с GitHub. Проверь сеть или GITHUB_TOKEN.")
        return

    system_prompt = (
        "Ты — эксперт по анализу учебных датасетов. Проанализируй имя и описание репозитория GitHub. "
        "Мы ищем готовые спарсенные базы данных, архивы задач конкурсов и олимпиад (Сириус, ВсОШ, Курчатов, ЧИП, Кенгуру) "
        "или тестов ВПР по классам. Форматы файлов должны быть JSON, CSV, SQL, XML, или репозиторий должен содержать скрипты-парсеры этих заданий. "
        "Если репозиторий действительно содержит структурированные учебные задания, напиши очень кратко (2-3 предложения): "
        "какие предметы, конкурсы или классы там найдены. "
        "Если это пустой студенческий репозиторий, лаба или мусор, не связанный с готовыми заданиями — ответь строго одним словом: ИГНОР."
    )
    
    found_count = 0
    for repo in repos:
        cursor.execute("SELECT repo_id FROM scanned_repos WHERE repo_id=?", (repo["id"],))
        if cursor.fetchone():
            continue 

        user_prompt = f"Репозиторий: {repo['name']}\nОписание: {repo['description']}\nСсылка: {repo['url']}"
        response = ask_deepseek_sync(system_prompt, user_prompt)
        
        if not response or "ИГНОР" in response:
            continue

        message = (
            f"🎯 НАЙДЕН СЛИВ ЗАДАНИЙ НА GITHUB!\n\n"
            f"📦 Репозиторий: {repo['name']}\n"
            f"📝 Анализ ИИ:\n{response}\n\n"
            f"🔗 Ссылка на исходники: {repo['url']}"
        )
        send_message(peer_id, message)
        
        cursor.execute("INSERT OR IGNORE INTO scanned_repos (repo_id, repo_name, url) VALUES (?,?,?)", 
                       (repo["id"], repo["name"], repo["url"]))
        conn.commit()
        found_count += 1
        time.sleep(1.5) 

    if found_count == 0:
        send_message(peer_id, "📭 Новых баз данных или парсеров на GitHub пока не появилось. Я продолжу мониторинг!")
    else:
        send_message(peer_id, f"✅ Охота завершена! Найдено и отправлено новых источников: {found_count}")

# ---------- ОБРАБОТКА КОМАНД ----------
def handle_command(peer_id, text):
    text = text.strip().lower()
    
    if text.startswith("/scan"):
        process_hunting(peer_id)

    elif text.startswith("/help"):
        help_text = (
            "🤖 Бот-Охотник за контентом ВПР и Олимпиад (Powered by DeepSeek-V3):\n\n"
            "/scan — запустить ИИ-сканирование GitHub на наличие баз заданий, файлов JSON/CSV и парсеров 🎯\n"
            "/help — показать эту справку"
        )
        send_message(peer_id, help_text)

# ---------- ГЛАВНЫЙ ЦИКЛ ----------
def main():
    print("Бот-Охотник (DeepSeek-версия) успешно запущен...")
    for event in longpoll.listen():
        if event.type == VkBotEventType.MESSAGE_NEW:
            msg_obj = event.obj.message
            text = msg_obj.get('text', '')
            peer_id = msg_obj.get('peer_id')
            if text:
                try: 
                    handle_command(peer_id, text)
                except Exception as e: 
                    send_message(peer_id, f"⚠️ Ошибка в обработке команды: {e}")

if __name__ == "__main__":
    main()
