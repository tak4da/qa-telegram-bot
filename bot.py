import os
import re
import asyncio
import logging
from dataclasses import dataclass
from typing import List, Tuple, Optional
from collections import Counter

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.bot import DefaultBotProperties
from aiogram.types import Message
from aiogram.types import Message
from aiogram.dispatcher.filters import Command
from docx import Document

logging.basicConfig(level=logging.INFO)

# ----------------------------
# Настройки через ENV
# ----------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TOKEN")
DOCX_PATH = os.getenv("QA_DOCX", "типовые_вопросы_пилотов_ответы_260126.docx")

MIN_SCORE_TO_ANSWER = int(os.getenv("MIN_SCORE_TO_ANSWER", "60"))
MIN_SCORE_FOR_MULTI = int(os.getenv("MIN_SCORE_FOR_MULTI", "70"))
MULTI_WITHIN_POINTS = int(os.getenv("MULTI_WITHIN_POINTS", "7"))
TOP_K = int(os.getenv("TOP_K", "3"))

# ----------------------------
# Модель данных
# ----------------------------
@dataclass
class QAItem:
    q: str
    a: str

# ----------------------------
# Нормализация и простая похожесть
# ----------------------------
_RU_STOP = {
    "и", "в", "во", "на", "а", "но", "или", "ли", "что", "это", "как", "к", "ко", "по", "за",
    "из", "у", "о", "об", "от", "до", "для", "при", "если", "то", "же", "мы", "вы", "он", "она",
    "они", "я", "ты", "не", "нет", "да", "там", "тут", "вот", "уже", "ещё", "еще", "наиболее", "данный", "это"
}

_TERMS = {
    "anomalia", "GMV", "обновление", "алгоритм", "приоритет", "наполнение", "Z адреса", "площадь", "задание",
    "новый алгоритм", "критичный приоритет", "поставщик", "категория товара", "смена", "товар", "перемещение"
}

def normalize(text: str) -> str:
    t = text.lower().replace("ё", "е")
    t = re.sub(r"[\t\r\n]+", " ", t)
    t = re.sub(r"[^0-9a-zа-я %]+", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"\s+", " ", t).strip()
    return t

def tokenize(text: str) -> List[str]:
    t = normalize(text)
    tokens = [w for w in t.split() if len(w) >= 2 and w not in _RU_STOP]
    return tokens

def jaccard(a: List[str], b: List[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)

def overlap(a: List[str], b: List[str]) -> float:
    if not b:
        return 0.0
    ca, cb = Counter(a), Counter(b)
    inter = 0
    total = 0
    for k, v in cb.items():
        total += v
        inter += min(v, ca.get(k, 0))
    if total == 0:
        return 0.0
    return inter / total

def seq_ratio(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b).ratio()

def similarity_score(user_text: str, base_q: str) -> int:
    u_norm = normalize(user_text)
    q_norm = normalize(base_q)

    u_tok = tokenize(user_text)
    q_tok = tokenize(base_q)

    s1 = jaccard(u_tok, q_tok)            
    s2 = overlap(u_tok, q_tok)            
    s3 = seq_ratio(u_norm, q_norm)        

    score = (45 * s1) + (35 * s2) + (20 * s3)   
    return int(round(score))

# ----------------------------
# Парсинг DOCX
# ----------------------------
def extract_qa_from_docx(path: str) -> List[QAItem]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Не найден файл DOCX: {path}")

    doc = Document(path)
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]

    qa: List[QAItem] = []
    cur_q: Optional[str] = None
    cur_a_parts: List[str] = []
    mode = None  

    q_re = re.compile(r"^вопрос\s*:\s*(.*)$", re.IGNORECASE)
    a_re = re.compile(r"^ответ\s*:\s*(.*)$", re.IGNORECASE)

    def flush():
        nonlocal cur_q, cur_a_parts, mode
        if cur_q and cur_a_parts:
            a_text = "\n".join([x for x in cur_a_parts if x]).strip()
            qa.append(QAItem(q=cur_q.strip(), a=a_text))
        cur_q = None
        cur_a_parts = []
        mode = None

    for line in paragraphs:
        m_q = q_re.match(line)
        m_a = a_re.match(line)

        if m_q:
            flush()
            cur_q = (m_q.group(1) or "").strip()
            mode = "q"
            continue

        if m_a:
            mode = "a"
            first = (m_a.group(1) or "").strip()
            if first:
                cur_a_parts.append(first)
            continue

        if mode == "q":
            if cur_q:
                cur_q += " " + line
            else:
                cur_q = line
        elif mode == "a":
            cur_a_parts.append(line)
        else:
            continue

    flush()
    return qa

