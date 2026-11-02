"""PES 2013 - sincronizacao via Rclone (Google Drive).
Execute apenas em um computador por vez; nao feche o terminal durante o upload.
"""
from pathlib import Path
from datetime import datetime
import hashlib
import json
import logging
import msvcrt
import os
import shutil
import subprocess
import sys
import tempfile

JOGO = Path(r"C:\Program Files (x86)\KONAMI\Pro Evolution Soccer 2013\pes2013.exe")
SAVE_LOCAL = Path.home() / "Documents" / "KONAMI" / "Pro Evolution Soccer 2013" / "save"
NUVEM = "backup_nuvem:PES2013_Sync/save"
BACKUP_NUVEM = "backup_nuvem:PES2013_Sync/backups"
BASE = Path(__file__).resolve().parent
ESTADO = BASE / "estado_sync.json"
BACKUPS = BASE / "backups"
LOG = BASE / "pes_sync.log"
LOCK = BASE / "pes_sync.lock"

logging.basicConfig(filename=LOG, level=logging.INFO,
                    format="%(asctime)s | %(levelname)s | %(message)s",
                    encoding="utf-8")


def registrar(mensagem):
    print(mensagem, flush=True)
    logging.info(mensagem)


def rclone(*args):
    resultado = subprocess.run(["rclone", *args], capture_output=True, text=True)
    if resultado.returncode:
        raise RuntimeError("Rclone falhou: " +
                           (resultado.stderr.strip() or f"codigo {resultado.returncode}"))
    return resultado


def inventario(pasta):
    dados = {}
    for arquivo in pasta.rglob("*"):
        if not arquivo.is_file():
            continue
        sha = hashlib.sha256()
        with arquivo.open("rb") as f:
            for bloco in iter(lambda: f.read(1024 * 1024), b""):
                sha.update(bloco)
        dados[arquivo.relative_to(pasta).as_posix()] = sha.hexdigest()
    return dados


def carregar_estado():
    if not ESTADO.exists():
        return None
    dados = json.loads(ESTADO.read_text(encoding="utf-8"))
    if not isinstance(dados, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                               for k, v in dados.items()):
        raise RuntimeError("estado_sync.json invalido; nao altere nem exclua sem backup.")
    return dados


