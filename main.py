import json
import sqlite3
from datetime import date
import os
import base64
import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
LASTFM_API_KEY = os.getenv("LASTFM_API_KEY", "").strip()
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()
AUDIODB_API_KEY = os.getenv("AUDIODB_API_KEY", "").strip()
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()

YOUTUBE_DAILY_LIMIT = 20
YOUTUBE_QUOTA_FILE = "youtube_quota.json"
DATABASE_FILE = "metalpedia.db"


def inicializar_banco():
    conexao = sqlite3.connect(DATABASE_FILE)
    cursor = conexao.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS favoritos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_user_id INTEGER NOT NULL,
            banda TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(telegram_user_id, banda)
        )
    """)

    conexao.commit()
    conexao.close()


def adicionar_favorito(telegram_user_id, banda):
    conexao = sqlite3.connect(DATABASE_FILE)
    cursor = conexao.cursor()

    try:
        cursor.execute(
            "INSERT INTO favoritos (telegram_user_id, banda) VALUES (?, ?)",
            (telegram_user_id, banda)
        )
        conexao.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conexao.close()


def listar_favoritos(telegram_user_id):
    conexao = sqlite3.connect(DATABASE_FILE)
    cursor = conexao.cursor()

    cursor.execute(
        "SELECT banda FROM favoritos WHERE telegram_user_id = ? ORDER BY banda",
        (telegram_user_id,)
    )

    favoritos = [linha[0] for linha in cursor.fetchall()]
    conexao.close()

    return favoritos


def remover_favorito(telegram_user_id, banda):
    conexao = sqlite3.connect(DATABASE_FILE)
    cursor = conexao.cursor()

    cursor.execute(
        "DELETE FROM favoritos WHERE telegram_user_id = ? AND LOWER(banda) = LOWER(?)",
        (telegram_user_id, banda)
    )

    removido = cursor.rowcount > 0
    conexao.commit()
    conexao.close()

    return removido


def pode_usar_youtube():
    hoje = date.today().isoformat()

    try:
        with open(YOUTUBE_QUOTA_FILE, "r", encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
    except FileNotFoundError:
        dados = {"data": hoje, "usos": 0}

    if dados.get("data") != hoje:
        dados = {"data": hoje, "usos": 0}

    if dados["usos"] >= YOUTUBE_DAILY_LIMIT:
        return False

    dados["usos"] += 1

    with open(YOUTUBE_QUOTA_FILE, "w", encoding="utf-8") as arquivo:
        json.dump(dados, arquivo, indent=4)

    return True


def buscar_banda(nome_banda):
    url = "https://musicbrainz.org/ws/2/artist/"
    params = {"query": nome_banda, "fmt": "json"}
    headers = {"User-Agent": "MetalpediaBot/1.0"}

    response = requests.get(url, params=params, headers=headers, timeout=10)

    if response.status_code != 200:
        return None

    dados = response.json()

    if not dados.get("artists"):
        return None

    artista = dados["artists"][0]

    return {
        "id": artista.get("id"),
        "nome": artista.get("name", "N/A"),
        "pais": artista.get("country", "N/A"),
        "inicio": artista.get("life-span", {}).get("begin", "N/A"),
        "tipo": artista.get("type", "N/A")
    }


def buscar_albuns(artist_id):
    url = "https://musicbrainz.org/ws/2/release-group"
    params = {"artist": artist_id, "fmt": "json", "limit": 10}
    headers = {"User-Agent": "MetalpediaBot/1.0"}

    response = requests.get(url, params=params, headers=headers, timeout=10)

    if response.status_code != 200:
        return []

    dados = response.json()
    albuns = []

    for item in dados.get("release-groups", []):
        if item.get("primary-type") == "Album":
            albuns.append(item.get("title", "Sem título"))

    return albuns[:5]


def buscar_top_musicas(nome_banda):
    if not LASTFM_API_KEY:
        return []

    url = "https://ws.audioscrobbler.com/2.0/"
    params = {
        "method": "artist.gettoptracks",
        "artist": nome_banda,
        "api_key": LASTFM_API_KEY,
        "format": "json",
        "limit": 5,
        "autocorrect": 1
    }

    response = requests.get(url, params=params, timeout=10)

    if response.status_code != 200:
        return []

    dados = response.json()
    tracks = dados.get("toptracks", {}).get("track", [])

    musicas = []

    for track in tracks:
        nome_musica = track.get("name")
        if nome_musica:
            musicas.append(nome_musica)

    return musicas


def buscar_artistas_similares(nome_banda):
    if not LASTFM_API_KEY:
        return []

    url = "https://ws.audioscrobbler.com/2.0/"
    params = {
        "method": "artist.getsimilar",
        "artist": nome_banda,
        "api_key": LASTFM_API_KEY,
        "format": "json",
        "limit": 8,
        "autocorrect": 1
    }

    response = requests.get(url, params=params, timeout=10)

    if response.status_code != 200:
        return []

    dados = response.json()
    artistas = dados.get("similarartists", {}).get("artist", [])

    similares = []

    for artista in artistas:
        nome = artista.get("name")
        match = artista.get("match")
        url_artista = artista.get("url")

        if nome:
            similares.append({
                "nome": nome,
                "match": match,
                "url": url_artista
            })

    return similares


def buscar_clipe_youtube(termo_busca):
    if not YOUTUBE_API_KEY:
        return None

    if not pode_usar_youtube():
        return "LIMITE_YOUTUBE"

    url = "https://www.googleapis.com/youtube/v3/search"

    params = {
        "part": "snippet",
        "q": termo_busca,
        "key": YOUTUBE_API_KEY,
        "type": "video",
        "maxResults": 1
    }

    response = requests.get(url, params=params, timeout=10)

    if response.status_code != 200:
        return None

    dados = response.json()
    items = dados.get("items", [])

    if not items:
        return None

    video_id = items[0].get("id", {}).get("videoId")

    if not video_id:
        return None

    return f"https://www.youtube.com/watch?v={video_id}"


def buscar_imagem_banda(nome_banda):
    if not AUDIODB_API_KEY:
        return None

    url = f"https://www.theaudiodb.com/api/v1/json/{AUDIODB_API_KEY}/search.php"
    params = {"s": nome_banda}

    response = requests.get(url, params=params, timeout=10)

    if response.status_code != 200:
        return None

    dados = response.json()

    if not dados.get("artists"):
        return None

    artista = dados["artists"][0]

    return (
        artista.get("strArtistFanart")
        or artista.get("strArtistThumb")
        or artista.get("strArtistLogo")
    )


def buscar_album(nome_banda, nome_album):
    if not AUDIODB_API_KEY:
        return None

    url = f"https://www.theaudiodb.com/api/v1/json/{AUDIODB_API_KEY}/searchalbum.php"
    params = {"s": nome_banda, "a": nome_album}

    response = requests.get(url, params=params, timeout=10)

    if response.status_code != 200:
        return None

    dados = response.json()

    if not dados.get("album"):
        return None

    album = dados["album"][0]

    return {
        "nome": album.get("strAlbum", "N/A"),
        "artista": album.get("strArtist", "N/A"),
        "ano": album.get("intYearReleased", "N/A"),
        "genero": album.get("strGenre", "N/A"),
        "estilo": album.get("strStyle", "N/A"),
        "descricao": album.get("strDescriptionEN"),
        "capa": album.get("strAlbumThumb")
    }


def obter_token_spotify():
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        return None

    auth_string = f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}"
    auth_bytes = auth_string.encode("utf-8")
    auth_base64 = base64.b64encode(auth_bytes).decode("utf-8")

    url = "https://accounts.spotify.com/api/token"

    headers = {
        "Authorization": f"Basic {auth_base64}",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    data = {"grant_type": "client_credentials"}

    response = requests.post(url, headers=headers, data=data, timeout=10)

    if response.status_code != 200:
        return None

    return response.json().get("access_token")


def buscar_musica_spotify(nome_banda, nome_musica):
    token = obter_token_spotify()

    if not token:
        return None

    url = "https://api.spotify.com/v1/search"
    headers = {"Authorization": f"Bearer {token}"}

    params = {
        "q": f"track:{nome_musica} artist:{nome_banda}",
        "type": "track",
        "limit": 1
    }

    response = requests.get(url, headers=headers, params=params, timeout=10)

    if response.status_code != 200:
        return None

    dados = response.json()
    items = dados.get("tracks", {}).get("items", [])

    if not items:
        return None

    track = items[0]
    album = track.get("album", {})
    imagens = album.get("images", [])

    duracao_ms = track.get("duration_ms", 0)
    minutos = duracao_ms // 60000
    segundos = (duracao_ms % 60000) // 1000
    duracao_formatada = f"{minutos}:{segundos:02d}"

    artistas = track.get("artists", [])
    nome_artista = artistas[0].get("name", "N/A") if artistas else "N/A"

    return {
        "nome": track.get("name", "N/A"),
        "artista": nome_artista,
        "album": album.get("name", "N/A"),
        "duracao": duracao_formatada,
        "popularidade": track.get("popularity", "N/A"),
        "spotify_url": track.get("external_urls", {}).get("spotify"),
        "capa": imagens[0]["url"] if imagens else None
    }


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤘 Bem-vindo(a) ao Metalpedia Bot!\n\n"
        "Comandos disponíveis:\n"
        "/banda nome_da_banda\n"
        "/clipe banda musica\n"
        "/imagem nome_da_banda\n"
        "/album banda, album\n"
        "/musica banda, musica\n"
        "/recomenda nome_da_banda\n"
        "/favoritar nome_da_banda\n"
        "/meusfavoritos\n"
        "/removerfavorito nome_da_banda\n\n"
        "Exemplos:\n"
        "/banda Nightwish\n"
        "/album Nightwish, Once\n"
        "/musica Nightwish, Ghost Love Score\n"
        "/recomenda Epica\n"
        "/favoritar Nightwish"
    )


async def banda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nome_banda = " ".join(context.args)

    if not nome_banda:
        await update.message.reply_text("Exemplo: /banda Nightwish")
        return

    info = buscar_banda(nome_banda)

    if not info:
        await update.message.reply_text("Não encontrei essa banda 😢")
        return

    albuns = buscar_albuns(info["id"])
    musicas = buscar_top_musicas(info["nome"])

    lista_albuns = "\n".join([f"💿 {album}" for album in albuns]) if albuns else "Não encontrei álbuns."
    lista_musicas = "\n".join([f"🎵 {musica}" for musica in musicas]) if musicas else "Não encontrei músicas."

    resposta = f"""
