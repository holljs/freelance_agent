import os
import json
import time
import sqlite3
import random  # Добавлено для динамических пауз
import requests
from dotenv import load_dotenv
from vk_api import VkApi
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
from vk_api.utils import get_random_id

# ---------- НАСТРОЙКИ ----------
load_dotenv()
VK_TOKEN = os.getenv("VK_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))
SILICONFLOW_API_TOKEN = os.getenv("SILICONFLOW_API_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")  # Крайне критично для Code Search API

# ---------- VK БОТ LONG POLL ----------
vk_session = VkApi(token=VK_TOKEN)
vk = vk_session.get_api()
longpoll = VkBotLongPoll(vk_session, group_id=GROUP_ID)

# База данных SQLite — отслеживание конкретных файлов, чтобы избежать дублей
DB_FILE = "neuro_hunter.db"
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
cursor = conn.cursor()

# Создаем таблицу для точечного кэширования файлов
cursor.executescript("""
CREATE TABLE IF NOT EXISTS scanned_files (
    file_url TEXT UNIQUE,
    file_name TEXT,
    repo_name TEXT,
    found_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

# ---------- ИИ-ФИЛЬТР НА DEEPSEEK-V3 ----------
def ask_deepseek_sync(system_prompt, user_prompt):
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
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"].strip()
        else:
            print(f"⚠️ SiliconFlow ошибка: {resp.status_code} - {resp.text}")
            return None
    except Exception as e:
        print(f"❌ Ошибка вызова DeepSeek: {e}")
        return None

# ---------- СВЕРХЭФФЕКТИВНЫЙ ПОИСК ПО КОДУ GITHUB ----------
def fetch_github_olympiad_files():
    """
    Сканирует внутренности GitHub (Code Search).
    Запросы оптимизированы через оператор OR для экономии лимитов API.
    """
    url = "https://api.github.com/search/code"
    
    # Полный список целевых олимпиад и тем ВПР
    search_keywords = [
        "vpr json", "впр математика 4 класс", "впр русский язык", "задания впр",
        "олимпиада кенгуру", "русский медвежонок олимпиада", "олимпиада кит",
        "конкурс чип человек и природа", "олимпис математика", "литенок",
        "олимпиада сириус", "всош школьный этап", "всош муниципальный этап",
        "высшая проба задания", "олимпиада курчатов", "questions json", "tasks csv"
    ]
    
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    else:
        print("⚠️ Ошибка: GITHUB_TOKEN не задан! Поиск по коду заблокирован без авторизации.")
        return []

    discovered_files = []
    seen_urls = set()
    
    print(f"🕵️‍♂️ Охотник начинает обход. Тем к проверке: {len(search_keywords)}")
    
    for keyword in search_keywords:
        # Упаковываем все расширения в один запрос через OR. 
        # Вместо 3 запросов к GitHub API тратится всего 1! Лимиты будут жить.
        query = f"{keyword} (extension:json OR extension:csv OR extension:sql)"
        params = {"q": query, "per_page": 15}
        
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=12)
            
            # Ловим жёсткий лимит Code Search API (у GitHub это 10 запросов в минуту)
            if resp.status_code == 403:
                print("⏳ Достигнут лимит GitHub API. Засыпаем на 60 секунд для полного сброса...")
                time.sleep(60)
                # Повторяем запрос после сна
                resp = requests.get(url, headers=headers, params=params, timeout=12)
                
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", [])
                print(f"🔎 Ключевик '{keyword}' выдал результатов: {len(items)}")
                
                for item in items:
                    file_url = item.get("html_url")
                    repo_info = item.get("repository", {})
                    repo_name = repo_info.get("full_name", "unknown")
                    
                    # Отсекаем мусор, папки с домашками и студенческие песочницы
                    path_lower = file_url.lower()
                    if any(x in path_lower for x in ["homework", "lab1", "lab2", "test_project", "sandbox", "learning"]):
                        continue
                        
                    if file_url not in seen_urls:
                        seen_urls.add(file_url)
                        discovered_files.append({
                            "file_name": item.get("name", "unknown"),
                            "path": item.get("path", ""),
                            "repo_name": repo_name,
                            "url": file_url
                        })
            else:
                print(f"⚠️ Ошибка GitHub API ({resp.status_code}) на ключевом слове '{keyword}'")
            
            # Рандомная задержка (Jitter) от 4 до 7 сек, чтобы эмулировать поведение человека
            time.sleep(random.uniform(4.0, 7.0))
            
        except Exception as e:
            print(f"💥 Ошибка выполнения поискового запроса '{query}': {e}")
            time.sleep(5)
                
    return discovered_files

# ---------- АНАЛИЗ НАЙДЕННЫХ ФАЙЛОВ И ОТПРАВКА В ВК ----------
def process_hunting(peer_id):
    send_message(peer_id, "🏹 Начинаю глубокое сканирование внутренностей GitHub по коду, файлам JSON/CSV и олимпиадам (Кенгуру, Сириус, Медвежонок)...")
    
    files = fetch_github_olympiad_files()
    
    if not files:
        send_message(peer_id, "⚠️ Не удалось найти новые файлы или исчерпаны лимиты запросов к GitHub. Попробуй позже.")
        return

    system_prompt = (
        "Ты — эксперт по анализу учебных баз данных. Проанализируй имя файла, его путь и репозиторий. "
        "Мы ищем готовые, структурированные файлы вопросов, ответов, тестов ВПР или олимпиад (Сириус, ВсОШ, Кенгуру, Медвежонок, ЧИП, Кит). "
        "Если этот файл действительно является базой данных с заданиями, напиши кратко (2 предложения): "
        "какой предмет/олимпиада и для каких классов там содержатся данные. "
        "Если это просто конфигурационный файл системы, манифест, лог или студенческий мусор — ответь строго одним словом: ИГНОР."
    )
    
    found_count = 0
    for file_item in files:
        # Исключаем дубликаты по базе данных
        cursor.execute("SELECT file_url FROM scanned_files WHERE file_url=?", (file_item["url"],))
        if cursor.fetchone():
            continue 

        user_prompt = f"Файл: {file_item['file_name']}\nПуть в проекте: {file_item['path']}\nРепозиторий: {file_item['repo_name']}\nСсылка: {file_item['url']}"
        response = ask_deepseek_sync(system_prompt, user_prompt)
        
        if not response or "ИГНОР" in response:
            continue

        # Красивое уведомление о сливе в ВК
        message = (
            f"🎯 НАЙДЕНА СТРУКТУРИРОВАННАЯ БАЗА ЗАДАНИЙ!\n\n"
            f"📁 Файл: {file_item['file_name']}\n"
            f"📦 Репозиторий: {file_item['repo_name']}\n"
            f"🔍 Анализ ИИ:\n{response}\n\n"
            f"🔗 Прямая ссылка на файл: {file_item['url']}"
        )
        send_message(peer_id, message)
        
        # Сохраняем в кэш
        cursor.execute("INSERT OR IGNORE INTO scanned_files (file_url, file_name, repo_name) VALUES (?,?,?)", 
                       (file_url := file_item["url"], file_item["file_name"], file_item["repo_name"]))
        conn.commit()
        
        found_count += 1
        time.sleep(2)  # Безопасный интервал для ВК API

    if found_count == 0:
        send_message(peer_id, "📭 Новых структурированных файлов ВПР или олимпиад на GitHub пока не обнаружено. Продолжаю следить!")
    else:
        send_message(peer_id, f"✅ Глубокая охота завершена! Найдено и отправлено новых баз: {found_count}")

# ---------- ОБРАБОТКА КОМАНД ----------
def handle_command(peer_id, text):
    text = text.strip().lower()
    
    if text.startswith("/scan"):
        process_hunting(peer_id)

    elif text.startswith("/help"):
        help_text = (
            "🤖 Бот-Охотник за базами ВПР и Олимпиад (Продвинутая Code Search версия):\n\n"
            "/scan — запустить точечный ИИ-поиск по файлам .json/.csv/.sql внутри репозиториев GitHub 🎯\n"
            "/help — показать эту справку"
        )
        send_message(peer_id, help_text)

# ---------- ГЛАВНЫЙ ЦИКЛ ----------
def main():
    print("Бот-Охотник (Глубокий поиск файлов через DeepSeek) успешно запущен...")
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
