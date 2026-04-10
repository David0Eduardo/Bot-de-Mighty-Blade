import json
import os
import re
import io
import discord

DATA_FILE = "campanha_data.json"

def carregar_dados():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding='utf-8') as f:
                return json.load(f)
        except:
            return {"config": {}, "partys": {}, "personagens": {}, "gold_partys": {}}
    return {"config": {}, "partys": {}, "personagens": {}, "gold_partys": {}}

def salvar_dados(dados):
    with open(DATA_FILE, "w", encoding='utf-8') as f:
        json.dump(dados, f, indent=4, ensure_ascii=False)

def buscar_inicial_xp(texto):
    xp_match = re.search(r"Experiência\s+(\d+)/(\d+)", texto)
    return (int(xp_match.group(1)), int(xp_match.group(2))) if xp_match else None

def buscar_inicial_lvl(texto):
    lvl_match = re.search(r"Nível\s+(\d+)", texto)
    return int(lvl_match.group(1)) if lvl_match else None

async def eh_mestre(ctx):
    dados = carregar_dados()
    guild_id = str(ctx.guild.id)
    config = dados["config"].get(guild_id, {})
    role_id = config.get("mestre_role")
    if role_id and any(r.id == int(role_id) for r in ctx.author.roles):
        return True
    return ctx.author.guild_permissions.administrator

async def editar_e_substituir_arquivos(thread, p, bot_user):
    """Localiza o backup pela TAG de segurança e atualiza os arquivos físicos (Versão TXT apenas)."""
    mensagem_backup = None
    async for message in thread.history(limit=50):
        if message.author.id == bot_user.id and "SISTEMA_BACKUP_ID" in message.content:
            mensagem_backup = message
            break

    if not mensagem_backup:
        print(f"[AVISO] Backup não encontrado para {p['nome']} na thread.")
        return False

    raw_txt = ""

    for attachment in mensagem_backup.attachments:
        try:
            if attachment.filename.endswith(".txt"):
                content = await attachment.read()
                raw_txt = content.decode('utf-8')
        except Exception as e:
            print(f"[ERRO] Falha ao ler anexo {attachment.filename}: {e}")
            continue

    # Modifica o TXT mantendo a formatação original
    if raw_txt:
        raw_txt = re.sub(r"(Nível\s+)\d+", f"\\g<1>{p['lvl']}", raw_txt)
        raw_txt = re.sub(r"(Experiência\s+)\d+/\d+", f"\\g<1>{p['xp']}/{p['limite_xp']}", raw_txt)
        
        if "Moedas" in raw_txt:
            raw_txt = re.sub(r"(Moedas\s+)\d+", f"\\g<1>{p['ouro']}", raw_txt)
        else:
            raw_txt += f"\nMoedas {p['ouro']}"

    try:
        files = [
            discord.File(io.BytesIO(raw_txt.encode('utf-8')), filename=f"{p['nome']}.txt")
        ]
        await mensagem_backup.delete()
        await thread.send(content=f"💾 **BACKUP ATUALIZADO (Nível {p['lvl']}):**\n`SISTEMA_BACKUP_ID`", files=files)
        print(f"[SUCESSO] Arquivo TXT atualizado para {p['nome']}.")
        return True
    except Exception as e:
        print(f"[ERRO] Falha ao enviar arquivo para {p['nome']}: {e}")
        return False
    
