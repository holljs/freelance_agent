import os
import feedparser
import requests
import time
from replicate import Client
from dotenv import load_dotenv
import vk_api 
from vk_api.utils import get_random_id

# Загружаем данные из файла .env
load_dotenv()

# --- НАСТРОЙКИ ---
VK_TOKEN = os.getenv("VK_TOKEN")           
VK_USER_ID = os.getenv("VK_USER_ID")       
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN")

client = Client(api_token=REPLICATE_API_TOKEN)

# Авторизуем бота в ВК
vk_session = vk_api.VkApi(token=VK_TOKEN)
vk = vk_session.get_api()

# Расширенный список категорий Хабр Фриланса
RSS_URLS = [
    "https://freelance.habr.com/tasks.rss?category_id=98",  # Боты и ИИ
    "https://freelance.habr.com/tasks.rss?category_id=97",  # Парсинг и скрипты
    "https://freelance.habr.com/tasks.rss?category_id=113", # Интеграция API
    "https://freelance.habr.com/tasks.rss?category_id=73"   # Бэкенд (Python/Веб)
]

# Файл памяти, чтобы не присылать одни и те же заказы
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
            random_id=get_random_id() 
        )
    except Exception as e:
        print(f"Ошибка отправки сообщения в ВК: {e}")

def analyze_and_pitch(title, description, link):
    system_prompt = (
        "Ты — крутой Python-разработчик. Твой стек: боты (ВКонтакте и Telegram), FastAPI, парсинг данных, "
        "интеграция сторонних API и работа с нейросетями (Replicate, OpenAI, генерация фото/видео/текста). "
        "Проанализируй заказ. Если заказ можно выполнить с помощью Python, API, нейросетей или написав бота — "
        "напиши профессиональный, вежливый и короткий отклик (питч), предложив свой стек и готовность начать. "
        "Если заказ вообще не из нашей сферы (например: верстка HTML, дизайн логотипа в Photoshop, 1C-бухгалтерия, SEO-продвижение, копирайтинг) — "
        "ответь строго одним словом: ИГНОР."
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
                    # Подходит! Отправляем в ВК
                    message = (
                        f"🚨 НОВЫЙ ПОДХОДЯЩИЙ ЗАКАЗ!\n\n"
                        f"📌 {title}\n\n"
                        f"🔗 Ссылка: {link}\n\n"
                        f"🤖 Готовый отклик:\n{pitch}"
                    )
                    send_to_vk(message)
                    print(f"✅ ВЗЯЛИ В РАБОТУ: {title}")
                else:
                    # Не подходит! Выводим в лог
                    print(f"❌ Пропустили (не наш профиль): {title}")
                
                save_task(task_id)
                time.sleep(2) 

if __name__ == "__main__":
    while True:
        try:
            check_freelance()
        except Exception as e:
            print(f"Ошибка в цикле: {e}")
        time.sleep(300)