🎸 {info['nome']}

📍 País: {info['pais']}
📅 Formada em: {info['inicio']}
🎼 Tipo: {info['tipo']}

Álbuns:
{lista_albuns}

Músicas populares:
{lista_musicas}
"""

    await update.message.reply_text(resposta)


async def clipe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    termo = " ".join(context.args)

    if not termo:
        await update.message.reply_text("Exemplo: /clipe Nightwish Nemo")
        return

    url = buscar_clipe_youtube(f"{termo} official video")

    if url == "LIMITE_YOUTUBE":
        await update.message.reply_text(
            "⚠️ Limite diário de buscas no YouTube atingido. Tente novamente amanhã."
        )
        return

    if not url:
        await update.message.reply_text("Não encontrei clipe 😢")
        return

    await update.message.reply_text(f"🎬 {url}")


async def imagem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nome_banda = " ".join(context.args)

    if not nome_banda:
        await update.message.reply_text("Exemplo: /imagem Nightwish")
        return

    imagem_url = buscar_imagem_banda(nome_banda)

    if not imagem_url:
        await update.message.reply_text("Não encontrei imagem 😢")
        return

    await update.message.reply_photo(photo=imagem_url, caption=f"🤘 {nome_banda}")


async def album(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = " ".join(context.args)

    if not texto or "," not in texto:
        await update.message.reply_text(
            "Use o formato:\n/album banda, album\n\nExemplo:\n/album Nightwish, Once"
        )
        return

    partes = texto.split(",", 1)
    nome_banda = partes[0].strip()
    nome_album = partes[1].strip()

    if not nome_banda or not nome_album:
        await update.message.reply_text(
            "Use o formato correto:\n/album Nightwish, Once"
        )
        return

    dados_album = buscar_album(nome_banda, nome_album)

    if not dados_album:
        await update.message.reply_text("Não encontrei esse álbum 😢")
        return

    descricao = dados_album["descricao"]

    if descricao:
        descricao = descricao[:700] + "..." if len(descricao) > 700 else descricao
    else:
        descricao = "Descrição não encontrada."

    resposta = f"""
