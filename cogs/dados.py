import discord
from discord.ext import commands
import random
import re
import utils
import json
import os


class Dados(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.arquivo_carma = "carma_stats.json"
        self.historico_carma = self._carregar_carma()

    def _carregar_carma(self):
        if os.path.exists(self.arquivo_carma):
            try:
                with open(self.arquivo_carma, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _salvar_carma(self):
        with open(self.arquivo_carma, "w", encoding="utf-8") as f:
            json.dump(self.historico_carma, f)

    def rolar_carma(self, faces, nome_ator):
        if not nome_ator:
            nome_ator = "desconhecido"
        if nome_ator not in self.historico_carma:
            self.historico_carma[nome_ator] = []

        historico = self.historico_carma[nome_ator]
        metade = faces * 0.4
        azarado = len(historico) >= 3 and all(r < metade for r in historico[-3:])

        if azarado:
            resultado = random.randint(int(metade) + 1, faces)
            carmico = True
        else:
            resultado = random.randint(1, faces)
            carmico = False

        historico.append(resultado)
        self.historico_carma[nome_ator] = historico[-5:]
        self._salvar_carma()
        return resultado, carmico

    async def obter_topico_ficha(self, message, nome_personagem):
        dados = utils.carregar_dados()
        gid = str(message.guild.id)
        canal_id = dados.get("config", {}).get(gid, {}).get("canal_fichas")
        if not canal_id:
            return None, "Canal de fichas não configurado."

        canal = self.bot.get_channel(int(canal_id))
        topico = discord.utils.get(canal.threads, name=f"Ficha: {nome_personagem}")
        return topico, None if topico else f"Ficha de {nome_personagem} não encontrada."

    async def ler_txt(self, topico):
        async for msg in topico.history(limit=10):
            if msg.attachments:
                for att in msg.attachments:
                    if att.filename.endswith(".txt"):
                        return (await att.read()).decode("utf-8")
        return None

    def _normalizar_termo(self, termo):
        termo = (termo or "").strip()
        atalhos = {
            "for": "Força",
            "agi": "Agilidade",
            "int": "Inteligência",
            "von": "Vontade",
            "esq": "Esquiva",
            "bloq": "Bloqueio",
        }
        return atalhos.get(termo.lower(), termo)

    def _termos_equivalentes(self, termo):
        termo = (termo or "").strip()
        if not termo:
            return []

        equivalencias = {
            "for": ["for", "força"],
            "agi": ["agi", "agilidade"],
            "int": ["int", "inteligência", "inteligencia"],
            "von": ["von", "vontade"],
            "esq": ["esq", "esquiva"],
            "bloq": ["bloq", "bloqueio"],
            "força": ["for", "força"],
            "agilidade": ["agi", "agilidade"],
            "inteligência": ["int", "inteligência", "inteligencia"],
            "inteligencia": ["int", "inteligência", "inteligencia"],
            "vontade": ["von", "vontade"],
            "esquiva": ["esq", "esquiva"],
            "bloqueio": ["bloq", "bloqueio"],
        }

        base = termo.lower()
        return equivalencias.get(base, [base])

    def _extrair_numero_de_linha(self, linha):
        if not linha:
            return None

        bonus = re.search(r"(?<!\w)([+\-]\d+)(?!\w)", linha)
        if bonus:
            return int(bonus.group(1))

        nums = re.findall(r"(?<!\w)(\d+)(?!\w)", linha)
        if nums:
            return int(nums[-1])

        return None

    def _buscar_bonus_em_bloco(self, bloco, termo):
        termo_base = self._normalizar_termo(termo)
        equivalentes = self._termos_equivalentes(termo)
        linhas = [ln.strip(" .\t") for ln in bloco.splitlines() if ln.strip()]

        # 1) atributos/defesas em linha própria
        for linha in linhas:
            linha_limpa = linha.strip(" .")
            for eq in equivalentes:
                m = re.search(
                    rf"(?i)^(?:{re.escape(eq)}|{re.escape(termo_base)})\s*[:=]?\s*([+\-]?\d+)\s*$",
                    linha_limpa
                )
                if m:
                    valor = int(m.group(1))
                    valor_fmt = str(valor) if str(valor).startswith(("+", "-")) else f"+{valor}"
                    return valor, f"{termo_base}({valor_fmt})"

        # 2) linha contendo o termo (armas/ataques/equipamentos)
        candidatos = []
        for linha in linhas:
            linha_limpa = linha.strip(" .")
            linha_norm = linha_limpa.lower()
            if any(eq in linha_norm for eq in equivalentes):
                valor = self._extrair_numero_de_linha(linha_limpa)
                if valor is not None:
                    candidatos.append((linha_limpa, valor))

        if candidatos:
            candidatos.sort(key=lambda item: len(item[0]))
            linha, valor = candidatos[0]
            valor_fmt = str(valor) if str(valor).startswith(("+", "-")) else f"+{valor}"
            return valor, f"{termo_base}({valor_fmt})"

        return 0, "Sem bônus"

    async def pegar_bonus(self, txt, termo):
        if not termo or not termo.strip():
            return 0, "Sem bônus"
        return self._buscar_bonus_em_bloco(txt, termo)

    async def extrair_bloco_pet(self, txt, nome_pet):
        nome_pet = (nome_pet or "").strip().lower()
        if not nome_pet:
            return None

        linhas = txt.splitlines()
        inicio = None

        # Procura a linha que contém o nome do pet
        for i, linha in enumerate(linhas):
            linha_limpa = linha.strip(" .\t").lower()
            if nome_pet in linha_limpa:
                inicio = i
                break

        if inicio is None:
            return None

        # Sobe até o último separador ----- antes do nome, se houver
        topo = inicio
        for i in range(inicio - 1, -1, -1):
            if re.fullmatch(r"\s*-{5,}\s*", linhas[i]):
                topo = i + 1
                break

        # Desce até o próximo separador -----, se houver
        fim = len(linhas)
        for j in range(inicio + 1, len(linhas)):
            if re.fullmatch(r"\s*-{5,}\s*", linhas[j]):
                fim = j
                break

        bloco = "\n".join(linhas[topo:fim]).strip()
        return bloco or None

    async def processar_pet(self, txt, nome_pet, termo):
        bloco_pet = await self.extrair_bloco_pet(txt, nome_pet)
        if not bloco_pet:
            return None, f"Pet '{nome_pet}' não encontrado na ficha."

        if not termo or not termo.strip():
            return {"bonus": 0, "tipo": "Sem bônus"}, None

        bonus, tipo = self._buscar_bonus_em_bloco(bloco_pet, termo)
        return {"bonus": bonus, "tipo": tipo}, None

    def _parse_comando(self, texto):
        texto = (texto or "").strip()
        alvo = ""
        if "/" in texto:
            texto, alvo = texto.split("/", 1)
            texto = texto.strip()
            alvo = alvo.strip()

        nome_pet = ""
        pet_match = re.search(r"\s+-p\s+(.+)$", texto, re.IGNORECASE)
        if pet_match:
            nome_pet = pet_match.group(1).strip()
            texto = texto[:pet_match.start()].strip()

        regex = r"^(atk|ataque|dano)?\s*(\d+)d(\d+)([\+\-]\d+)?(?:\s+(.+))?$"
        match = re.match(regex, texto, re.IGNORECASE)
        if not match:
            return None

        prefixo = (match.group(1) or "").lower()
        qtd = int(match.group(2))
        faces = int(match.group(3))
        bonus_manual = int((match.group(4) or "0").replace("+", ""))
        termo_raw = (match.group(5) or "").strip()

        termo = termo_raw
        m = re.match(r"^(.*?)\s*([\+\-]\d+)$", termo_raw)
        if m:
            termo = m.group(1).strip()
            bonus_manual += int(m.group(2))

        return {
            "prefixo": prefixo,
            "qtd": qtd,
            "faces": faces,
            "bonus_manual": bonus_manual,
            "termo": termo,
            "nome_pet_alvo": nome_pet,
            "alvo": alvo,
        }

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
        if not message.guild:
            return

        parsed = self._parse_comando(message.content)
        if not parsed:
            return

        try:
            prefixo = parsed["prefixo"]
            qtd = parsed["qtd"]
            faces = parsed["faces"]
            bonus_manual = parsed["bonus_manual"]
            termo = parsed["termo"]
            nome_pet_alvo = parsed["nome_pet_alvo"]
            alvo = parsed["alvo"]

            dados_rpg = utils.carregar_dados()
            gid, uid = str(message.guild.id), str(message.author.id)
            nome_char = dados_rpg.get("personagens", {}).get(gid, {}).get(uid, {}).get("nome")

            topico, erro = await self.obter_topico_ficha(message, nome_char)
            if erro:
                return await message.channel.send(f"❌ {erro}")

            txt = await self.ler_txt(topico)
            if not txt:
                return await message.channel.send("❌ Ficha não encontrada.")

            bonus_ficha = 0
            tipo = "Sem bônus"
            nome_exibicao = nome_char

            if nome_pet_alvo:
                res_pet, erro_pet = await self.processar_pet(txt, nome_pet_alvo, termo)
                if erro_pet:
                    return await message.channel.send(f"❌ {erro_pet}")
                bonus_ficha = res_pet["bonus"]
                tipo = res_pet["tipo"]
                nome_exibicao = f"🐾 {nome_pet_alvo}"
            else:
                if termo:
                    bonus_ficha, tipo = await self.pegar_bonus(txt, termo)

            rolagens = []
            for _ in range(qtd):
                val, _carm = self.rolar_carma(faces, f"{uid}_{nome_exibicao}")
                rolagens.append(val)

            soma = sum(rolagens)
            total = soma + bonus_manual + bonus_ficha

            embed = discord.Embed(title="🎲 Rolagem", color=0x2b2d31)
            embed.add_field(name="Ator", value=nome_exibicao, inline=True)
            if alvo:
                embed.add_field(name="Alvo", value=alvo, inline=True)

            partes = [f"{rolagens} = {soma}"]
            if bonus_manual:
                partes.append(f"{bonus_manual} (Manual)")
            if bonus_ficha:
                partes.append(f"{bonus_ficha} ({tipo})")

            embed.add_field(
                name="Resultado",
                value=" + ".join(partes) + f" = **{total}**",
                inline=False,
            )

            await message.channel.send(embed=embed)

            if alvo:
                if prefixo in ["atk", "ataque", ""]:
                    self.bot.dispatch("tentativa_ataque", alvo, total, int(gid), message.channel)
                elif prefixo == "dano":
                    self.bot.dispatch("tentativa_dano", alvo, total, int(gid), message.channel)

        except Exception as e:
            print(f"Erro em Dados: {e}")


async def setup(bot):
    await bot.add_cog(Dados(bot))
