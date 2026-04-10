import discord
from discord.ext import commands
import json
import io
from utils import (
    carregar_dados, salvar_dados, extrair_dados_txt, buscar_inicial_xp, 
    buscar_inicial_lvl, eh_mestre, processar_ganho_xp, atualizar_ficha_jogador, atualizar_painel_mestre, editar_e_substituir_arquivos
)


class MestreCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="sync", description="Sincroniza os comandos slash com o Discord (administrador).")
    @commands.has_permissions(administrator=True)
    async def sync(self, ctx):
        """Sincroniza os comandos com o Discord."""
        try:
            fmt = await self.bot.tree.sync()
            await ctx.send(f"✅ Sincronizados {len(fmt)} comandos com o Discord!")
        except Exception as e:
            await ctx.send(f"❌ Erro na sincronia: {e}")

    @commands.hybrid_command(name="mestre", description="Define o cargo de mestre e cria o painel de controle.")
    @commands.has_permissions(administrator=True)
    async def mestre(self, ctx, role: discord.Role):
        """Configura o cargo de mestre e reseta o painel."""
        await ctx.send(f"✅ Configurando cargo {role.mention} e preparando painel...", delete_after=5)
        dados = carregar_dados()
        guild_id = str(ctx.guild.id)
        
        # Deleta painéis antigos se existirem
        for thread in ctx.channel.threads:
            if thread.name == "📊 PAINEL DO MESTRE": 
                await thread.delete()
                
        if guild_id not in dados["config"]: 
            dados["config"][guild_id] = {}
            
        dados["config"][guild_id]["mestre_role"] = role.id
        salvar_dados(dados)
        
        await ctx.send(f"✅ Cargo {role.mention} definido. Criando painel...")
        # Chama o comando gerenciar internamente na Cog
        await ctx.invoke(self.gerenciar)

    @commands.hybrid_command(name="gerenciar", description="Cria ou atualiza o painel de controle do mestre.")
    async def gerenciar(self, ctx):
        """Cria ou atualiza o Painel do Mestre."""
        if not await eh_mestre(ctx): return
        dados = carregar_dados()
        guild_id = str(ctx.guild.id)
        
        if isinstance(ctx.channel, discord.Thread) and ctx.channel.name == "📊 PAINEL DO MESTRE":
            if await atualizar_painel_mestre(ctx, dados):
                return await ctx.send("✅ **Tabela e Nicks atualizados com sucesso!**", delete_after=3)

        role_id = dados["config"].get(guild_id, {}).get("mestre_role")
        role_m = ctx.guild.get_role(role_id) if role_id else None

        try:
            base_channel = ctx.channel.parent if isinstance(ctx.channel, discord.Thread) else ctx.channel
            thread = await base_channel.create_thread(name="📊 PAINEL DO MESTRE", type=discord.ChannelType.private_thread)
            await thread.add_user(ctx.author)
            if role_m:
                for m in role_m.members: await thread.add_user(m)

            guia = (
                f"⚔️ **PAINEL DE CONTROLE DO MESTRE ({role_m.mention if role_m else 'Mestre'})**\n"
                "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
                "✨ **SISTEMA DE XP E LEVEL UP:**\n"
                "▫️ `/xp @jogador <valor>` - Dá XP individual.\n"
                "▫️ `/party_recompensa <nome> <valor>` - XP para todos da Party.\n\n"
                "💰 **GESTÃO DE TESOURO (GOLD):**\n"
                "▫️ `/gold <nome_party> <valor>` - Adiciona ouro ao Cofre da party.\n"
                "▫️ `/gold_div <nome_party>` - Divide o ouro e atualiza Fichas/Threads.\n\n"
                "🛡️ **GESTÃO DE PARTYS (Máx: 1 por Jogador):**\n"
                "▫️ `/criar_party <nome> @membros` - Cria nova party (remove membros de antigas).\n"
                "▫️ `/party_ent <nome> @jogador` - Adiciona jogador a uma party existente.\n"
                "▫️ `/lista_partys` - Lista todos os grupos registrados.\n"
                "▫️ `/party_remover_membro <nome> @jogador` - Remove alguém de um grupo.\n"
                "▫️ `/remover_party <nome>` - Exclui a party e seu cofre.\n\n"
                "📊 **UTILITÁRIOS:**\n"
                "▫️ `/gerenciar` - Sincroniza Nicks e atualiza esta tabela.\n"
                "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬"
            )
            
            await thread.send(guia)
            sent_msg = await thread.send("📋 **STATUS ATUAL DA CAMPANHA**\n*(Carregando dados...)*")
            
            dados["config"][guild_id]["last_status_msg"] = sent_msg.id
            salvar_dados(dados)
            await atualizar_painel_mestre(ctx, dados)
            
        except Exception as e:
            await ctx.send(f"❌ Erro ao criar painel: {e}")

    @commands.hybrid_command(name="registrar", description="Registra um personagem anexando o arquivo .txt da ficha.")
    async def registrar(self, ctx):
        if len(ctx.message.attachments) != 1 or not ctx.message.attachments[0].filename.endswith('.txt'): 
            return await ctx.send("❌ Envie apenas o arquivo .txt!  (Certifique-se de usar '!' e não '/')")
        f_txt = ctx.message.attachments[0]
        raw_txt = await f_txt.read()
        txt_str = raw_txt.decode('utf-8')
        
        nome, stats, pets = extrair_dados_txt(txt_str)
        xp_dados = buscar_inicial_xp(txt_str)
        xp_at, lim_at = (xp_dados[0], xp_dados[1]) if xp_dados else (0, 10)
        lvl_at = buscar_inicial_lvl(txt_str) or 1
        
        dados = carregar_dados()
        gid, uid = str(ctx.guild.id), str(ctx.author.id)
        if gid not in dados["personagens"]: dados["personagens"][gid] = {}
        
        old_tid = dados["personagens"][gid].get(uid, {}).get("thread_id")
        
        # Formata a lista de ataques do personagem com quebra de linha
        lista_ataques = "\n".join(stats['ataques']) if stats['ataques'] else "Nenhum"

        idiomas_txt = ", ".join(stats.get("idiomas", [])) if isinstance(stats.get("idiomas"), list) else stats.get("idiomas", "Nenhum")

        resumo = (f"🛡️ **FICHA DE PERSONAGEM: {nome}**\n"
                f"👤 **Jogador:** {ctx.author.mention} | **Raça:** {stats['raca']} | **Classe:** {stats['classe']}\n"
                f"📚 **Aprendizado:** {stats.get('aprendiz', 'Nenhum')} | 🛤️ **Caminho:** {stats.get('caminho', 'Nenhum')}\n"
                f"🗣️ **Idiomas:** {idiomas_txt}\n"
                f"📊 **Nível:** {lvl_at} | ✨ **XP:** {xp_at}/{lim_at}\n"
                f"💰 **Ouro:** {stats['ouro']} | ❤️ **Vida:** {stats['vida']} | ⚡ **Mana:** {stats['mana']}\n"
                f"📊 **Atributos:** FOR {stats['for']} | AGI {stats['agi']} | INT {stats['int']} | VON {stats['von']}\n"
                f"🎲 **Iniciativa:** {stats['iniciativa']}\n"
                f"🛡️ **Defesas:** Bloqueio: {stats['bloqueio']} | Esquiva: {stats['esquiva']} | Determinação: {stats['determinacao']}\n"
                f"⚔️ **ATAQUES:**\n{lista_ataques}\n"
                f"\n**HABILIDADES:**\n{', '.join(stats['habilidades'])}\n"
                f"\n**INVENTÁRIO:**\n" + "\n".join(stats['itens']))
        
        if old_tid:
            try:
                thread = await ctx.guild.fetch_channel(int(old_tid))
                # Atualiza a ficha
                async for message in thread.history(limit=20, oldest_first=True):
                    if "🛡️ **FICHA DE PERSONAGEM:" in message.content:
                        await message.edit(content=resumo)
                        break
                # Atualiza o backup usando o TXT novo enviado pelo usuário
                mensagem_backup = None
                async for message in thread.history(limit=50):
                    if message.author.id == self.bot.user.id and "SISTEMA_BACKUP_ID" in message.content:
                        mensagem_backup = message
                        break

                if mensagem_backup:
                    try:
                        await mensagem_backup.delete()
                    except Exception as e:
                        print(f"Erro ao deletar backup antigo: {e}")

                await thread.send(
                    content="💾 **BACKUP ATUALIZADO:**\n`SISTEMA_BACKUP_ID`",
                    files=[discord.File(io.BytesIO(raw_txt), f"{nome}.txt")]
                )
            except Exception as e:
                print(f"Erro ao atualizar thread existente: {e}")
                # Cria nova thread se falhar
                thread = await ctx.channel.create_thread(name=f"Ficha: {nome}", type=discord.ChannelType.private_thread)
                await thread.add_user(ctx.author)
                await thread.send(resumo)
                if pets:
                    await thread.send("**▬▬▬▬▬▬▬▬▬▬ ALIADOS & PETS ▬▬▬▬▬▬▬▬▬▬**")
                    for i, p_info in enumerate(pets):
                        await thread.send(p_info)
                        if i < len(pets) - 1:
                            await thread.send("--------------------------------------------------")
                await thread.send(content="**BACKUP INICIAL**\n`SISTEMA_BACKUP_ID`", files=[discord.File(io.BytesIO(raw_txt), f"{nome}.txt")])
        else:
            thread = await ctx.channel.create_thread(name=f"Ficha: {nome}", type=discord.ChannelType.private_thread)
            await thread.add_user(ctx.author)
            await thread.send(resumo)
            if pets:
                await thread.send("**▬▬▬▬▬▬▬▬▬▬ ALIADOS & PETS ▬▬▬▬▬▬▬▬▬▬**")
                for i, p_info in enumerate(pets):
                    await thread.send(p_info)
                    if i < len(pets) - 1:
                        await thread.send("--------------------------------------------------")
            await thread.send(content="**BACKUP INICIAL**\n`SISTEMA_BACKUP_ID`", files=[discord.File(io.BytesIO(raw_txt), f"{nome}.txt")])
        
        dados["personagens"][gid][uid] = {
            "nome": nome, "player": ctx.author.display_name, "lvl": lvl_at, "xp": xp_at,
            "limite_xp": lim_at, "hp": stats["vida"], "mp": stats["mana"], 
            "ouro": int(stats["ouro"]) if str(stats["ouro"]).isdigit() else 0, "thread_id": thread.id,
            "iniciativa": stats.get("iniciativa", "0"), "caminho": stats.get("caminho", "Nenhum"),
            "aprendiz": stats.get("aprendiz", "Nenhum")
        }
        salvar_dados(dados)
        await ctx.send(f"✅ Ficha de **{nome}** sincronizada!")
        await atualizar_painel_mestre(ctx, dados)

    @commands.hybrid_command(name="limite", description="Ajusta o limite de XP e nível de um jogador (mestre).")
    async def limite(self, ctx, alvo: discord.Member, novo_limite: int, novo_lvl: int = None):
        if not await eh_mestre(ctx): return
        dados = carregar_dados()
        gid, uid = str(ctx.guild.id), str(alvo.id)
        if uid not in dados["personagens"].get(gid, {}): return await ctx.send("❌ Sem ficha.")
        dados["personagens"][gid][uid]["limite_xp"] = novo_limite
        if novo_lvl: dados["personagens"][gid][uid]["lvl"] = novo_lvl
        salvar_dados(dados)
        await atualizar_ficha_jogador(ctx, uid, dados)
        await atualizar_painel_mestre(ctx, dados)
        await ctx.send(f"✅ Alterado para {alvo.name}.", delete_after=5)

    @commands.hybrid_command(name="criar_party", description="Cria uma nova party com membros especificados (mestre).")
    async def criar_party(self, ctx, nome_party: str, membros: commands.Greedy[discord.Member]):
        if not await eh_mestre(ctx): return
        dados = carregar_dados()
        gid = str(ctx.guild.id)
        if gid not in dados["partys"]: dados["partys"][gid] = {}
        novos_ids = [str(m.id) for m in membros]
        dados["partys"][gid][nome_party] = novos_ids
        salvar_dados(dados)
        await ctx.send(f"✅ Party **{nome_party}** criada!")
        await atualizar_painel_mestre(ctx, dados)

    @commands.hybrid_command(name="party_ent", description="Adiciona um jogador a uma party existente (mestre).")
    async def party_ent(self, ctx, nome_party: str, alvo: discord.Member):
        if not await eh_mestre(ctx): return
        dados = carregar_dados()
        gid, uid = str(ctx.guild.id), str(alvo.id)
        if nome_party not in dados.get("partys", {}).get(gid, {}): return await ctx.send("❌ Party não existe.")
        for p_membros in dados["partys"][gid].values():
            if uid in p_membros: p_membros.remove(uid)
        dados["partys"][gid][nome_party].append(uid)
        salvar_dados(dados)
        await ctx.send(f"✅ {alvo.display_name} adicionado à party **{nome_party}**.")
        await atualizar_painel_mestre(ctx, dados)

    @commands.hybrid_command(name="lista_partys", description="Lista todas as partys registradas no servidor (mestre).")
    async def lista_partys(self, ctx):
        if not await eh_mestre(ctx): return
        dados = carregar_dados()
        partys = dados.get("partys", {}).get(str(ctx.guild.id), {})
        if not partys: return await ctx.send("📭 Nenhuma party.")
        msg = "📜 **PARTYS:**\n" + "\n".join([f"🔹 **{n}**: {len(m)} membros" for n, m in partys.items()])
        await ctx.send(msg)

    @commands.hybrid_command(name="remover_party", description="Remove uma party e seu cofre (mestre).")
    async def remover_party(self, ctx, nome_party: str):
        if not await eh_mestre(ctx): return
        dados = carregar_dados()
        gid = str(ctx.guild.id)
        if nome_party in dados.get("partys", {}).get(gid, {}):
            del dados["partys"][gid][nome_party]
            salvar_dados(dados)
            await ctx.send(f"✅ Party **{nome_party}** removida.")
            await atualizar_painel_mestre(ctx, dados)

    @commands.hybrid_command(name="party_remover_membro", description="Remove um membro de uma party (mestre).")
    async def party_remover_membro(self, ctx, nome_party: str, alvo: discord.Member):
        if not await eh_mestre(ctx): return
        dados = carregar_dados()
        gid, uid = str(ctx.guild.id), str(alvo.id)
        if nome_party in dados.get("partys", {}).get(gid, {}):
            if uid in dados["partys"][gid][nome_party]:
                dados["partys"][gid][nome_party].remove(uid)
                salvar_dados(dados)
                await ctx.send(f"✅ {alvo.display_name} removido da party **{nome_party}**.")
                await atualizar_painel_mestre(ctx, dados)

    @commands.hybrid_command(name="xp", description="Concede XP a um jogador (mestre).")
    async def xp(self, ctx, alvo: discord.Member, valor: int):
        if not await eh_mestre(ctx): return
        
        # Avisa ao Discord para esperar (evita o "aplicativo não respondeu")
        await ctx.defer(ephemeral=True) 
        
        dados = carregar_dados()
        gid, uid = str(ctx.guild.id), str(alvo.id)
        
        await processar_ganho_xp(ctx, gid, uid, valor, dados, self.bot.user)
        
        if uid in dados["personagens"].get(gid, {}):
            p = dados["personagens"][gid][uid]
            tid = p.get("thread_id")
            if tid:
                try:
                    thread = await self.bot.fetch_channel(int(tid))
                    await editar_e_substituir_arquivos(thread, p, self.bot.user)
                except Exception as e:
                    print(f"Erro ao acessar thread: {e}")

        await atualizar_painel_mestre(ctx, dados)
        # Usa followup porque o defer já foi chamado
        await ctx.followup.send(f"✅ XP enviado para {alvo.display_name} e arquivos atualizados!", ephemeral=True)

    @commands.hybrid_command(name="party_recompensa", description="Concede XP a todos os membros de uma party (mestre).")
    async def party_recompensa(self, ctx, nome_party: str, valor_xp: int):
        if not await eh_mestre(ctx): return
        dados = carregar_dados()
        gid = str(ctx.guild.id)
        party = dados.get("partys", {}).get(gid, {}).get(nome_party)
        if not party: return await ctx.send("❌ Party não encontrada.")
        for uid in party:
            await processar_ganho_xp(ctx, gid, uid, valor_xp, dados, self.bot.user)
        await ctx.send(f"🎉 **{valor_xp} XP** enviado para a party **{nome_party}**!")
        await atualizar_painel_mestre(ctx, dados)

    @commands.hybrid_command(name="gold", description="Adiciona ouro ao cofre de uma party (mestre).")
    async def gold(self, ctx, nome_party: str, valor_gold: int):
        if not await eh_mestre(ctx): return
        dados = carregar_dados()
        gid = str(ctx.guild.id)
        if "gold_partys" not in dados: dados["gold_partys"] = {}
        if gid not in dados["gold_partys"]: dados["gold_partys"][gid] = {}
        atual = dados["gold_partys"][gid].get(nome_party, 0)
        dados["gold_partys"][gid][nome_party] = atual + valor_gold
        salvar_dados(dados)
        await ctx.send(f"💰 Cofre de **{nome_party}**: **{atual + valor_gold}g**.")

    @commands.hybrid_command(name="gold_div", description="Divide o ouro do cofre da party entre os membros e atualiza fichas.")
    async def gold_div(self, ctx, nome_party: str):
        """Divide o ouro corrigindo o erro de atributo 'followup'."""
        if not await eh_mestre(ctx): return
        
        # Inicia o estado de espera (pode ser True ou False conforme sua preferência)
        await ctx.defer(ephemeral=True) 
        
        try:
            dados = carregar_dados()
            gid = str(ctx.guild.id)
            
            partys = dados.get("partys", {}).get(gid, {})
            if nome_party not in partys:
                return await ctx.send(f"❌ A party '{nome_party}' não existe.", ephemeral=True)

            membros = partys.get(nome_party, [])
            total_gold = dados.get("gold_partys", {}).get(gid, {}).get(nome_party, 0)
            
            if not membros or total_gold <= 0:
                return await ctx.send("❌ Cofre vazio ou sem membros.", ephemeral=True)
            
            divisao = total_gold // len(membros)
            
            for uid in membros:
                if uid in dados["personagens"].get(gid, {}):
                    dados["personagens"][gid][uid]["ouro"] += divisao
                    
                    p = dados["personagens"][gid][uid]
                    tid = p.get("thread_id")
                    
                    if tid:
                        try:
                            # Busca a thread/canal
                            channel = await self.bot.fetch_channel(int(tid))
                            # Função do utils (com suporte ao Editor de Runas)
                            await editar_e_substituir_arquivos(channel, p, self.bot.user)
                        except Exception as e:
                            print(f"[ERRO GOLD_DIV] Falha na thread de {p['nome']}: {e}")
                    
                    await atualizar_ficha_jogador(ctx, uid, dados)

            # Salva os dados globais
            dados["gold_partys"][gid][nome_party] = 0
            salvar_dados(dados)
            await atualizar_painel_mestre(ctx, dados)
            
            # RESPOSTA CORRETA: O ctx.send() resolve o "pensando" em comandos híbridos
            await ctx.send(f"💰 Sucesso! Cada membro recebeu {divisao}g e os arquivos foram atualizados.", ephemeral=True)

        except Exception as e:
            print(f"[ERRO CRÍTICO GOLD_DIV]: {e}")
            # Garante que o bot responda mesmo em erro para não travar o Discord
            try:
                await ctx.send(f"⚠️ Erro interno: {e}", ephemeral=True)
            except:
                pass
                        
async def setup(bot):
    await bot.add_cog(MestreCog(bot))        