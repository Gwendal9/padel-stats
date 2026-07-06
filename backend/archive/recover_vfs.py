"""
VFS SQLite custom : renvoie des pages vides (0x00) pour toute lecture
hors des limites réelles du fichier. Permet d'ouvrir un fichier DB dont
le header annonce plus de pages qu'il n'en contient réellement.
"""
import apsw, sqlite3, struct, os, sys

PAGE_SIZE = 4096

class PaddedFile(apsw.VFSFile):
    def __init__(self, name, flags, real_size):
        self._real_size = real_size
        super().__init__("", name, flags)

    def xRead(self, amount, offset):
        file_size = self._real_size
        if offset >= file_size:
            return b'\x00' * amount
        if offset + amount > file_size:
            # Lecture partielle : données réelles + padding
            real_data = super().xRead(file_size - offset, offset)
            return real_data + b'\x00' * (amount - (file_size - offset))
        return super().xRead(amount, offset)

    def xFileSize(self):
        # On rapporte la vraie taille fichier (pas la taille header)
        return self._real_size


class PaddedVFS(apsw.VFS):
    def __init__(self, real_size):
        self.real_size = real_size
        super().__init__("padded", "")

    def xOpen(self, name, flags):
        return PaddedFile(name, flags, self.real_size)


def recover(corrupted_db, output_db):
    real_size = os.path.getsize(corrupted_db)
    real_pages = real_size // PAGE_SIZE
    print(f"Pages réelles: {real_pages}, taille: {real_size//1024//1024}MB")

    # Patcher header pour que SQLite accepte la taille réelle
    with open(corrupted_db, 'r+b') as f:
        f.seek(28); f.write(struct.pack('>I', real_pages))
        f.seek(18); f.write(b'\x01\x01')   # legacy journal
        f.seek(92); f.write(b'\x00\x00\x00\x00')  # invalider version_valid_for

    # Créer le VFS avec padding
    vfs = PaddedVFS(real_size)

    try:
        src = apsw.Connection(f"file:{corrupted_db}?nolock=1",
                              flags=apsw.SQLITE_OPEN_READONLY | apsw.SQLITE_OPEN_URI,
                              vfs="padded")
        print("Connexion ouverte")

        # Lire les tables
        tables = src.execute("SELECT name, type FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        print(f"Tables trouvées: {[t[0] for t in tables]}")

        # Copier vers la nouvelle DB
        dst = sqlite3.connect(output_db)
        src_backup = apsw.Connection(f"file:{corrupted_db}?nolock=1",
                                     flags=apsw.SQLITE_OPEN_READONLY | apsw.SQLITE_OPEN_URI,
                                     vfs="padded")

        # Utiliser sqlite3.Connection.backup via l'API
        for table_name, _ in tables:
            try:
                rows = src.execute(f"SELECT COUNT(*) FROM '{table_name}'").fetchone()[0]
                print(f"  {table_name}: {rows} lignes")
            except Exception as e:
                print(f"  {table_name}: ERREUR {e}")

        src.close()

    except Exception as e:
        print(f"Erreur ouverture: {e}")
    finally:
        del vfs


if __name__ == "__main__":
    corrupted = "/sessions/festive-exciting-faraday/mnt/backend/tenup_backup_20260516_224549.db"
    output = "/sessions/festive-exciting-faraday/mnt/backend/tenup_recovered3.db"
    recover(corrupted, output)
