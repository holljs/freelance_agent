def check_freelance():
    print("Проверяю биржу...")
    processed = load_processed_tasks()
    
    # Надеваем маску обычного пользователя Chrome
    feedparser.USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    
    for url in RSS_URLS:
        # Для красоты вытаскиваем номер категории из ссылки
        cat_id = url.split("=")[-1] 
        
        feed = feedparser.parse(url)
        
        # Включаем рентген: смотрим реальный ответ Хабра
        status = getattr(feed, 'status', 'Ошибка сети/Блокировка')
        tasks_count = len(feed.entries)
        print(f"🔎 Категория {cat_id} | Статус Хабра: {status} | Найдено задач: {tasks_count}")
        
        for entry in feed.entries:
            task_id = entry.link.split("/")[-1] 
            
            if task_id not in processed:
                title = entry.title
                description = entry.description
                link = entry.link
                
                pitch = analyze_and_pitch(title, description, link)
                
                if pitch:
                    message = (
                        f"🚨 НОВЫЙ ПОДХОДЯЩИЙ ЗАКАЗ!\n\n"
                        f"📌 {title}\n\n"
                        f"🔗 Ссылка: {link}\n\n"
                        f"🤖 Готовый отклик:\n{pitch}"
                    )
                    send_to_vk(message)
                    print(f"✅ ВЗЯЛИ В РАБОТУ: {title}")
                else:
                    print(f"❌ Пропустили (не наш профиль): {title}")
                
                save_task(task_id)
                time.sleep(2)
