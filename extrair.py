import os
import re
import sys
import json
from urllib.parse import urlparse, urljoin
import firebase_admin
from firebase_admin import credentials, db
from playwright.sync_api import sync_playwright

try:
    from playwright_stealth import stealth_sync
except ImportError:
    stealth_sync = None

FIREBASE_DB_URL = (
    os.environ.get("FIREBASE_DB_URL")
    or "https://k404repacks-pc-default-rtdb.firebaseio.com"
).rstrip("/")
FIREBASE_CREDENTIALS_JSON = os.environ.get("FIREBASE_CREDENTIALS_JSON")
ARQUIVO_JOGOS = "jogos.txt"


def obter_ref_firebase(id_jogo):
    """Inicializa o Firebase Admin SDK e retorna a referência /jogos/{id_jogo}."""
    if not FIREBASE_CREDENTIALS_JSON:
        raise RuntimeError("FIREBASE_CREDENTIALS_JSON não configurado nos Secrets do GitHub.")

    if not firebase_admin._apps:
        info = json.loads(FIREBASE_CREDENTIALS_JSON)
        cred = credentials.Certificate(info)
        firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})

    return db.reference(f"jogos/{id_jogo}")


def gerar_id_jogo(url_origem):
    """Gera um ID limpo e consistente a partir do slug da URL."""
    path = urlparse(url_origem).path.strip("/")
    slug = path.split("/")[-1] if path else "jogo-pc"
    slug = re.sub(r"\.(html|php)$", "", slug, flags=re.IGNORECASE).lower()
    slug = re.sub(r"[^a-z0-9-]+", "-", slug).strip("-")
    return slug or "jogo-pc"


def separar_versao_e_extra(texto_raw):
    """
    Separa a versão curta (ex.: v1.5.0.0) do extra/edição (ex.: + Multiplayer, + All DLCs).
    """
    texto = (texto_raw or "").strip()
    versao = ""
    extras = []

    m_ver = re.search(r"\bv?(\d+(?:\.\d+){1,4})\b", texto, flags=re.IGNORECASE)
    if m_ver:
        versao = "v" + m_ver.group(1)

    padroes_extra = [
        r"\+\s*Multiplayer(?:\s*Fix)?",
        r"\+\s*Online(?:\s*Fix)?",
        r"\+\s*All\s*(?:DLCs?|add-ons?)",
        r"\+\s*DLCs?",
        r"\+\s*Co-?op",
        r"Multiplayer(?:\s*Fix)?",
        r"All\s*(?:DLCs?|add-ons?)",
        r"Deluxe\s*Edition",
        r"Complete\s*Edition",
        r"Gold\s*Edition",
        r"Ultimate\s*Edition",
    ]

    for pat in padroes_extra:
        for achado in re.findall(pat, texto, flags=re.IGNORECASE):
            limpo = re.sub(r"\s+", " ", achado).strip()
            if limpo and not limpo.startswith("+") and any(
                k in limpo.lower() for k in ("multiplayer", "dlc", "online", "co-op", "coop", "add-on")
            ):
                limpo = "+ " + limpo
            if limpo.lower() not in [x.lower() for x in extras]:
                extras.append(limpo)

    extra_final = " ".join(extras).strip()
    return versao, extra_final


def extrair_tamanho_jogo(texto_corpo):
    """Extrai o peso/tamanho do jogo (ex.: 8.28 GB) a partir do texto da página."""
    if not texto_corpo:
        return ""
    m = re.search(
        r"(?:Game\s*Size|Tamanho|Size)\s*[:\-]\s*([0-9]+(?:[.,][0-9]+)?\s*(?:GB|MB|TB))",
        texto_corpo,
        flags=re.IGNORECASE,
    )
    if m:
        return re.sub(r"\s+", " ", m.group(1)).replace(",", ".").strip()
    return ""


def limpar_nome_jogo(titulo_raw):
    """Extrai apenas o nome base do jogo, removendo versão, extras e sufixos de site."""
    t = (titulo_raw or "").strip()
    t = re.sub(r"\s*[\|\-–—]\s*SteamRIP.*$", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"\s*Free\s*Download.*$", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"\s*\([^)]*\)\s*", " ", t).strip()
    t = re.sub(r"\s*\[[^\]]*\]\s*", " ", t).strip()
    t = re.sub(r"\s+", " ", t).strip()
    return t