def extrair_dados_txt(conteudo):
    linhas = [l.strip() for l in conteudo.split('\n') if l.strip()]
    nome = linhas[1].split(',')[0].strip() if len(linhas) > 1 else "Desconhecido"
    
    # Extrai Raça e Classe da linha 4 (index 3), removendo caracteres especiais como ♀♂
    raca = "Desconhecida"
    classe = "Desconhecida"
    caminho = "Nenhum"
    aprendiz = "Nenhum"
    
    if len(linhas) > 3:
        # Remove símbolos especiais (♀, ♂, etc)
        linha_raca_classe = re.sub(r'[♀♂]', '', linhas[3]).strip()
        palavras = linha_raca_classe.split()
        raca = palavras[0].strip() if len(palavras) > 0 else "Desconhecida"
        classe = palavras[1].strip().rstrip(',') if len(palavras) > 1 else "Desconhecida"
    
    # Procura por "caminho" e "aprendiz" até encontrar "[Geral]"
    for i, linha in enumerate(linhas):
        if "[Geral]" in linha:
            break
        
        if "caminho" in linha.lower() and " de " in linha:
            caminho_raw = linha
            caminho = caminho_raw.split(" de ")[-1].strip()
        
        if "aprendiz" in linha.lower() and " de " in linha:
            aprendiz_raw = linha
            aprendiz = aprendiz_raw.split(" de ")[-1].strip().rstrip(',')
    
    
    blocos = conteudo.split("----------")
    corpo_principal = blocos[0]
    
    def buscar(padrao, texto):
        match = re.search(padrao, texto)
        return match.group(1).strip() if match else "?"
    
    idiomas = []
    m_idiomas = re.search(r"Idiomas\s+(.+)", corpo_principal, re.IGNORECASE)
    if m_idiomas:
        idiomas = [i.strip() for i in m_idiomas.group(1).split(",") if i.strip()]

    ataques_p = []
    sec_atq_p = re.search(r"\[Ataques\]\n(.*?)(?=\n\n|\[|\Z)", corpo_principal, re.DOTALL)
    if sec_atq_p:
        linhas = sec_atq_p.group(1).strip().split('\n')
        ataques_p = [l.strip() for l in linhas if l.strip()]

    hab_personagem = []
    secoes_hab = re.findall(r"\[Habilidades.*?\]\n(.*?)(?=\n\n|\[Equipamentos|----------|\Z)", corpo_principal, re.DOTALL)
    for secao in secoes_hab:
        for linha in secao.strip().split('\n'):
            linha = linha.strip()
            if not linha: continue
            if '[' in linha or '(' in linha:
                # Captura tudo até encontrar um "[" ou "("
                # Se houver ":", ele será incluído no nome_hab
                match_hab = re.match(r"([^(\[]+)", linha)
                if match_hab:
                    nome_hab = match_hab.group(1).strip()
                    
                    # Filtro para evitar pegar lixo ou strings muito curtas
                    if nome_hab and len(nome_hab) > 2:
                        hab_personagem.append(nome_hab)

    itens_lista = []
    equip_match = re.search(r"Equipamentos.*?\n(.*?)(\n\n|----------|\Z)", corpo_principal, re.DOTALL)
    if equip_match:
        for linha in equip_match.group(1).strip().split('\n'):
            if '.' in linha:
                n = re.search(r"\.(.*?)(?:\s{2,}|x\d+)", linha)
                qtd = re.search(r"x(\d+)", linha)
                itens_lista.append(f"{n.group(1).strip() if n else 'Item'} (x{qtd.group(1) if qtd else '1'})")

    stats_p = {
        "vida": buscar(r"Pontos de Vida\s+([\d/]+)", corpo_principal),
        "mana": buscar(r"Pontos de Mana\s+([\d/]+)", corpo_principal),
        "for": buscar(r"Força\s+(\d+)", corpo_principal),
        "agi": buscar(r"Agilidade\s+(\d+)", corpo_principal),
        "int": buscar(r"Inteligência\s+(\d+)", corpo_principal),
        "von": buscar(r"Vontade\s+(\d+)", corpo_principal),
        "iniciativa": buscar(r"Iniciativa\s+(.+)", corpo_principal),
        "bloqueio": buscar(r"Bloqueio\s+(\d+)", corpo_principal),
        "esquiva": buscar(r"Esquiva\s+(\d+)", corpo_principal),
        "determinacao": buscar(r"Determinação\s+(\d+)", corpo_principal),
        "ouro": buscar(r"Moedas\s+(\d+)", corpo_principal),
        "idiomas": idiomas,
        "raca": raca,
        "classe": classe,
        "caminho": caminho,
        "aprendiz": aprendiz,
        "ataques": ataques_p,
        "itens": itens_lista,
        "habilidades": list(dict.fromkeys(hab_personagem))
    }

    pets_info = []
    for bloco in blocos[1:]:
        if not bloco.strip() or "Vida" not in bloco: continue
        nome_pet = bloco.strip().split('\n')[0].strip()
        sec_atq = re.search(r"Ataques.*?\n(.*?)(?=\nHabilidades|----------|\Z)", bloco, re.DOTALL)
        sec_hab = re.search(r"Habilidades.*?\n(.*?)(?=\n----------|\Z)", bloco, re.DOTALL)
        atq = [a.strip() for a in re.findall(r"^\.([^.\n]+?\+\d+)", sec_atq.group(1), re.MULTILINE)] if sec_atq else []
        h_pet = []
        if sec_hab:
            res_h = re.findall(r"^\.([^.\n:\[]+)", sec_hab.group(1), re.MULTILINE)
            h_pet = [h.strip() for h in res_h if h.strip() and "Tipo" not in h and "Tamanho" not in h]

        info = (
            f"**{nome_pet}** HP: {buscar(r'\.Vida:\s+(\d+)', bloco)} | MP: {buscar(r'\.Mana:\s+(\d+)', bloco)}\n"
            f"FOR {buscar(r'\.Força:\s+(\d+)', bloco)} AGI {buscar(r'\.Agilidade:\s+(\d+)', bloco)} INT {buscar(r'\.Inteligência:\s+(\d+)', bloco)} VON {buscar(r'\.Vontade:\s+(\d+)', bloco)}\n"
            f"Bloqueio: {buscar(r'\.Bloqueio:\s+(\d+)', bloco)} | Esquiva: {buscar(r'\.Esquiva:\s+(\d+)', bloco)} | Determinação: {buscar(r'\.Determinação:\s+(\d+)', bloco)}\n"
            f"**Ataques:** {', '.join(atq)}\n**Habilidades:** {', '.join(h_pet)}"
        )
        pets_info.append(info)
    
    return nome, stats_p, pets_info

