import os
import re
import sys
import json
from urllib.parse import urlparse, urljoin
import requests
from playwright.sync_api import sync_playwright

try:
    from google.oauth2 import service_account
    import google.auth.transport.requests
except ImportError:
    service_account = None

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


def obter_token_firebase():
    """Gera token OAuth2 usando a Service Account salva no GitHub Secrets."""
    if not FIREBASE_CREDENTIALS_JSON or not service_account:
        return None
    try:
        info = json.loads(FIREBASE_CREDENTIALS_JSON)
        scopes = [
            "https://www.googleapis.com/auth/firebase.database",
            "https://www.googleapis.com/auth/userinfo.email",
        ]
        creds = service_account.Credentials.from_service_account_info(info, scopes=scopes)
        creds.refresh(google.auth.transport.requests.Request())
        return creds.token
    except Exception as e:
        print(f"⚠️ Erro ao gerar token Firebase: {e}")
        return None


def gerar_id_jogo(url_origem):
    """Gera um ID limpo e consistente a partir do slug da URL."""
    path = urlparse(url_origem).path.strip("/")
    slug = path.split("/")[-1] if path else "jogo-pc"
    slug = re.sub(r"\.(html|php)$", "", slug, flags=re.IGNORECASE).lower()
    slug = re.sub(r"[^a-z0-9-]+", "-", slug).strip("-")
    return slug or "jogo-pc"


def separar_versao_e_extra(texto_raw):
    """
    Separa a versão curta (ex.: v483.970) do extra/edição (ex.: + Multiplayer, + All DLCs).
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
        r"\+\s*All\s*DLCs?",
        r"\+\s*DLCs?",
        r"\+\s*Co-?op",
        r"Multiplayer(?:\s*Fix)?",
        r"All\s*DLCs?",
        r"Deluxe\s*Edition",
        r"Complete\s*Edition",
        r"Gold\s*Edition",
        r"Ultimate\s*Edition",
    ]

    for pat in padroes_extra:
        for achado in re.findall(pat, texto, flags=re.IGNORECASE):
            limpo = re.sub(r"\s+", " ", achado).strip()
            if limpo and not limpo.startswith("+") and ("multiplayer" in limpo.lower() or "dlc" in limpo.lower() or "online" in limpo.lower() or "co-op" in limpo.lower() or "co-op" in limpo.lower()):
                limpo = "+ " + limpo
            if limpo.lower() not in [x.lower() for x in extras]:
                extras.append(limpo)

    extra_final = " ".join(extras).strip()
    return versao, extra_final


def limpar_nome_jogo(titulo_raw):
    """Extrai apenas o nome base do jogo, removendo versão, extras e sufixos de site."""
    t = (titulo_raw or "").strip()
    t = re.sub(r"\s*[\|\-–—]\s*SteamRIP.*$", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"\s*Free\s*Download.*$", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"\s*\([^)]*\)\s*", " ", t).strip()
    t = re.sub(r"\s*\[[^\]]*\]\s*", " ", t).strip()
    t = re.sub(r"\s+", " ", t).strip()
    return t


def normalizar_nome_host(url_ou_texto, host_previo=""):
    """Identifica o nome amigável do host (Gofile, Buzzheavier, Fileditch, etc.)."""
    alvo = f"{host_previo} {url_ou_texto}".lower()
    if "gofile" in alvo:
        return "Gofile"
    if "buzzheavier" in alvo or "bzzhr" in alvo:
        return "Buzzheavier"
    if "fileditch" in alvo:
        return "Fileditch"
    if "mediafire" in alvo:
        return "MediaFire"
    if "mega.nz" in alvo or "mega.io" in alvo:
        return "Mega"
    if "1fichier" in alvo:
        return "1fichier"
    if "pixeldrain" in alvo:
        return "PixelDrain"
    if "vikingfile" in alvo:
        return "VikingFile"
    if "datanodes" in alvo:
        return "DataNodes"
    if "torrent" in alvo or "magnet:" in alvo:
        return "Torrent"

    if host_previo and host_previo.strip():
        return host_previo.strip().title()

    try:
        dominio = urlparse(url_ou_texto).netloc.replace("www.", "")
        parte = dominio.split(".")[0] if dominio else "Espelho"
        return parte.capitalize() or "Espelho"
    except Exception:
        return "Espelho"


def url_e_host_de_download(url):
    """Filtra URLs externas que parecem links de hospedagem/download e ignora lixo."""
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
        "#",
    ]
    if any(bad in u for bad in ignorar):
        return False

    hosts_conhecidos = [
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
    ]
    return any(h in u for h in hosts_conhecidos)


def extrair_dados_steamrip(url_alvo):
    """
    Abre a página no SteamRIP e extrai:
    - nome
    - versao (curta)
    - extra (ex.: + Multiplayer, + All DLCs)
    - foto (capa/imagem do jogo)
    - links (dicionário de espelhos válidos)
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

            # 1. Título completo (H1 ou og:title) para separar versão e extra
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

            nome_limpo = limpar_nome_jogo(titulo_completo)
            if nome_limpo:
                resultado["nome"] = nome_limpo

            versao, extra = separar_versao_e_extra(titulo_completo)
            resultado["versao"] = versao
            resultado["extra"] = extra

            # 2. Foto / Capa do jogo
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
                            src: e.getAttribute('src') || e.getAttribute('data-src') || '',
                            w: e.naturalWidth || e.width || 0,
                            h: e.naturalHeight || e.height || 0
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

            # 3. Coletar links de download (focando na seção principal antes de 'Update Only' se houver)
            candidatos = page.evaluate(
                """() => {
                    const out = [];
                    const anchors = Array.from(document.querySelectorAll('a[href]'));
                    for (const a of anchors) {
                        const href = (a.getAttribute('href') || '').trim();
                        if (!href || href.startsWith('#') || href.toLowerCase().startsWith('javascript:')) continue;

                        let contexto = (a.innerText || a.textContent || '').trim();
                        const prev = a.previousElementSibling;
                        if (prev) {
                            contexto += ' ' + (prev.innerText || prev.textContent || '').trim();
                        }
                        const parent = a.parentElement;
                        if (parent && parent.previousElementSibling) {
                            contexto += ' ' + (parent.previousElementSibling.innerText || parent.previousElementSibling.textContent || '').trim();
                        }

                        out.push({
                            href: href,
                            contexto: contexto.replace(/\\s+/g, ' ').trim()
                        });
                    }
                    return out;
                }"""
            )

            links_dict = {}
            contador_generico = 1

            for item in candidatos:
                href_abs = urljoin(url_alvo, item.get("href") or "")
                ctx = item.get("contexto") or ""

                if not url_e_host_de_download(href_abs):
                    continue

                nome_host = normalizar_nome_host(href_abs, ctx)
                chave = re.sub(r"[^a-z0-9]+", "_", nome_host.lower()).strip("_") or f"mirror_{contador_generico}"

                if chave in links_dict and links_dict[chave] != href_abs:
                    contador_generico += 1
                    chave = f"{chave}_{contador_generico}"

                if chave not in links_dict:
                    links_dict[chave] = href_abs

            resultado["links"] = links_dict

        except Exception as e:
            print(f"❌ Erro ao extrair '{url_alvo}': {e}")

        browser.close()

    return resultado