def normalizar_nome_host(url_ou_texto, contexto_previo=""):
    """Identifica o nome real do host (MegaDB, BZZHR/Buzzheavier, 1fichier, Torrent, etc.)."""
    ctx = (contexto_previo or "").strip()
    alvo = f"{ctx} {url_ou_texto}".lower()

    # Prioriza nomes explícitos visíveis no contexto do botão
    for host_exato, rotulo in [
        ("megadb", "MegaDB"),
        ("buzzheavier", "Buzzheavier"),
        ("bzzhr", "BZZHR"),
        ("1fichier", "1fichier"),
        ("gofile", "Gofile"),
        ("mediafire", "MediaFire"),
        ("fileditch", "Fileditch"),
        ("pixeldrain", "PixelDrain"),
        ("vikingfile", "VikingFile"),
        ("datanodes", "DataNodes"),
        ("mega.nz", "Mega"),
        ("mega.io", "Mega"),
        ("torrent", "Torrent"),
    ]:
        if host_exato in alvo:
            return rotulo

    if ctx:
        palavras = re.sub(r"(?i)\b(download\s*here|download|link|mirror|click\s*here)\b", "", ctx).strip()
        palavras = re.sub(r"\s+", " ", palavras).strip()
        if palavras and len(palavras) <= 28:
            return palavras

    try:
        dominio = urlparse(url_ou_texto).netloc.replace("www.", "")
        parte = dominio.split(".")[0] if dominio else "Mirror"
        return parte.upper() if len(parte) <= 5 else parte.capitalize()
    except Exception:
        return "Mirror"


def url_e_host_de_download(url, contexto=""):
    """Filtra URLs de download/espelho/torrent e ignora redes sociais/anúncios."""
    if not url or not url.startswith(("http://", "https://", "magnet:")):
        return False

    u = url.lower()
    ignorar = [
        "steamrip.com",
        "discord.gg",
        "discord.com",
        "twitter.com",
        "x.com",
        "youtube.com",
        "reddit.com",
        "store.steampowered.com",
        "doubleclick.net",
        "googlesyndication",
        "google-analytics",
        "googletagmanager",
        "facebook.com",
        "instagram.com",
        "cdn-cgi",
        "javascript:",
    ]
    if any(bad in u for bad in ignorar):
        return False

    if u.startswith("magnet:") or "torrent" in u or "torrent" in (contexto or "").lower():
        return True

    hosts_conhecidos = [
        "megadb",
        "gofile",
        "buzzheavier",
        "bzzhr",
        "fileditch",
        "mediafire",
        "mega.nz",
        "mega.io",
        "1fichier",
        "pixeldrain",
        "vikingfile",
        "datanodes",
        "qiwi",
        "akirabox",
        "modsfire",
        "uploadhaven",
        "ranoz",
        "bowfile",
    ]
    if any(h in u for h in hosts_conhecidos):
        return True

    # Aceita link externo se o texto/contexto indicar explicitamente download/mirror
    return bool(re.search(r"\b(download\s*here|torrent|megadb|bzzhr|buzzheavier|1fichier|gofile|mediafire)\b", f"{contexto} {u}", flags=re.IGNORECASE))