async def atualizar_ficha_jogador(ctx, uid, dados):
    guild_id = str(ctx.guild.id)
    p = dados["personagens"][guild_id].get(uid)
    if not p or not p.get("thread_id"): return
    try:
        thread = await ctx.guild.fetch_channel(int(p["thread_id"]))
        async for message in thread.history(limit=20, oldest_first=True):
            if "🛡️ **FICHA DE PERSONAGEM:" in message.content:
                linhas = message.content.split('\n')
                linhas[0] = f"🛡️ **FICHA DE PERSONAGEM: {p['nome']}**"
                linhas[1] = f"👤 **Jogador:** <@{uid}> | **Nível:** {p['lvl']} | ✨ **XP:** {p['xp']}/{p['limite_xp']}"
                linhas[2] = f"💰 **Ouro:** {p.get('ouro', 0)} | ❤️ **Vida:** {p['hp']} | ⚡ **Mana:** {p['mp']}"
                await message.edit(content='\n'.join(linhas))
                break
    except: pass

async def processar_ganho_xp(ctx, guild_id, uid, valor_xp, dados, bot_user):
    if uid not in dados["personagens"].get(guild_id, {}): return
    p = dados["personagens"][guild_id][uid]
    p["xp"] += valor_xp
    leveled_up = False
    while p["xp"] >= p["limite_xp"]:
        p["xp"] -= p["limite_xp"]
        p["lvl"] += 1
        leveled_up = True
    salvar_dados(dados)
    await atualizar_ficha_jogador(ctx, uid, dados)
    tid = p.get("thread_id")
    if tid:
        try:
            thread = await ctx.guild.fetch_channel(int(tid))
            await editar_e_substituir_arquivos(thread, p, bot_user)
        except: pass
    if leveled_up:
        await ctx.send(f"🎊 **LEVEL UP!** {p['nome']} subiu para o **Nível {p['lvl']}**!")

async def atualizar_painel_mestre(ctx, dados):
    guild_id = str(ctx.guild.id)
    last_msg_id = dados["config"].get(guild_id, {}).get("last_status_msg")
    personagens = dados.get("personagens", {}).get(guild_id, {})
    partys = dados.get("partys", {}).get(guild_id, {})
    
    msg_tabela = "📋 **STATUS ATUAL DA CAMPANHA**\n```\n"
    msg_tabela += f"{'Personagem':<12} | {'Jogador':<12} | {'LVL':<3} | {'PV':<6} | {'PM':<6} | {'XP':<6} | {'Party':<10}\n"
    msg_tabela += "-" * 88 + "\n"
    
    for uid, info in personagens.items():
        membro = ctx.guild.get_member(int(uid))
        nick = membro.display_name[:12] if membro else info.get("player", "???")[:12]
        nome_party = "---"
        for p_nome, membros in partys.items():
            if uid in membros:
                nome_party = p_nome[:10]
                break
        msg_tabela += f"{info.get('nome','?'):<12} | {nick:<12} | {info.get('lvl',1):<3} | {info.get('hp','?'):<6} | {info.get('mp','?'):<6} | {info.get('xp',0)}/{info.get('limite_xp',10):<3} | {nome_party:<10}\n"
    msg_tabela += "```"

    if last_msg_id:
        # Procura a thread "📊 PAINEL DO MESTRE" e a mensagem dentro dela
        for channel in ctx.guild.text_channels:
            if not isinstance(channel, discord.TextChannel):
                continue
            try:
                # Procura threads ativas e arquivadas
                async for thread in channel.archived_threads():
                    if thread.name == "📊 PAINEL DO MESTRE":
                        try:
                            msg = await thread.fetch_message(last_msg_id)
                            await msg.edit(content=msg_tabela)
                            return True
                        except:
                            pass
                
                # Procura threads ativas
                for thread in channel.threads:
                    if thread.name == "📊 PAINEL DO MESTRE":
                        try:
                            msg = await thread.fetch_message(last_msg_id)
                            await msg.edit(content=msg_tabela)
                            return True
                        except:
                            pass
            except:
                continue
    return False