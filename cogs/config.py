import discord
from discord.ext import commands
from discord import app_commands
import utils

class Config(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _salvar_config_canal(self, guild_id: int, canal_id: int):
        dados = utils.carregar_dados()
        gid = str(guild_id)

        if "config" not in dados:
            dados["config"] = {}

        if gid not in dados["config"]:
            dados["config"][gid] = {}

        dados["config"][gid]["canal_fichas"] = canal_id
        utils.salvar_dados(dados)

    @app_commands.command(
        name="setfichas",
        description="Define o canal onde os tópicos de ficha estão localizados"
    )
    @app_commands.describe(canal="O canal de texto que contém os tópicos das fichas")
    @app_commands.checks.has_permissions(administrator=True)
    async def setfichas_slash(self, interaction: discord.Interaction, canal: discord.TextChannel):
        self._salvar_config_canal(interaction.guild_id, canal.id)
        await interaction.response.send_message(
            f"✅ **Configuração Salva!**\nAgora buscarei os tópicos de ficha em: {canal.mention}",
            ephemeral=True
        )

    @commands.command(name="setfichas", aliases=["set_fichas"])
    @commands.has_permissions(administrator=True)
    async def setfichas_prefix(self, ctx, canal: discord.TextChannel = None):
        alvo = canal or ctx.channel
        self._salvar_config_canal(ctx.guild.id, alvo.id)
        await ctx.send(f"✅ **Canal de fichas definido!**\nLocal: {alvo.mention}")

    @app_commands.command(
        name="ver_configs",
        description="Exibe as configurações atuais do servidor"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def ver_configs_slash(self, interaction: discord.Interaction):
        dados = utils.carregar_dados()
        gid = str(interaction.guild_id)
        config = dados.get("config", {}).get(gid, {})

        embed = discord.Embed(title="⚙️ Configurações do Servidor", color=discord.Color.blue())
        canal_fichas = config.get("canal_fichas")
        valor_canal = f"<#{canal_fichas}>" if canal_fichas else "Não definido"
        embed.add_field(name="Canal de Fichas", value=valor_canal, inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(Config(bot))