💿 {dados_album['nome']}

🎸 Artista: {dados_album['artista']}
📅 Ano: {dados_album['ano']}
🎼 Gênero: {dados_album['genero']}
🔥 Estilo: {dados_album['estilo']}

📝 Sobre:
{descricao}
"""

    if dados_album["capa"]:
        await update.message.reply_photo(photo=dados_album["capa"], caption=resposta)
    else:
        await update.message.reply_text(resposta)


async def musica(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = " ".join(context.args)

    if not texto or "," not in texto:
        await update.message.reply_text(
            "Use o formato:\n/musica banda, musica\n\nExemplo:\n/musica Nightwish, Ghost Love Score"
        )
        return

    partes = texto.split(",", 1)
    nome_banda = partes[0].strip()
    nome_musica = partes[1].strip()

    if not nome_banda or not nome_musica:
        await update.message.reply_text(
            "Use o formato correto:\n/musica Nightwish, Ghost Love Score"
        )
        return

    dados_musica = buscar_musica_spotify(nome_banda, nome_musica)

    if not dados_musica:
        await update.message.reply_text("Não encontrei essa música no Spotify 😢")
        return

    youtube_url = buscar_clipe_youtube(
        f"{dados_musica['artista']} {dados_musica['nome']} official video"
    )

    resposta = f"""