# ----------------------------
# Индекс поиска
# ----------------------------
class QASearch:
    def __init__(self, docx_path: str):
        self.docx_path = docx_path
        self.qa: List[QAItem] = []

    def load(self) -> int:
        self.qa = extract_qa_from_docx(self.docx_path)
        logging.info("Загружено QA: %s шт из %s", len(self.qa), self.docx_path)
        return len(self.qa)

    def search(self, user_text: str, top_k: int = 3) -> List[Tuple[int, QAItem]]:
        scored: List[Tuple[int, QAItem]] = []
        for item in self.qa:
            s = similarity_score(user_text, item.q)
            scored.append((s, item))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:top_k]

# ----------------------------
# Форматирование ответов
# ----------------------------
def format_candidates(cands: List[Tuple[int, QAItem]]) -> str:
    lines = ["Не уверен, что нашёл точное совпадение. Вот 3 ближайших вопроса:"]
    for idx, (score, item) in enumerate(cands, start=1):
        q_short = item.q.strip()
        if len(q_short) > 160:
            q_short = q_short[:160] + "…"
        lines.append(f"{idx}) ({score/100:.2f}) {q_short}")
    lines.append("")
    lines.append("Ответь цифрой 1-3, или перефразируй вопрос покороче.")
    return "\n".join(lines)

def format_single(best_score: int, best_item: QAItem) -> str:
    return f"{best_item.a.strip()}"

def format_multi(user_text: str, cands: List[Tuple[int, QAItem]]) -> str:
    lines = []
    lines.append("Похоже, в вопросе несколько тем. Отвечаю по пунктам:")
    for score, item in cands:
        q_short = item.q.strip()
        if len(q_short) > 140:
            q_short = q_short[:140] + "…"
        lines.append("")
        lines.append(f"• {q_short}")
        lines.append(item.a.strip())
    return "\n".join(lines)

# ----------------------------
# Aiogram
# ----------------------------
dp = Dispatcher()
index = QASearch(DOCX_PATH)

last_candidates = {}  

@dp.message(Command('start'))
async def on_start(message: Message):
    text = (
        "Привет! Я бот-помощник по FAQ.\n"
        "Напиши вопрос своими словами.\n\n"
        "Команды:\n"
        "/reload - перечитать DOCX\n"
        "/info - статус\n"
    )
    await message.answer(text)

@dp.message(Command('info'))
async def on_info(message: Message):
    await message.answer(
        f"Файл: {DOCX_PATH}\n"
        f"QA в памяти: {len(index.qa)}\n"
        f"Порог ответа: {MIN_SCORE_TO_ANSWER}\n"
        f"Порог мульти-ответа: {MIN_SCORE_FOR_MULTI}\n"
    )

@dp.message(Command('reload'))
async def on_reload(message: Message):
    try:
        count = index.load()
        await message.answer(f"Ок, перечитал базу. Вопросов: {count}")
    except Exception as e:
        await message.answer(f"Не смог перечитать DOCX: {e}")

def looks_like_choice(text: str) -> Optional[int]:
    t = normalize(text)
    if t in {"1", "2", "3"}:
        return int(t)
    m = re.match(r"^(?:вариант\s*)?([1-3])\.?$", t)
    if m:
        return int(m.group(1))
    return None

@dp.message()
async def on_question(message: types.Message):
    user_id = message.from_user.id if message.from_user else 0
    text = message.text or ""

    choice = looks_like_choice(text)
    if choice and user_id in last_candidates:
        cands = last_candidates[user_id]
        idx = choice - 1
        if 0 <= idx < len(cands):
            score, item = cands[idx]
            await message.answer(format_single(score, item))
            return

    if not index.qa:
        try:
            index.load()
        except Exception as e:
            await message.answer(f"База не загрузилась: {e}")
            return

    cands = index.search(text, top_k=TOP_K)
    last_candidates[user_id] = cands

    best_score, best_item = cands[0]
    second_score = cands[1][0] if len(cands) > 1 else 0
    third_score = cands[2][0] if len(cands) > 2 else 0

    if best_score < MIN_SCORE_TO_ANSWER:
        await message.answer(format_candidates(cands))
        return

    multi_pack = []
    if best_score >= MIN_SCORE_FOR_MULTI:
        multi_pack.append((best_score, best_item))
        if second_score >= MIN_SCORE_FOR_MULTI and (best_score - second_score) <= MULTI_WITHIN_POINTS:
            multi_pack.append(cands[1])
        if third_score >= MIN_SCORE_FOR_MULTI and (best_score - third_score) <= (MULTI_WITHIN_POINTS + 3):
            multi_pack.append(cands[2])

    if len(multi_pack) >= 2:
        await message.answer(format_multi(text, multi_pack))
        return

    await message.answer(format_single(best_score, best_item))

async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Нет BOT_TOKEN (или TOKEN) в переменных окружения.")

    index.load()

    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
