import discord
from discord.ext import tasks, commands
import sqlite3
import time
import os

TOKEN = os.getenv("TOKEN")

GUILD_ID = 804372207069429782  # Вставь ID своего сервера Discord

# Твоя сетка: количество часов -> ID роли
# Можешь менять часы и ID как угодно
ROLE_THRESHOLDS = {
    1: 1543567647482839112,   # Роль I   (1 час)
    5: 1543567638951886948,   # Роль II  (5 часов)
    10: 1526279146727145664,  # Роль III (15 часов)
    15: 1446619619057205288,  # Роль IV  (30 часов)
    30: 1346270564477698118,  # Роль V   (50 часов)
    50: 1351997282005815356,  # Роль VI  (75 часов)
    75: 1351997240461230233, # Роль VII (100 часов)
    100: 1351997159322550312, # Роль VIII (125 часов)
    125: 1351996707428372530, # Роль IX  (150 часов)
    150: 1544266256427778059  # Роль X   (200 часов)
}



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

# Временное хранение заходов: user_id -> timestamp захода
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
    # Чистим логи старше 35 дней, чтобы база не раздувалась
    old_cutoff = int(time.time()) - (35 * 24 * 60 * 60)
    cursor.execute("DELETE FROM voice_logs WHERE start_time < ?", (old_cutoff,))
    db.commit()
    check_roles_loop.start()

@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return

    now = int(time.time())

    # Юзер зашел в войс
    if before.channel is None and after.channel is not None:
        active_sessions[member.id] = now

    # Юзер вышел из войса
    elif before.channel is not None and after.channel is None:
        start_time = active_sessions.pop(member.id, None)
        if start_time:
            duration = now - start_time
            if duration >= 10:  # Игнорим скачки меньше 10 секунд
                cursor.execute(
                    "INSERT INTO voice_logs (user_id, start_time, duration) VALUES (?, ?, ?)",
                    (member.id, start_time, duration)
                )
                db.commit()

# Фоновая проверка каждые 5 минут
@tasks.loop(minutes=5)
async def check_roles_loop():
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    now = int(time.time())

    for member in guild.members:
        if member.bot:
            continue

        # Считаем записанные секунды за последние 30 дней
        total_seconds = get_voice_seconds_last_30_days(member.id)

        # Если прямо сейчас сидит в войсе — плюсуем текущую сессию
        if member.id in active_sessions:
            total_seconds += (now - active_sessions[member.id])

        hours = total_seconds / 3600.0

        # Синхронизируем роли
        for threshold_hours, role_id in ROLE_THRESHOLDS.items():
            role = guild.get_role(role_id)
            if not role:
                continue

            if hours >= threshold_hours:
                # Порог пройден — выдаем роль, если еще нет
                if role not in member.roles:
                    try:
                        await member.add_roles(role, reason="Награда за активность в войсе (30 дней)")
                        print(f"Выдана роль {role.name} пользователю {member.name}")
                    except discord.Forbidden:
                        print(f"Ошибка прав: роль бота должна стоять ВЫШЕ роли {role.name}!")
            else:
                # Порог не пройден (время сгорело за 30 дней) — забираем роль
                if role in member.roles:
                    try:
                        await member.remove_roles(role, reason="Просадка онлайна ниже порога за 30 дней")
                        print(f"Отобрана роль {role.name} у пользователя {member.name}")
                    except discord.Forbidden:
                        print(f"Ошибка прав: роль бота должна стоять ВЫШЕ роли {role.name}!")

# Команда для проверки своего времени в чате: !войс
@bot.command(name="войс")
async def check_my_voice(ctx):
    now = int(time.time())
    total_seconds = get_voice_seconds_last_30_days(ctx.author.id)
    if ctx.author.id in active_sessions:
        total_seconds += (now - active_sessions[ctx.author.id])

    hours = round(total_seconds / 3600.0, 1)
    await ctx.send(f"👤 {ctx.author.mention}, твой онлайн за последние 30 дней: **{hours} ч.**")

bot.run(TOKEN)
