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

# Меняем Хабр на крупнейшую биржу FL.ru (Категория: Программирование)
RSS_URLS = [
    "https://www.fl.ru/rss/all.xml?category=5"
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
    # Достаем токен из окружения
    SILICONFLOW_API_TOKEN = os.getenv("SILICONFLOW_API_TOKEN")
    if not SILICONFLOW_API_TOKEN:
        print("⚠️ Ошибка: SILICONFLOW_API_TOKEN не найден в .env")
        return None

    system_prompt = (
        "Ты — крутой Python-разработчик. Твой стек: боты (ВКонтакте и Telegram), FastAPI, парсинг данных, "
        "интеграция сторонних API и работа с нейросетями (Replicate, OpenAI, генерация фото/видео/текста). "
        "Проанализируй заказ. Если заказ можно выполнить с помощью Python, API, нейросетей или написав бота — "
        "напиши профессиональный, вежливый и короткий отклик (питч), предложив свой стек и готовность начать. "
        "Если заказ вообще не из нашей сферы (например: верстка HTML, дизайн логотипа в Photoshop, 1C-бухгалтерия, SEO-продвижение, копирайтинг) — "
        "ответь строго одним словом: ИГНОР."
    )
    
    url = "https://api.siliconflow.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {SILICONFLOW_API_TOKEN}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "deepseek-ai/DeepSeek-V3",  # 🔥 Заменили на копеечный DeepSeek!
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Заголовок: {title}\nОписание: {description}"}
        ],
        "max_tokens": 600,
        "temperature": 0.5
    }

    # 3 попытки достучаться до Китая, если упадет сеть
    for attempt in range(3):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            if response.status_code == 200:
                res_json = response.json()
                answer = res_json["choices"][0]["message"]["content"].strip()
                
                if "ИГНОР" in answer:
                    return None
                return answer
            else:
                print(f"⚠️ SiliconFlow вернул статус {response.status_code}: {response.text}")
        except Exception as e:
            print(f"⚠️ Ошибка сети на попытке {attempt + 1}: {e}")
        time.sleep(2)
        
    return None

def check_freelance():
    print("\nПроверяю биржу FL.ru...")
    processed = load_processed_tasks()
    
    # Надеваем маску обычного пользователя Chrome
    feedparser.USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    
    for url in RSS_URLS:
        feed = feedparser.parse(url)
        
        # Рентген: смотрим реальный ответ
        status = getattr(feed, 'status', 'Ошибка сети/Блокировка')
        tasks_count = len(feed.entries)
        print(f"🔎 Статус FL.ru: {status} | Найдено задач: {tasks_count}")
        
        for entry in feed.entries:
            # Используем саму ссылку как 100% уникальный ID для FL.ru
            task_id = entry.link 
            
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
                    print(f"❌ Пропустили: {title}")
                
                save_task(task_id)
                time.sleep(2) 

# Вечный двигатель
if __name__ == "__main__":
    while True:
        try:
            check_freelance()
        except Exception as e:
            print(f"Ошибка в цикле: {e}")
        time.sleep(300)