🎵 {dados_musica['nome']}

🎸 Artista: {dados_musica['artista']}
💿 Álbum: {dados_musica['album']}
⏱ Duração: {dados_musica['duracao']}
🔥 Popularidade Spotify: {dados_musica['popularidade']}

🎧 Spotify:
{dados_musica['spotify_url']}
"""

    if youtube_url == "LIMITE_YOUTUBE":
        resposta += "\n🎬 YouTube: limite diário atingido."
    elif youtube_url:
        resposta += f"\n🎬 YouTube:\n{youtube_url}"

    if dados_musica["capa"]:
        await update.message.reply_photo(photo=dados_musica["capa"], caption=resposta)
    else:
        await update.message.reply_text(resposta)


async def recomenda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nome_banda = " ".join(context.args)

    if not nome_banda:
        await update.message.reply_text("Exemplo: /recomenda Epica")
        return

    similares = buscar_artistas_similares(nome_banda)

    if not similares:
        await update.message.reply_text("Não encontrei recomendações para essa banda 😢")
        return

    lista = "\n".join([f"🎸 {item['nome']}" for item in similares])

    resposta = f"""
🤘 Se você gosta de {nome_banda}, talvez curta:

{lista}
"""

    await update.message.reply_text(resposta)


async def favoritar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nome_banda = " ".join(context.args).strip()

    if not nome_banda:
        await update.message.reply_text("Exemplo: /favoritar Nightwish")
        return

    telegram_user_id = update.effective_user.id
    banda_formatada = nome_banda.title()

    sucesso = adicionar_favorito(telegram_user_id, banda_formatada)

    if sucesso:
        await update.message.reply_text(f"✅ {banda_formatada} foi adicionada aos seus favoritos.")
    else:
        await update.message.reply_text(f"⚠️ {banda_formatada} já está nos seus favoritos.")


async def meusfavoritos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_user_id = update.effective_user.id
    favoritos = listar_favoritos(telegram_user_id)

    if not favoritos:
        await update.message.reply_text(
            "Você ainda não tem bandas favoritas.\n\nExemplo: /favoritar Nightwish"
        )
        return

    lista = "\n".join([f"🎸 {banda}" for banda in favoritos])

    resposta = f"""
⭐ Suas bandas favoritas:

{lista}
"""

    await update.message.reply_text(resposta)


async def removerfavorito(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nome_banda = " ".join(context.args).strip()

    if not nome_banda:
        await update.message.reply_text("Exemplo: /removerfavorito Nightwish")
        return

    telegram_user_id = update.effective_user.id
    removido = remover_favorito(telegram_user_id, nome_banda)

    if removido:
        await update.message.reply_text(f"🗑️ {nome_banda.title()} foi removida dos seus favoritos.")
    else:
        await update.message.reply_text(f"⚠️ {nome_banda.title()} não estava nos seus favoritos.")


def main():
    if not TOKEN:
        raise ValueError("Token do Telegram não encontrado.")

    inicializar_banco()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("banda", banda))
    app.add_handler(CommandHandler("clipe", clipe))
    app.add_handler(CommandHandler("imagem", imagem))
    app.add_handler(CommandHandler("album", album))
    app.add_handler(CommandHandler("musica", musica))
    app.add_handler(CommandHandler("recomenda", recomenda))
    app.add_handler(CommandHandler("favoritar", favoritar))
    app.add_handler(CommandHandler("meusfavoritos", meusfavoritos))
    app.add_handler(CommandHandler("removerfavorito", removerfavorito))

    print("Bot rodando...")
    app.run_polling()


if __name__ == "__main__":
    main()