def buscar_jogo_firebase(id_jogo, token=None):
    """Consulta os dados atuais de /jogos/{id_jogo}.json no Firebase PC."""
    url = f"{FIREBASE_DB_URL}/jogos/{id_jogo}.json"
    params = {"auth": token} if token else {}
    try:
        res = requests.get(url, params=params, timeout=20)
        if res.status_code == 200 and res.text and res.text != "null":
            dados = res.json()
            return dados if isinstance(dados, dict) else {}
    except Exception as e:
        print(f"⚠️ Erro ao ler Firebase para '{id_jogo}': {e}")
    return {}


def salvar_jogo_firebase(dados_novos):
    """Salva ou atualiza /jogos/{id}.json no Firebase PC apenas se houver mudança real."""
    id_jogo = dados_novos["id"]
    if not dados_novos.get("links"):
        print(f"⚠️ Nenhum link de download encontrado para '{id_jogo}'. Nada enviado ao Firebase.")
        return False

    token = obter_token_firebase()
    if not token:
        print("❌ FIREBASE_CREDENTIALS_JSON ausente ou inválido. Configure nos Secrets do GitHub.")
        return False

    atual = buscar_jogo_firebase(id_jogo, token=token)

    mesmo_nome = atual.get("nome") == dados_novos.get("nome")
    mesma_versao = atual.get("versao") == dados_novos.get("versao")
    mesmo_extra = atual.get("extra") == dados_novos.get("extra")
    mesma_foto = atual.get("foto") == dados_novos.get("foto")
    mesmos_links = atual.get("links") == dados_novos.get("links")

    if mesmo_nome and mesma_versao and mesmo_extra and mesma_foto and mesmos_links:
        print(f"⏩ '{id_jogo}' sem alterações ({dados_novos.get('versao') or 'sem versão'}).")
        return True

    payload = {
        "id": id_jogo,
        "nome": dados_novos["nome"],
        "versao": dados_novos["versao"],
        "extra": dados_novos["extra"],
        "foto": dados_novos["foto"],
        "links": dados_novos["links"],
        "url_origem": dados_novos["url_origem"],
        "fonte": "steamrip",
    }

    url = f"{FIREBASE_DB_URL}/jogos/{id_jogo}.json"
    try:
        res = requests.put(url, params={"auth": token}, json=payload, timeout=25)
        if res.status_code == 200:
            print(
                f"✅ Firebase PC atualizado -> ID: {id_jogo} | "
                f"Versão: {payload['versao'] or '-'} | "
                f"Extra: {payload['extra'] or '-'} | "
                f"Links: {len(payload['links'])}"
            )
            return True
        else:
            print(f"❌ Erro HTTP ao salvar no Firebase ({res.status_code}): {res.text}")
            return False
    except Exception as e:
        print(f"❌ Exceção ao salvar no Firebase: {e}")
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
