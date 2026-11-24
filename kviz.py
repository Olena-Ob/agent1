"""Тест (квіз) «Що насправді керує вашими виборами?»: тексти в texts.py (QUIZ_...), надсилання в bot.py.
Тут: як виглядають питання і результат, підрахунок балів і статистика для /test_stat.
Відповіді людини зберігаються в базі (db.kv quiz:<id>), тож після оновлення агента тест не губиться."""
from collections import Counter

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import db
import texts as T

KEYS = list(T.QUIZ_RESULTS)  # A, B, C, D: відповідь з номером i дає бал результату KEYS[i]


def _kb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, url=d) if d.startswith("http") else
                                  InlineKeyboardButton(t, callback_data=d) for t, d in r] for r in rows if r])


def answers(uid, value=None):
    if value is None:
        return db.kv(f"quiz:{uid}") or ""
    return db.kv(f"quiz:{uid}", value)


def intro():
    return T.QUIZ_INTRO, _kb([[(T.BTN_QUIZ_GO, "qz:go")], [(T.BTN_HOME, "home")]])


def question(n):
    q, opts = T.QUIZ_QUESTIONS[n]
    lines = [T.QUIZ_PROGRESS.format(n=n + 1, total=len(T.QUIZ_QUESTIONS)), "", f"<b>{q}</b>", ""]
    lines += [f"{T.QUIZ_LETTERS[i]}) {o}" for i, o in enumerate(opts)]
    buttons = [(T.QUIZ_LETTERS[i], f"qz:{n}:{i}") for i in range(len(opts))]
    return "\n".join(lines), _kb([buttons, [(T.BTN_QUIZ_RESTART, "qz:re"), (T.BTN_HOME, "home")]])


def score(ans):
    """ans: рядок з букв результатів, напр. "AABDC..". Найбільше балів; якщо порівну, то вибір в останньому питанні."""
    c = Counter(ans)
    best = max(c.values())
    top = [k for k in KEYS if c.get(k) == best]
    return ans[-1] if ans and ans[-1] in top else top[0]


def result(key):
    title, text, advice = T.QUIZ_RESULTS[key]
    rows = [[(T.BTN_QUIZ_PRODUCT, "qz:prod")]]  # новим повідомленням, щоб результат лишився в чаті
    if key in T.QUIZ_PRACTICE_FOR and T.PRACTICE_FILE:
        rows.append([(T.BTN_QUIZ_PRACTICE, "qz:prak")])
    rows += [[(T.BTN_QUIZ_ASK, "write:quiz")], [(T.BTN_QUIZ_RESTART, "qz:re"), (T.BTN_HOME, "home")]]
    return T.QUIZ_RESULT.format(title=title, text=text, advice=advice), _kb(rows)


def stats(admins):
    """Почали (натиснули «Почати»), дійшли до кінця, і останній результат кожної людини."""
    with db.conn() as c:
        rows = c.execute("SELECT user_id, ev, extra FROM events WHERE ev IN ('quiz_start','quiz_done') ORDER BY at, rowid").fetchall()
    started, last = set(), {}
    for uid, ev, extra in rows:
        if uid in admins:
            continue
        if ev == "quiz_start":
            started.add(uid)
        elif extra in T.QUIZ_RESULTS:
            last[uid] = extra
    per = Counter(last.values())
    res = "\n".join(f"• {T.QUIZ_RESULTS[k][0]}: {per.get(k, 0)}" for k in KEYS)
    return T.QUIZ_STAT.format(title=T.QUIZ_TITLE, started=len(started), done=len(last), results=res)