def extrair_dados_steamrip(url_alvo):
    """
    Abre a página no SteamRIP e extrai:
    - nome
    - versao
    - extra
    - tamanho
    - foto
    - links (até 6 espelhos/botões com nomes reais)
    """
    print(f"🔎 Extraindo SteamRIP: {url_alvo}")
    id_jogo = gerar_id_jogo(url_alvo)
    nome_padrao = id_jogo.replace("-", " ").title()

    resultado = {
        "id": id_jogo,
        "url_origem": url_alvo,
        "nome": nome_padrao,
        "versao": "",
        "extra": "",
        "tamanho": "",
        "foto": "",
        "links": {},
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--window-size=1366,768",
            ],
        )

        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
            locale="en-US",
        )

        page = context.new_page()
        if stealth_sync:
            stealth_sync(page)
        else:
            page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )

        try:
            page.goto(url_alvo, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(4500)

            titulo_completo = ""
            try:
                h1 = page.locator("h1").first
                if h1.count() > 0:
                    titulo_completo = (h1.inner_text() or "").strip()
            except Exception:
                pass

            if not titulo_completo:
                try:
                    og_t = page.locator('meta[property="og:title"]').first
                    if og_t.count() > 0:
                        titulo_completo = (og_t.get_attribute("content") or "").strip()
                except Exception:
                    pass

            if not titulo_completo:
                titulo_completo = (page.title() or "").strip()

            texto_pagina = ""
            try:
                texto_pagina = page.evaluate(
                    "() => (document.querySelector('article, .entry-content, main') || document.body).innerText || ''"
                ) or ""
            except Exception:
                texto_pagina = ""

            nome_limpo = limpar_nome_jogo(titulo_completo)
            if nome_limpo:
                resultado["nome"] = nome_limpo

            versao_tit, extra_tit = separar_versao_e_extra(titulo_completo)
            versao_corpo, extra_corpo = separar_versao_e_extra(texto_pagina)

            # Prioriza versão encontrada no corpo (ex.: Version: v1.5.0.0) se o título não tiver
            resultado["versao"] = versao_corpo or versao_tit
            extras_combinados = []
            for item in (extra_corpo, extra_tit):
                for parte in (item or "").split("+"):
                    p_limpo = parte.strip()
                    if p_limpo and p_limpo.lower() not in [x.lower() for x in extras_combinados]:
                        extras_combinados.append("+ " + p_limpo if not p_limpo.startswith("+") else p_limpo)
            resultado["extra"] = " ".join(extras_combinados).strip()
            resultado["tamanho"] = extrair_tamanho_jogo(texto_pagina)

            foto_url = ""
            try:
                og_img = page.locator('meta[property="og:image"]').first
                if og_img.count() > 0:
                    foto_url = (og_img.get_attribute("content") or "").strip()
            except Exception:
                pass

            if not foto_url:
                try:
                    imgs = page.eval_on_selector_all(
                        "article img, .entry-content img, main img, img",
                        """els => els.map(e => ({
                            src: e.getAttribute('src') || e.getAttribute('data-src') || ''
                        }))""",
                    )
                    for item in imgs:
                        src = (item.get("src") or "").strip()
                        if src.startswith("http") and "logo" not in src.lower() and "icon" not in src.lower():
                            foto_url = urljoin(url_alvo, src)
                            break
                except Exception:
                    pass

            if foto_url:
                resultado["foto"] = urljoin(url_alvo, foto_url)

            candidatos = page.evaluate(
                """() => {
                    const out = [];
                    const root = document.querySelector('article, .entry-content, main') || document.body;
                    const anchors = Array.from(root.querySelectorAll('a[href]'));
                    for (const a of anchors) {
                        const href = (a.getAttribute('href') || '').trim();
                        if (!href || href.startsWith('#') || href.toLowerCase().startsWith('javascript:')) continue;

                        const ownText = (a.innerText || a.textContent || '').trim();
                        let prevText = '';
                        const prev = a.previousElementSibling;
                        if (prev) prevText = (prev.innerText || prev.textContent || '').trim();

                        let parentPrevText = '';
                        const parent = a.parentElement;
                        if (parent) {
                            const pPrev = parent.previousElementSibling;
                            if (pPrev) parentPrevText = (pPrev.innerText || pPrev.textContent || '').trim();
                            const pText = (parent.innerText || parent.textContent || '').trim();
                            if (!prevText && pText && pText !== ownText) {
                                prevText = pText.replace(ownText, '').trim();
                            }
                        }

                        const contexto = [parentPrevText, prevText, ownText]
                            .filter(Boolean)
                            .join(' ')
                            .replace(/\\s+/g, ' ')
                            .trim();

                        out.push({
                            href: href,
                            contexto: contexto
                        });
                    }
                    return out;
                }"""
            )

            links_dict = {}
            contador = 1

            for item in candidatos:
                if len(links_dict) >= 6:
                    break

                href_abs = urljoin(url_alvo, item.get("href") or "")
                ctx = item.get("contexto") or ""

                if not url_e_host_de_download(href_abs, ctx):
                    continue

                nome_host = normalizar_nome_host(href_abs, ctx)
                chave_base = re.sub(r"[^a-z0-9]+", "_", nome_host.lower()).strip("_") or f"mirror_{contador}"

                chave = chave_base
                if chave in links_dict and links_dict[chave] != href_abs:
                    contador += 1
                    chave = f"{chave_base}_{contador}"

                if chave not in links_dict:
                    links_dict[chave] = href_abs
                    contador += 1

            resultado["links"] = links_dict

        except Exception as e:
            print(f"❌ Erro ao extrair '{url_alvo}': {e}")

        browser.close()

    return resultado


def salvar_jogo_firebase(dados_novos):
    """Salva ou atualiza /jogos/{id}.json no Firebase PC apenas se houver mudança real."""
    id_jogo = dados_novos["id"]
    if not dados_novos.get("links"):
        print(f"⚠️ Nenhum link de download encontrado para '{id_jogo}'. Nada enviado ao Firebase.")
        return False

    try:
        ref = obter_ref_firebase(id_jogo)
        atual = ref.get() or {}
        if not isinstance(atual, dict):
            atual = {}

        mesmo_nome = atual.get("nome") == dados_novos.get("nome")
        mesma_versao = atual.get("versao") == dados_novos.get("versao")
        mesmo_extra = atual.get("extra") == dados_novos.get("extra")
        mesmo_tamanho = atual.get("tamanho") == dados_novos.get("tamanho")
        mesma_foto = atual.get("foto") == dados_novos.get("foto")
        mesmos_links = atual.get("links") == dados_novos.get("links")

        if mesmo_nome and mesma_versao and mesmo_extra and mesmo_tamanho and mesma_foto and mesmos_links:
            print(f"⏩ '{id_jogo}' sem alterações ({dados_novos.get('versao') or 'sem versão'}).")
            return True

        payload = {
            "id": id_jogo,
            "nome": dados_novos["nome"],
            "versao": dados_novos["versao"],
            "extra": dados_novos["extra"],
            "tamanho": dados_novos["tamanho"],
            "foto": dados_novos["foto"],
            "links": dados_novos["links"],
            "url_origem": dados_novos["url_origem"],
            "fonte": "steamrip",
        }

        ref.set(payload)
        print(
            f"✅ Firebase PC atualizado -> ID: {id_jogo} | "
            f"Versão: {payload['versao'] or '-'} | "
            f"Extra: {payload['extra'] or '-'} | "
            f"Tamanho: {payload['tamanho'] or '-'} | "
            f"Links: {len(payload['links'])}"
        )
        return True
    except Exception as e:
        print(f"❌ Erro ao salvar no Firebase: {e}")
        return False


def registrar_na_lista(url_alvo):
    """Salva a URL no jogos.txt para monitoramento automático futuro."""
    existentes = set()
    if os.path.exists(ARQUIVO_JOGOS):
        with open(ARQUIVO_JOGOS, "r", encoding="utf-8") as f:
            existentes = {line.strip() for line in f if line.strip()}

    if url_alvo not in existentes:
        with open(ARQUIVO_JOGOS, "a", encoding="utf-8") as f:
            f.write(url_alvo + "\n")
        print(f"📝 Adicionado ao {ARQUIVO_JOGOS}: {url_alvo}")


def processar_url(url_alvo):
    url_limpa = (url_alvo or "").strip()
    if not url_limpa.startswith(("http://", "https://")):
        print(f"⚠️ URL inválida ignorada: {url_alvo}")
        return

    print("\n==================================================")
    dados = extrair_dados_steamrip(url_limpa)
    ok = salvar_jogo_firebase(dados)
    if ok:
        registrar_na_lista(url_limpa)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].strip().startswith("http"):
        processar_url(sys.argv[1].strip())
    elif os.path.exists(ARQUIVO_JOGOS):
        with open(ARQUIVO_JOGOS, "r", encoding="utf-8") as f:
            urls = [linha.strip() for linha in f if linha.strip() and not linha.strip().startswith("#")]

        print(f"🤖 Modo automático iniciado. {len(urls)} jogo(s) no {ARQUIVO_JOGOS}.")
        for u in urls:
            processar_url(u)
    else:
        print(f"⚠️ Nenhum '{ARQUIVO_JOGOS}' encontrado e nenhuma URL manual passada.")
