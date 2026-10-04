import os
import time
import sqlite3
import discord
from discord.ext import tasks, commands

TOKEN = os.getenv("TOKEN")

GUILD_ID = 804372207069429782
NOTIFY_CHANNEL_ID = 804372207538143273  # Канал для поздравлений с апом

# Сетка: количество часов -> ID роли
ROLE_THRESHOLDS = {
    1: 1543567647482839112,   # Роль I   (1 час)
    5: 1543567638951886948,   # Роль II  (5 часов)
    10: 1526279146727145664,  # Роль III (10 часов)
    15: 1446619619057205288,  # Роль IV  (15 часов)
    30: 1346270564477698118,  # Роль V   (30 часов)
    50: 1351997282005815356,  # Роль VI  (50 часов)
    75: 1351997240461230233,  # Роль VII (75 часов)
    100: 1351997159322550312, # Роль VIII (100 часов)
    125: 1351996707428372530, # Роль IX  (125 часов)
    150: 1544266256427778059  # Роль X   (150 часов)
}

ALL_RANK_ROLE_IDS = set(ROLE_THRESHOLDS.values())

intents = discord.Intents.default()
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Подключение к локальной базе данных
db = sqlite3.connect("voice_stats.db")
cursor = db.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS voice_logs (
    user_id INTEGER,
    start_time INTEGER,
    duration INTEGER
)
""")
db.commit()

# Временное хранилище активных сессий: user_id -> timestamp последней фиксации
active_sessions = {}

def get_voice_seconds_last_30_days(user_id: int) -> int:
    cutoff = int(time.time()) - (30 * 24 * 60 * 60)
    cursor.execute("""
        SELECT SUM(duration) FROM voice_logs 
        WHERE user_id = ? AND start_time >= ?
    """, (user_id, cutoff))
    res = cursor.fetchone()[0]
    return res if res else 0

@bot.event
async def on_ready():
    print(f"Бот запущен под именем: {bot.user}")
    # Чистим логи старше 35 дней
    old_cutoff = int(time.time()) - (35 * 24 * 60 * 60)
    cursor.execute("DELETE FROM voice_logs WHERE start_time < ?", (old_cutoff,))
    db.commit()

    # Инициализируем тех, кто уже сидит в войсах при старте/рестарте бота
    now = int(time.time())
    for guild in bot.guilds:
        for channel in guild.voice_channels:
            for member in channel.members:
                if not member.bot:
                    active_sessions[member.id] = now

    if not check_roles_loop.is_running():
        check_roles_loop.start()

@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return

    now = int(time.time())

    # Фиксируем только реальную смену каналов, вход или выход.
    # Муты микрофона, включение наушников, вебки и стримы игнорируются.
    if before.channel != after.channel:
        # Если вышел из канала (или перешел в другой) — закрываем кусок сессии
        if before.channel is not None:
            start_time = active_sessions.pop(member.id, None)
            if start_time:
                duration = now - start_time
                if duration >= 10:
                    cursor.execute(
                        "INSERT INTO voice_logs (user_id, start_time, duration) VALUES (?, ?, ?)",
                        (member.id, start_time, duration)
                    )
                    db.commit()

        # Если зашел в новый канал — стартуем новый отрезок
        if after.channel is not None:
            active_sessions[member.id] = now

# Фоновая проверка и синхронизация каждые 5 минут
@tasks.loop(minutes=5)
async def check_roles_loop():
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    now = int(time.time())

    # 1. СБРОС АКТИВНЫХ СЕССИЙ В БАЗУ
    # Каждые 5 минут вбиваем набежавшее время в SQLite, сдвигая точку отсчета.
    for user_id in list(active_sessions.keys()):
        start_time = active_sessions[user_id]
        duration = now - start_time
        if duration >= 10:
            cursor.execute(
                "INSERT INTO voice_logs (user_id, start_time, duration) VALUES (?, ?, ?)",
                (user_id, start_time, duration)
            )
            active_sessions[user_id] = now
    db.commit()

    notify_channel = guild.get_channel(NOTIFY_CHANNEL_ID)

    # 2. ПРОВЕРКА И СИНХРОНИЗАЦИЯ РОЛЕЙ
    for m in guild.members:
        if m.bot:
            continue

        try:
            member = await guild.fetch_member(m.id)
        except Exception:
            member = m

        total_seconds = get_voice_seconds_last_30_days(member.id)
        hours = total_seconds / 3600.0

        # Вычисляем максимальную роль, которую заслужил юзер под текущие часы
        best_threshold = 0
        best_role_id = None
        for threshold in sorted(ROLE_THRESHOLDS.keys(), reverse=True):
            if hours >= threshold:
                best_role_id = ROLE_THRESHOLDS[threshold]
                best_threshold = threshold
                break

        # Роли онлайна, которые висят на человеке прямо сейчас
        current_rank_roles = [r for r in member.roles if r.id in ALL_RANK_ROLE_IDS]
        current_rank_ids = {r.id for r in current_rank_roles}

        # Определяем максимальный порог ролей, которые УЖЕ висят на юзере
        current_max_threshold = 0
        for thresh, r_id in ROLE_THRESHOLDS.items():
            if r_id in current_rank_ids:
                if thresh > current_max_threshold:
                    current_max_threshold = thresh

        # Если нужная роль уже висит и лишних ролей нет — пропускаем
        if best_role_id in current_rank_ids and len(current_rank_ids) == 1:
            continue

        # Снимаем все старые / дублирующие роли
        roles_to_remove = [r for r in current_rank_roles if r.id != best_role_id]
        if roles_to_remove:
            try:
                await member.remove_roles(*roles_to_remove, reason="Зачистка старых/лишних ролей онлайна")
                print(f"🧹 Сняты лишние роли у {member.name}: {[r.name for r in roles_to_remove]}")
            except discord.Forbidden:
                print(f"❌ ОШИБКА: Роль бота должна стоять ВЫШЕ уровневых ролей в настройках сервера!")
            except Exception as e:
                print(f"Ошибка при снятии ролей: {e}")

        # Выдаем новую актуальную роль
        if best_role_id and best_role_id not in current_rank_ids:
            target_role = guild.get_role(best_role_id)
            if target_role:
                try:
                    await member.add_roles(target_role, reason=f"Порог онлайна ({round(hours, 1)} ч.)")
                    print(f"👑 Выдана роль {target_role.name} пользователю {member.name}")

                    # Отправляем сообщение СТРОГО при повышении уровня
                    if best_threshold > current_max_threshold:
                        if notify_channel:
                            try:
                                await notify_channel.send(
                                    f"🎉 {member.mention} налетал в войсе **{round(hours, 1)} ч.** и получил роль {target_role.mention}!"
                                )
                            except Exception as e:
                                print(f"Не удалось отправить уведомление: {e}")
                    else:
                        print(f"Роль обновлена без уведомления (откат часов или синхронизация после рестарта)")
                except discord.Forbidden:
                    print(f"❌ Ошибка прав: не удалось выдать роль {target_role.name}!")
                except Exception as e:
                    print(f"Ошибка при выдаче роли: {e}")

# Команда для проверки времени участником: !войс
@bot.command(name="войс")
async def check_my_voice(ctx):
    now = int(time.time())
    total_seconds = get_voice_seconds_last_30_days(ctx.author.id)
    if ctx.author.id in active_sessions:
        total_seconds += (now - active_sessions[ctx.author.id])

    hours = round(total_seconds / 3600.0, 1)
    await ctx.send(f"👤 {ctx.author.mention}, твой онлайн за последние 30 дней: **{hours} ч.**")

# Админ-команда для проверки состояния SQLite: !бд
@bot.command(name="бд")
@commands.has_permissions(administrator=True)
async def check_db(ctx):
    if not os.path.exists("voice_stats.db"):
        await ctx.send("❌ Файла `voice_stats.db` нет на диске!")
        return

    size_kb = round(os.path.getsize("voice_stats.db") / 1024, 2)
    cursor.execute("SELECT COUNT(*) FROM voice_logs")
    total_rows = cursor.fetchone()[0]

    cursor.execute("SELECT user_id, duration, start_time FROM voice_logs ORDER BY rowid DESC LIMIT 5")
    rows = cursor.fetchall()

    msg = f"📁 **Статус базы данных:**\n"
    msg += f"• Размер файла: `{size_kb} KB`\n"
    msg += f"• Всего записей в логах: `{total_rows}`\n\n"
    msg += "🕒 **Последние 5 записей:**\n"

    if rows:
        for uid, dur, st in rows:
            mins = round(dur / 60, 1)
            msg += f"• <@{uid}>: `{mins} мин.` (timestamp: {st})\n"
    else:
        msg += "*(пусто, записей пока нет)*"

    await ctx.send(msg)

bot.run(TOKEN)
