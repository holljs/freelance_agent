import os
import feedparser
import requests
import time
from replicate import Client
from dotenv import load_dotenv
import vk_api # Подключаем библиотеку ВКонтакте
from vk_api.utils import get_random_id

# Загружаем данные из файла .env
load_dotenv()

# --- НАСТРОЙКИ ---
VK_TOKEN = os.getenv("VK_TOKEN")           # Ключ доступа сообщества ВК
VK_USER_ID = os.getenv("VK_USER_ID")       # Твой личный цифровой ID ВКонтакте
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN")

client = Client(api_token=REPLICATE_API_TOKEN)

# Авторизуем бота в ВК
vk_session = vk_api.VkApi(token=VK_TOKEN)
vk = vk_session.get_api()

# RSS-лента Хабр Фриланса
RSS_URLS = [
    "https://freelance.habr.com/tasks.rss?category_id=98",  # Боты
    "https://freelance.habr.com/tasks.rss?category_id=97"   # Скрипты
]

DB_FILE = "processed_tasks.txt"

def load_processed_tasks():
    try:
        with open(DB_FILE, "r") as f:
            return set(f.read().splitlines())
    except FileNotFoundError:
        return set()

def save_task(task_id):
    with open(DB_FILE, "a") as f:
        f.write(f"{task_id}\n")

# --- ОТПРАВКА В ВК ---
def send_to_vk(text):
    try:
        vk.messages.send(
            user_id=VK_USER_ID,
            message=text,
            random_id=get_random_id() # ВК требует уникальный ID для каждого сообщения
        )
    except Exception as e:
        print(f"Ошибка отправки сообщения в ВК: {e}")

def analyze_and_pitch(title, description, link):
    system_prompt = (
        "Ты — опытный разработчик ИИ-агентов и ботов. Твоя задача — проанализировать заказ на фрилансе. "
        "Если заказ НЕ связан с разработкой ботов, интеграцией нейросетей (API, ChatGPT, LangChain) или автоматизацией, ответь строго одним словом: ИГНОР. "
        "Если заказ подходит, напиши профессиональное, цепляющее сопроводительное письмо (питч) на русском языке. "
        "Пиши кратко, без воды, вежливо и продающе."
    )
    
    user_prompt = f"Заголовок: {title}\nОписание: {description}"
    
    output = client.run(
        "openai/gpt-4o-mini",
        input={
            "prompt": user_prompt,
            "system_prompt": system_prompt,
            "max_tokens": 1000
        }
    )
    response = "".join(output).strip()
    
    if "ИГНОР" in response:
        return None
    return response

def check_freelance():
    print("Проверяю биржу...")
    processed = load_processed_tasks()
    
    for url in RSS_URLS:
        feed = feedparser.parse(url)
        for entry in feed.entries:
            task_id = entry.link.split("/")[-1] 
            
            if task_id not in processed:
                title = entry.title
                description = entry.description
                link = entry.link
                
                pitch = analyze_and_pitch(title, description, link)
                
                if pitch:
                    # Текст для ВК (ВК не поддерживает Markdown так же, как Телеграм, поэтому делаем просто и чисто)
                    message = (
                        f"🚨 НАЙДЕН ЗАКАЗ ДЛЯ НАШЕГО ИИ!\n\n"
                        f"📌 {title}\n\n"
                        f"🔗 Открыть заказ: {link}\n\n"
                        f"🤖 Готовый отклик:\n{pitch}"
                    )
                    send_to_vk(message)
                    print(f"Нашли крутой заказ: {title}")
                
                save_task(task_id)
                time.sleep(2) 

if __name__ == "__main__":
    while True:
        try:
            check_freelance()
        except Exception as e:
            print(f"Ошибка в цикле: {e}")
        time.sleep(300)