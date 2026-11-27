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


def extrair_versao_exata(titulo_completo, texto_corpo):
    """
    Busca a versão de forma confiável:
    1) Linhas explícitas com 'Version: v...'
    2) Título principal do jogo
    3) Fallback controlado no texto do post
    """
    candidatos_texto = [texto_corpo or "", titulo_completo or ""]

    for fonte in candidatos_texto:
        m_rotulo = re.search(
            r"(?:^|\n|\s)version\s*[:\-]\s*v?(\d+(?:\.\d+){1,4})",
            fonte,
            flags=re.IGNORECASE,
        )
        if m_rotulo:
            return "v" + m_rotulo.group(1)

    m_titulo = re.search(r"\bv?(\d+(?:\.\d+){1,4})\b", titulo_completo or "", flags=re.IGNORECASE)
    if m_titulo:
        return "v" + m_titulo.group(1)

    m_corpo = re.search(r"\bv(\d+(?:\.\d+){1,4})\b", texto_corpo or "", flags=re.IGNORECASE)
    if m_corpo:
        return "v" + m_corpo.group(1)

    return ""


def separar_extra(texto_raw):
    """Extrai extras/edições (ex.: + Multiplayer, + All DLCs, + all add-ons)."""
    texto = (texto_raw or "").strip()
    extras = []

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

    return " ".join(extras).strip()


def extrair_tamanho_jogo(texto_corpo):
    """Extrai o peso/tamanho do jogo (ex.: 8.28 GB)."""
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
    """Extrai apenas o nome base do jogo."""
    t = (titulo_raw or "").strip()
    t = re.sub(r"\s*[\|\-–—]\s*SteamRIP.*$", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"\s*Free\s*Download.*$", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"\s*\([^)]*\)\s*", " ", t).strip()
    t = re.sub(r"\s*\[[^\]]*\]\s*", " ", t).strip()
    t = re.sub(r"\s+", " ", t).strip()
    return t


def identificar_nome_host(url, rotulo_proximo=""):
    """
    Define o nome real do host usando primeiro o domínio/URL real
    e complementando com o rótulo exato daquele botão (ex.: Torrent).
    """
    u = (url or "").lower()
    rot = (rotulo_proximo or "").strip()

    if "torrent" in rot.lower() or u.startswith("magnet:") or "torrent" in u:
        return "Torrent"
    if "1fichier" in u:
        return "1fichier"
    if "megadb" in u:
        return "MegaDB"
    if "buzzheavier" in u or "bzzhr" in u:
        return "Buzzheavier"
    if "gofile" in u:
        return "Gofile"
    if "mediafire" in u:
        return "MediaFire"
    if "fileditch" in u:
        return "Fileditch"
    if "pixeldrain" in u:
        return "PixelDrain"
    if "vikingfile" in u:
        return "VikingFile"
    if "datanodes" in u:
        return "DataNodes"
    if "mega.nz" in u or "mega.io" in u:
        return "Mega"

    if rot:
        limpo = re.sub(r"(?i)\b(download\s*here|download|click\s*here|link|mirror)\b", "", rot).strip()
        limpo = re.sub(r"\s+", " ", limpo).strip()
        if limpo and len(limpo) <= 24:
            return limpo

    try:
        dominio = urlparse(url).netloc.replace("www.", "")
        parte = dominio.split(".")[0] if dominio else "Mirror"
        return parte.upper() if len(parte) <= 5 else parte.capitalize()
    except Exception:
        return "Mirror"


def url_e_host_de_download(url, rotulo=""):
    """Valida se a URL é um destino real de download/espelho/torrent."""
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

    if u.startswith("magnet:") or "torrent" in u or "torrent" in (rotulo or "").lower():
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
    return any(h in u for h in hosts_conhecidos)


def extrair_dados_steamrip(url_alvo):
    """Abre a página no SteamRIP e extrai metadados e links com seus nomes corretos."""
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

            texto_corpo = ""
            try:
                texto_corpo = page.evaluate(
                    """() => {
                        const art = document.querySelector('article, .entry-content, .post-content, main') || document.body;
                        return art ? (art.innerText || '') : '';
                    }"""
                ) or ""
            except Exception:
                texto_corpo = ""

            nome_limpo = limpar_nome_jogo(titulo_completo)
            if nome_limpo:
                resultado["nome"] = nome_limpo

            resultado["versao"] = extrair_versao_exata(titulo_completo, texto_corpo)
            extra_corpo = separar_extra(texto_corpo)
            extra_tit = separar_extra(titulo_completo)
            resultado["extra"] = extra_corpo or extra_tit
            resultado["tamanho"] = extrair_tamanho_jogo(texto_corpo)

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

            # Pega cada link dentro do conteúdo principal junto com o título imediatamente acima dele
            candidatos = page.evaluate(
                """() => {
                    const out = [];
                    const root = document.querySelector('article, .entry-content, .post-content, main') || document.body;
                    const anchors = Array.from(root.querySelectorAll('a[href]'));

                    for (const a of anchors) {
                        const href = (a.getAttribute('href') || '').trim();
                        if (!href || href.startsWith('#') || href.toLowerCase().startsWith('javascript:')) continue;

                        let rotulo = '';
                        let cursor = a.previousElementSibling;
                        let passos = 0;
                        while (cursor && passos < 3) {
                            const txt = (cursor.innerText || cursor.textContent || '').trim();
                            if (txt && !/^(download here|click here|get link)$/i.test(txt)) {
                                rotulo = txt;
                                break;
                            }
                            cursor = cursor.previousElementSibling;
                            passos++;
                        }

                        if (!rotulo && a.parentElement) {
                            let pCursor = a.parentElement.previousElementSibling;
                            let pPassos = 0;
                            while (pCursor && pPassos < 3) {
                                const txt = (pCursor.innerText || pCursor.textContent || '').trim();
                                if (txt && !/^(download here|click here|get link)$/i.test(txt)) {
                                    rotulo = txt;
                                    break;
                                }
                                pCursor = pCursor.previousElementSibling;
                                pPassos++;
                            }
                        }

                        const textoProprio = (a.innerText || a.textContent || '').trim();
                        out.push({
                            href: href,
                            rotulo: rotulo || textoProprio
                        });
                    }
                    return out;
                }"""
            )

            links_dict = {}
            idx_extra = 1

            for item in candidatos:
                if len(links_dict) >= 6:
                    break

                href_abs = urljoin(url_alvo, item.get("href") or "")
                rotulo = item.get("rotulo") or ""

                if not url_e_host_de_download(href_abs, rotulo):
                    continue

                nome_host = identificar_nome_host(href_abs, rotulo)
                chave_base = re.sub(r"[^a-z0-9]+", "_", nome_host.lower()).strip("_") or f"link_{idx_extra}"

                if chave_base not in links_dict:
                    links_dict[chave_base] = href_abs
                elif links_dict[chave_base] != href_abs:
                    idx_extra += 1
                    links_dict[f"{chave_base}_{idx_extra}"] = href_abs

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