def salvar_estado(dados):
    temp = ESTADO.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(dados, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    temp.replace(ESTADO)


def baixar_nuvem(destino):
    # Verifica acesso e existencia. O stdout de lsf nao e mostrado.
    rclone("lsf", NUVEM)
    rclone("copy", NUVEM, str(destino))


def ler_nuvem():
    # Cada chamada faz uma leitura NOVA. Nao guardar snapshot antigo apos o jogo.
    with tempfile.TemporaryDirectory(prefix="pes2013_remote_") as temp:
        pasta = Path(temp)
        baixar_nuvem(pasta)
        return inventario(pasta)


def backup_local():
    nome = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    BACKUPS.mkdir(parents=True, exist_ok=True)
    destino = BACKUPS / nome
    shutil.copytree(SAVE_LOCAL, destino)
    if inventario(destino) != inventario(SAVE_LOCAL):
        raise RuntimeError("Falha na verificacao do backup local.")
    registrar(f"Backup local criado: {destino}")


def baixar_versao(remoto_esperado):
    backup_local()
    with tempfile.TemporaryDirectory(prefix="pes2013_download_") as temp:
        pasta = Path(temp)
        baixar_nuvem(pasta)
        if inventario(pasta) != remoto_esperado:
            raise RuntimeError("Nuvem mudou durante o download. Nenhum save foi substituido.")
        rclone("sync", str(pasta), str(SAVE_LOCAL))
    if inventario(SAVE_LOCAL) != remoto_esperado:
        raise RuntimeError("Download incompleto. Backup local foi preservado.")
    salvar_estado(remoto_esperado)
    registrar(f"Download confirmado ({len(remoto_esperado)} arquivos)")


def enviar_versao(remoto_esperado, motivo):
    # Preparar backup antes de alterar nuvem. Mantem arquivos substituidos em backups remotos.
    backup_local()
    if ler_nuvem() != remoto_esperado:
        raise RuntimeError("Nuvem mudou antes do upload. Envio bloqueado.")
    atual = inventario(SAVE_LOCAL)
    destino_backup = BACKUP_NUVEM + "/" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    registrar(f"Enviando saves ({motivo})...")
    rclone("sync", str(SAVE_LOCAL), NUVEM, "--backup-dir", destino_backup)
    if ler_nuvem() != atual:
        raise RuntimeError("Upload nao confirmado. Nao abra o PES em outro computador.")
    salvar_estado(atual)
    registrar(f"Upload confirmado ({len(atual)} arquivos)")


def preparar():
    local = inventario(SAVE_LOCAL)
    remoto = ler_nuvem()
    anterior = carregar_estado()

    if anterior is None:
        if local != remoto:
            raise RuntimeError("Primeira execucao: saves diferentes. Faça backup e alinhe manualmente antes de jogar.")
        salvar_estado(local)
        registrar(f"Estado inicial registrado ({len(local)} arquivos)")
    elif local == remoto:
        salvar_estado(local)  # Recupera inclusive um upload concluido antes de salvar o estado.
        registrar(f"Saves locais e remotos iguais ({len(local)} arquivos)")
    elif local != anterior and remoto == anterior:
        # Upload foi interrompido antes de modificar a nuvem.
        registrar("Alteracoes locais pendentes; recuperando upload anterior.")
        enviar_versao(remoto, "recuperacao de sessao interrompida")
    elif local == anterior and remoto != anterior:
        registrar("Google Drive possui uma versao nova; baixando.")
        baixar_versao(remoto)
    else:
        raise RuntimeError("CONFLITO: notebook e nuvem mudaram desde a ultima sincronizacao. "
                           "Nenhum arquivo foi sobrescrito. Preserve ambas as versoes.")


def iniciar_jogo():
    if not JOGO.is_file():
        raise FileNotFoundError(f"Executavel nao encontrado: {JOGO}")
    registrar("Abrindo PES 2013")
    processo = subprocess.Popen([str(JOGO)], cwd=str(JOGO.parent))
    codigo = processo.wait()
    registrar(f"PES 2013 encerrado (codigo: {codigo})")


def sincronizar_apos_jogo(referencia):
    local = inventario(SAVE_LOCAL)
    remoto = ler_nuvem()
    if remoto != referencia:
        raise RuntimeError("Nuvem foi modificada enquanto o jogo estava aberto. Upload bloqueado.")
    if local == referencia:
        registrar("Nenhuma alteracao para enviar")
    else:
        enviar_versao(referencia, "fim do jogo")


def main():
    logging.info("=== Inicio da execucao ===")
    if not SAVE_LOCAL.is_dir():
        logging.error("Pasta de saves nao encontrada: %s", SAVE_LOCAL)
        print(f"ERRO: pasta de saves nao encontrada: {SAVE_LOCAL}")
        return 1
    # Lock exclusivo SOMENTE neste Windows; nao e bloqueio distribuido.
    with LOCK.open("a+b") as trava:
        try:
            trava.seek(0)
            msvcrt.locking(trava.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            print("ERRO: esta automacao ja esta aberta neste computador.")
            return 1
        try:
            preparar()
            referencia = inventario(SAVE_LOCAL)
            iniciar_jogo()
            sincronizar_apos_jogo(referencia)
            registrar("=== Execucao concluida sem erros ===")
            return 0
        except Exception as erro:
            logging.exception("Falha na automacao")
            print(f"\nERRO: {erro}\nConsulte: {LOG}", flush=True)
            return 1
        finally:
            trava.seek(0)
            msvcrt.locking(trava.fileno(), msvcrt.LK_UNLCK, 1)


if __name__ == "__main__":
    sys.exit(main())
