import discord
from discord.ext import commands
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# --- CONFIGURAÇÕES DE INTENTS ---
intents = discord.Intents.default()
intents.message_content = True  # Necessário para ler o conteúdo das mensagens (!)
intents.members = True          # Necessário para listar membros e dar XP/Gold

# --- INICIALIZAÇÃO DO BOT ---
bot = commands.Bot(
    command_prefix="!", 
    intents=intents,
    application_id=1486481265703125142
)

@bot.event
async def on_ready():
    print(f'✅ Bot Conectado: {bot.user.name}')
    print(f'🆔 ID do Bot: {bot.user.id}')
    print('---') 
    
    # Sincroniza os comandos automaticamente ao ligar
    try:
        synced = await bot.tree.sync()
        print(f"♻️ Sincronizados {len(synced)} comandos globais.")
    except Exception as e:
        print(f"❌ Erro ao sincronizar no on_ready: {e}")

async def load_extensions():
    """Carrega todos os arquivos da pasta /cogs"""
    if not os.path.exists('./cogs'):
        os.makedirs('./cogs')

    for filename in os.listdir('./cogs'):
        if filename.endswith('.py'):
            try:
                await bot.load_extension(f'cogs.{filename[:-3]}')
                print(f'⚙️ Extensão carregada: {filename}')
            except Exception as e:
                print(f'❌ Erro ao carregar {filename}: {e}')

async def main():
    async with bot:
        await load_extensions()
        await bot.start(TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("👋 Bot desligado pelo usuário.")