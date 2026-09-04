from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent
SERVER_DIR = WEB_DIR.parent

CONFIG_FILE = SERVER_DIR / "server_config.json"
GAME_LOG = SERVER_DIR / "valheim_server.log"
BACKUP_DIR = SERVER_DIR / "backups"
LOGS_DIR = SERVER_DIR / "logs"
SERVER_BIN = SERVER_DIR / "valheim_server.x86_64"
LINUX64_DIR = SERVER_DIR / "linux64"
USERS_FILE = SERVER_DIR / "users.json"
PLAYER_HISTORY_FILE = SERVER_DIR / "player_history.json"
SERVER_HISTORY_FILE = SERVER_DIR / "server_history.json"
SERVER_HISTORY_LOG = LOGS_DIR / "server_history.log"
WORLD_SAVE_DIR = Path.home() / ".config/unity3d/IronGate/Valheim"
