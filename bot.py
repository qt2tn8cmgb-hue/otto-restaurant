import asyncio, json, os
from pathlib import Path
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

TOKEN = os.environ["BOT_TOKEN"]
ADMINS = {int(x) for x in os.environ["ADMIN_IDS"].replace(" ", "").split(",") if x}
PORT = int(os.getenv("PORT", "8080"))
BASE = Path(__file__).parent
DATA = BASE / "menu.json"
lock = asyncio.Lock()

def load(): return json.loads(DATA.read_text("utf-8"))
def save(d):
    tmp = DATA.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(DATA)

def find(d, iid):
    for ci, c in enumerate(d["categories"]):
        for it in c["items"]:
            if it["id"] == iid: return ci, c, it
    return None, None, None

def fmt_price(s):
    parts = [p.replace(" ", "") for p in s.replace("\u2009", "").split("/")]
    if not parts or not all(p.isdigit() and int(p) > 0 for p in parts): return None
    return "/".join(f"{int(p):,}".replace(",", " ") for p in parts)

class S(StatesGroup):
    price = State(); name = State(); n_ru = State(); n_tr = State(); n_price = State()

r = Router()
r.message.filter(F.from_user.id.in_(ADMINS))
r.callback_query.filter(F.from_user.id.in_(ADMINS))

def kb_main(d):
    b = InlineKeyboardBuilder()
    for i, c in enumerate(d["categories"]):
        b.button(text=f'{c["name"]["ru"]} ({len(c["items"])})', callback_data=f"cat:{i}")
    b.adjust(2); return b.as_markup()

def kb_cat(d, ci):
    b = InlineKeyboardBuilder()
    for it in d["categories"][ci]["items"]:
        b.button(text=f'{it["ru"]} — {it["price"]}', callback_data=f'it:{it["id"]}')
    b.adjust(1)
    b.row(*[x for x in InlineKeyboardBuilder().button(text="➕ Добавить блюдо", callback_data=f"add:{ci}").button(text="⬅️ Назад", callback_data="main").buttons])
    return b.as_markup()

def kb_item(iid, ci):
    b = InlineKeyboardBuilder()
    b.button(text="💰 Изменить цену", callback_data=f"price:{iid}")
    b.button(text="✏️ Изменить название", callback_data=f"name:{iid}")
    b.button(text="🗑 Удалить", callback_data=f"del:{iid}")
    b.button(text="⬅️ Назад", callback_data=f"cat:{ci}")
    b.adjust(1); return b.as_markup()

