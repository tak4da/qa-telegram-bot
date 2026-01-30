import logging
import os
from aiogram import Bot, Dispatcher, types, executor
from aiogram.types import ParseMode
from docx import Document

# Настройки бота
API_TOKEN = os.getenv("BOT_TOKEN")
DOCX_PATH = os.getenv("QA_DOCX", "типовые_вопросы_пилотов_ответы_260126.docx")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

# Модели данных для вопросов, терминов и багфиксов
class QAItem:
    def __init__(self, question, answer):
        self.q = question
        self.a = answer

# Парсинг DOCX для извлечения вопросов и ответов
def extract_qa_from_docx(path: str):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Не найден файл DOCX: {path}")
    
    doc = Document(path)
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    
    qa = []
    cur_q = None
    cur_a_parts = []
    mode = None  # текущий режим: "q" - вопрос, "a" - ответ
    
    q_re = re.compile(r"^вопрос\s*:\s*(.*)$", re.IGNORECASE)
    a_re = re.compile(r"^ответ\s*:\s*(.*)$", re.IGNORECASE)

    for line in paragraphs:
        m_q = q_re.match(line)
        m_a = a_re.match(line)
        
        if m_q:
            if cur_q and cur_a_parts:
                qa.append(QAItem(cur_q.strip(), "\n".join(cur_a_parts)))
            cur_q = m_q.group(1)
            cur_a_parts = []
            mode = "q"
            continue
        
        if m_a:
            if cur_q:
                qa.append(QAItem(cur_q.strip(), "\n".join(cur_a_parts)))
            cur_a_parts = [m_a.group(1)]
            mode = "a"
            continue
        
        if mode == "q":
            cur_q += " " + line
        elif mode == "a":
            cur_a_parts.append(line)
    
    if cur_q and cur_a_parts:
        qa.append(QAItem(cur_q.strip(), "\n".join(cur_a_parts)))

    return qa

# Загрузка вопросов и ответов из файла
qa_data = extract_qa_from_docx(DOCX_PATH)

# Команды для бота
@dp.message_handler(commands=['start'])
async def send_welcome(message: types.Message):
    await message.reply(
        "Привет! Я помогу тебе с вопросами и ответами.\n\n"
        "Выберите одну из опций:\n"
        "/questions - Вопросы и ответы\n"
        "/terms - Термины\n"
        "/bugfixes - Исправления багов"
    )

@dp.message_handler(commands=['questions'])
async def send_questions(message: types.Message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for qa in qa_data[:10]:  # Показываем первые 10 вопросов
        markup.add(types.KeyboardButton(qa.q))
    await message.reply("Выберите вопрос:", reply_markup=markup)

@dp.message_handler(commands=['terms'])
async def send_terms(message: types.Message):
    terms_list = "\n".join([
        "SSCC: Серийный код транспортной упаковки.",
        "GMV: Общий объем продаж товара.",
        "ЛМWork: Программный продукт для управления задачами.",
        # Добавьте остальные термины по аналогии
    ])
    await message.reply(f"Термины:\n\n{terms_list}")

@dp.message_handler(commands=['bugfixes'])
async def send_bug_fixes(message: types.Message):
    bug_fixes = [
        "Исправлена ошибка в карточке товара при пополнении К адреса.",
        "Исправлена проблема с созданием задания при изменении стока.",
        # Добавьте остальные багфиксы
    ]
    await message.reply(f"Исправления багов:\n\n" + "\n".join(bug_fixes))

@dp.message_handler(lambda message: True)
async def handle_question(message: types.Message):
    user_text = message.text.lower()
    matched_answers = []

    # Ищем лучший ответ по схожести
    for qa in qa_data:
        if user_text in qa.q.lower():
            matched_answers.append(qa)
    
    if matched_answers:
        # Если нашли несколько ответов
        if len(matched_answers) > 1:
            response = "Нашел несколько вариантов:\n"
            for idx, qa in enumerate(matched_answers, 1):
                response += f"{idx}) {qa.a}\n"
            response += "Выберите номер ответа."
            await message.reply(response)
        else:
            await message.reply(f"Ответ: {matched_answers[0].a}")
    else:
        await message.reply("Извините, я не нашел подходящего ответа.")

async def main():
    if not API_TOKEN:
        raise RuntimeError("Нет BOT_TOKEN (или TOKEN) в переменных окружения.")
    
    bot = Bot(API_TOKEN)
    await dp.start_polling()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
