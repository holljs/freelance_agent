import os
import time
import feedparser
import requests
import vk_api
from vk_api.utils import get_random_id
from replicate import Client
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from urllib.parse import urljoin

# Загружаем переменные окружения из .env
load_dotenv()

# --- НАСТРОЙКИ ---
VK_TOKEN = os.getenv("VK_TOKEN")
VK_USER_ID = os.getenv("VK_USER_ID")
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN")
TOKENROUTER_API_TOKEN = os.getenv("TOKENROUTER_API_TOKEN") or os.getenv("TOKENROUTER_API_KEY")

replicate_client = Client(api_token=REPLICATE_API_TOKEN) if REPLICATE_API_TOKEN else None

vk_session = vk_api.VkApi(token=VK_TOKEN)
vk = vk_session.get_api()

# Заменяем RSS_URLS на:
RSS_URLS = [
    "https://freelance.habr.com/tasks?categories=development_bots,development_all_inclusive,development_backend,development_scripts.rss",
    # Avito через их RSS (работает!)
    "https://www.avito.ru/rss?q=%D0%B1%D0%BE%D1%82&categoryId=115",   # "бот"
    "https://www.avito.ru/rss?q=%D0%BF%D0%B0%D1%80%D1%81%D0%B8%D0%BD%D0%B3&categoryId=115",  # "парсинг"
    "https://www.avito.ru/rss?q=%D0%B0%D0%B2%D1%82%D0%BE%D0%BC%D0%B0%D1%82%D0%B8%D0%B7%D0%B0%D1%86%D0%B8%D1%8F&categoryId=115",  # "автоматизация"
]

DB_FILE = "processed_tasks.txt"



def parse_freelancehunt():
    """Парсит свежие заказы с Freelancehunt (без RSS)"""
    url = "https://freelancehunt.com/projects"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "ru,en;q=0.9"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            print(f"⚠️ Freelancehunt статус {resp.status_code}")
            return []
        soup = BeautifulSoup(resp.text, 'html.parser')
        tasks = []
        for card in soup.select('[data-project-id], .project-card, article, [class*="project"]')[:30]:
            try:
                title_el = card.select_one('h3, h4, .project-title, a')
                link_el = card.select_one('a[href*="/project/"]') or title_el
                desc_el = card.select_one('.project-description, p, [class*="description"]')
                if not title_el or not link_el:
                    continue
                href = link_el.get('href', '')
                if not href.startswith('http'):
                    href = urljoin("https://freelancehunt.com", href)
                tasks.append({
                    'title': title_el.get_text(strip=True),
                    'description': (desc_el.get_text(strip=True) if desc_el else '')[:500],
                    'link': href
                })
            except Exception:
                continue
        return tasks
    except Exception as e:
        print(f"⚠️ Ошибка парсинга Freelancehunt: {e}")
        return []


def load_processed_tasks():
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return set(f.read().splitlines())
    except FileNotFoundError:
        return set()


def save_task(task_id):
    with open(DB_FILE, "a", encoding="utf-8") as f:
        f.write(f"{task_id}\n")


def send_to_vk(text):
    try:
        vk.messages.send(
            user_id=int(VK_USER_ID),
            message=text,
            random_id=get_random_id()
        )
    except Exception as e:
        print(f"⚠️ Ошибка отправки сообщения в ВК: {e}")


def call_tokenrouter(system_prompt, user_content):
    if not TOKENROUTER_API_TOKEN:
        return None

    url = "https://api.tokenrouter.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {TOKENROUTER_API_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "deepseek/deepseek-v4-flash",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ],
        "max_tokens": 500,
        "temperature": 0.4
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=20)
        if resp.status_code == 200:
            res_json = resp.json()
            return res_json["choices"][0]["message"]["content"].strip()
        elif resp.status_code == 429:
            print("⚠️ TokenRouter: превышен лимит запросов (429), переключаемся на Replicate...")
            return None
        else:
            print(f"⚠️ TokenRouter статус {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"⚠️ Ошибка сети TokenRouter: {e}")
    return None


def call_replicate_fallback(system_prompt, user_content):
    if not replicate_client:
        return None
    try:
        output = replicate_client.run(
            "deepseek-ai/deepseek-r1",
            input={
                "prompt": f"{system_prompt}\n\nЗаказ:\n{user_content}",
                "max_tokens": 500,
                "temperature": 0.4
            }
        )
        if isinstance(output, list):
            return "".join(output).strip()
        return str(output).strip()
    except Exception as e:
        print(f"⚠️ Ошибка вызова Replicate Fallback: {e}")
        return None


def analyze_and_pitch(title, description, link):
    system_prompt = (
        "Ты — опытный Python-разработчик и фрилансер. "
        "Твой стек: Telegram и VK боты (aiogram, vk_api), FastAPI бэкенды, парсинг данных и веб-скрейпинг, "
        "интеграция ИИ-моделей (OpenAI, DeepSeek, Replicate, генерация изображений/текста/голоса), автоматизация рутины.\n\n"
        "Правила оценки заказа:\n"
        "1. Если заказ НЕ подходит (1С, верстка лендингов, Figma/Photoshop дизайн, копирайтинг, SEO) — ответь строго одним словом: ИГНОР.\n"
        "2. Если заказ подходит — напиши короткий (до 4-5 предложений), вежливый отклик: "
        "покажи понимание задачи, предложи конкретное решение на Python/API и укажи готовность начать прямо сейчас."
    )
    user_content = f"Заголовок: {title}\nОписание: {description}\nСсылка: {link}"

    # 1. Сначала пробуем TokenRouter
    answer = call_tokenrouter(system_prompt, user_content)

    # 2. Если TokenRouter выдал 429 или недоступен — используем Replicate
    if not answer:
        print("🔄 Пробуем резервный вызов через Replicate...")
        answer = call_replicate_fallback(system_prompt, user_content)

    if answer and "ИГНОР" not in answer.upper():
        return answer
    return None


def check_freelance():
    print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Проверяю биржи фриланса...")
    processed = load_processed_tasks()

    feedparser.USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    for url in RSS_URLS:
        try:
            feed = feedparser.parse(url)
            status = getattr(feed, 'status', '200 (OK)')
            print(f"🔎 RSS: {url[:30]}... | Статус: {status} | Задач: {len(feed.entries)}")

            for entry in feed.entries:
                task_id = entry.link

                if task_id not in processed:
                    title = getattr(entry, 'title', 'Без заголовка')
                    description = getattr(entry, 'description', '')
                    link = entry.link

                    pitch = analyze_and_pitch(title, description, link)

                    if pitch:
                        message = (
                            f"🚨 ПОДХОДЯЩИЙ ЗАКАЗ!\n\n"
                            f"📌 {title}\n\n"
                            f"🔗 {link}\n\n"
                            f"🤖 Черновик отклика:\n{pitch}"
                        )
                        send_to_vk(message)
                        print(f"✅ ВЗЯТО: {title}")
                    else:
                        print(f"❌ Пропуск: {title}")

                    save_task(task_id)
                    time.sleep(2)

        except Exception as e:
            print(f"⚠️ Ошибка при обработке ленты {url}: {e}")


if __name__ == "__main__":
    while True:
        try:
            check_freelance()
        except Exception as e:
            print(f"⚠️ Ошибка в главном цикле: {e}")
        time.sleep(300)