@r.message(CommandStart())
@r.message(Command("menu"))
async def start(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Меню OTTO. Выберите раздел:", reply_markup=kb_main(load()))

@r.callback_query(F.data == "main")
async def cb_main(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.edit_text("Меню OTTO. Выберите раздел:", reply_markup=kb_main(load())); await c.answer()

@r.callback_query(F.data.startswith("cat:"))
async def cb_cat(c: CallbackQuery, state: FSMContext):
    await state.clear(); ci = int(c.data[4:]); d = load()
    await c.message.edit_text(f'📂 {d["categories"][ci]["name"]["ru"]}\nНажмите на блюдо, чтобы изменить.', reply_markup=kb_cat(d, ci)); await c.answer()

@r.callback_query(F.data.startswith("it:"))
async def cb_item(c: CallbackQuery):
    d = load(); ci, cat, it = find(d, int(c.data[3:]))
    if not it: return await c.answer("Блюдо не найдено", show_alert=True)
    await c.message.edit_text(f'🍽 {it["ru"]}\n{it["tr"] or ""}\nЦена: {it["price"]} сум', reply_markup=kb_item(it["id"], ci)); await c.answer()

@r.callback_query(F.data.startswith("price:"))
async def cb_price(c: CallbackQuery, state: FSMContext):
    await state.set_state(S.price); await state.update_data(iid=int(c.data[6:]))
    await c.message.answer("Введите новую цену цифрами, например 75000 (две порции: 80000/150000):"); await c.answer()

@r.message(S.price)
async def got_price(m: Message, state: FSMContext):
    p = fmt_price(m.text or "")
    if not p: return await m.answer("Не понял цену. Пример: 75000 или 80000/150000")
    iid = (await state.get_data())["iid"]
    async with lock:
        d = load(); ci, _, it = find(d, iid)
        if it: it["price"] = p; save(d)
    await state.clear(); await m.answer(f"✅ Цена обновлена: {p}", reply_markup=kb_cat(load(), ci) if it else None)

@r.callback_query(F.data.startswith("name:"))
async def cb_name(c: CallbackQuery, state: FSMContext):
    await state.set_state(S.name); await state.update_data(iid=int(c.data[5:]))
    await c.message.answer("Введите новое название (по-русски):"); await c.answer()

@r.message(S.name)
async def got_name(m: Message, state: FSMContext):
    iid = (await state.get_data())["iid"]
    async with lock:
        d = load(); ci, _, it = find(d, iid)
        if it: it["ru"] = (m.text or "").strip()[:80]; save(d)
    await state.clear(); await m.answer("✅ Название обновлено", reply_markup=kb_cat(load(), ci) if it else None)

@r.callback_query(F.data.startswith("del:"))
async def cb_del(c: CallbackQuery):
    iid = int(c.data[4:])
    b = InlineKeyboardBuilder(); b.button(text="Да, удалить", callback_data=f"delok:{iid}"); b.button(text="Отмена", callback_data=f"it:{iid}")
    await c.message.edit_text("Точно удалить это блюдо?", reply_markup=b.as_markup()); await c.answer()

@r.callback_query(F.data.startswith("delok:"))
async def cb_delok(c: CallbackQuery):
    iid = int(c.data[6:])
    async with lock:
        d = load(); ci, cat, it = find(d, iid)
        if it: cat["items"].remove(it); save(d)
    await c.message.edit_text("🗑 Удалено.", reply_markup=kb_cat(load(), ci) if it else None); await c.answer()

@r.callback_query(F.data.startswith("add:"))
async def cb_add(c: CallbackQuery, state: FSMContext):
    await state.set_state(S.n_ru); await state.update_data(ci=int(c.data[4:]))
    await c.message.answer("Название блюда (по-русски):"); await c.answer()

@r.message(S.n_ru)
async def add_ru(m: Message, state: FSMContext):
    await state.update_data(ru=(m.text or "").strip()[:80]); await state.set_state(S.n_tr)
    await m.answer("Название по-турецки (или «-», если нет):")

@r.message(S.n_tr)
async def add_tr(m: Message, state: FSMContext):
    tr = (m.text or "").strip(); await state.update_data(tr="" if tr == "-" else tr[:80]); await state.set_state(S.n_price)
    await m.answer("Цена цифрами, например 75000:")

@r.message(S.n_price)
async def add_price(m: Message, state: FSMContext):
    p = fmt_price(m.text or "")
    if not p: return await m.answer("Не понял цену. Пример: 75000")
    x = await state.get_data()
    async with lock:
        d = load(); nid = max(i["id"] for c in d["categories"] for i in c["items"]) + 1
        d["categories"][x["ci"]]["items"].append({"id": nid, "ru": x["ru"], "tr": x["tr"], "price": p}); save(d)
    await state.clear(); await m.answer("✅ Блюдо добавлено", reply_markup=kb_cat(load(), x["ci"]))

async def menu_json(_):
    return web.Response(text=DATA.read_text("utf-8"), content_type="application/json",
                        headers={"Cache-Control": "no-store", "Access-Control-Allow-Origin": "*"})
async def index(_): return web.FileResponse(BASE / "site" / "index.html")

async def main():
    bot = Bot(TOKEN); dp = Dispatcher(storage=MemoryStorage()); dp.include_router(r)
    app = web.Application(); app.router.add_get("/", index); app.router.add_get("/menu.json", menu_json)
    run = web.AppRunner(app); await run.setup(); await web.TCPSite(run, "0.0.0.0", PORT).start()
    await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())
