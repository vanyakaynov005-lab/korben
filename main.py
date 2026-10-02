import discord
from discord.ext import tasks, commands
import sqlite3
import time
import os

TOKEN = os.getenv("TOKEN")

GUILD_ID = 804372207069429782
NOTIFY_CHANNEL_ID = 804372207538143273  # Канал для оповещений об апе

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

# Множество всех ID ранговых ролей для быстрой зачистки
ALL_RANK_ROLE_IDS = set(ROLE_THRESHOLDS.values())

intents = discord.Intents.default()
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Подключение к базе
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
    old_cutoff = int(time.time()) - (35 * 24 * 60 * 60)
    cursor.execute("DELETE FROM voice_logs WHERE start_time < ?", (old_cutoff,))
    db.commit()
    if not check_roles_loop.is_running():
        check_roles_loop.start()

@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return

    now = int(time.time())

    # Зашел в войс (игнорируем стримы/муты, важен сам факт входа)
    if before.channel is None and after.channel is not None:
        active_sessions[member.id] = now

    # Вышел из войса
    elif before.channel is not None and after.channel is None:
        start_time = active_sessions.pop(member.id, None)
        if start_time:
            duration = now - start_time
            if duration >= 10:
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

    notify_channel = guild.get_channel(NOTIFY_CHANNEL_ID)
    now = int(time.time())

    for member in guild.members:
        if member.bot:
            continue

        total_seconds = get_voice_seconds_last_30_days(member.id)
        if member.id in active_sessions:
            total_seconds += (now - active_sessions[member.id])

        hours = total_seconds / 3600.0

        # Ищем строго одну наивысшую роль, которую заслужил юзер
        best_role_id = None
        for threshold in sorted(ROLE_THRESHOLDS.keys(), reverse=True):
            if hours >= threshold:
                best_role_id = ROLE_THRESHOLDS[threshold]
                break

        # 1. Находим все уровневые роли, которые висят на участнике прямо сейчас
        user_rank_roles = [r for r in member.roles if r.id in ALL_RANK_ROLE_IDS]

        # 2. Определяем, какие из них лишние (все, кроме best_role_id)
        roles_to_remove = [r for r in user_rank_roles if r.id != best_role_id]
        if roles_to_remove:
            try:
                await member.remove_roles(*roles_to_remove, reason="Чистка старых/лишних ролей онлайна")
                print(f"🧹 Сняты лишние роли у {member.name}: {[r.name for r in roles_to_remove]}")
            except discord.Forbidden:
                print(f"❌ Ошибка прав: роль бота должна стоять ВЫШЕ уровневых ролей!")
            except Exception as e:
                print(f"Ошибка при удалении ролей: {e}")

        # 3. Выдаем новую роль, если её ещё нет
        if best_role_id:
            target_role = guild.get_role(best_role_id)
            if target_role and target_role not in member.roles:
                try:
                    await member.add_roles(target_role, reason=f"Достигнут порог онлайна ({round(hours, 1)} ч.)")
                    print(f"👑 Выдана роль {target_role.name} пользователю {member.name}")

                    # Отправляем оповещение об апе в указанный канал
                    if notify_channel:
                        try:
                            await notify_channel.send(
                                f"🎉 {member.mention} налетал в войсе **{round(hours, 1)} ч.** и получил роль {target_role.mention}!"
                            )
                        except Exception as e:
                            print(f"Не удалось отправить сообщение в канал оповещений: {e}")
                except discord.Forbidden:
                    print(f"❌ Ошибка прав при выдаче роли {target_role.name}!")
                except Exception as e:
                    print(f"Ошибка при выдаче роли: {e}")

# Команда для проверки времени
@bot.command(name="войс")
async def check_my_voice(ctx):
    now = int(time.time())
    total_seconds = get_voice_seconds_last_30_days(ctx.author.id)
    if ctx.author.id in active_sessions:
        total_seconds += (now - active_sessions[ctx.author.id])

    hours = round(total_seconds / 3600.0, 1)
    await ctx.send(f"👤 {ctx.author.mention}, твой онлайн за последние 30 дней: **{hours} ч.**")

bot.run(TOKEN)
