"""Settings from the environment that .mcp.json passes in (plugin userConfig)."""
import os
from dataclasses import dataclass
from pathlib import Path

# The author's deployment; a self-hosted setup overrides both through userConfig.
DEFAULT_BROKER_URL = "https://gdrive-reader-broker.gdrive-reader-broker.workers.dev"
DEFAULT_CLIENT_ID = "90797836930-srr3msslmq7c3ataf1ge29lt5nnec8ro.apps.googleusercontent.com"
DEFAULT_FOLDER = "https://drive.google.com/drive/folders/1AU6ZW-3NFxme6388YPe98bGWWh9q_E5Y"


@dataclass
class Config:
    folder: str
    broker_url: str
    client_id: str
    token_path: Path
    callback_port: int
    api_base: str

    @classmethod
    def from_env(cls) -> "Config":
        env = os.environ
        data_dir = env.get("CLAUDE_PLUGIN_DATA") or str(Path.home() / ".gdrive-reader")
        port = env.get("GDRIVE_CALLBACK_PORT", "").strip()
        return cls(
            folder=env.get("GDRIVE_FOLDER", "").strip() or DEFAULT_FOLDER,
            broker_url=env.get("GDRIVE_BROKER_URL", "").strip() or DEFAULT_BROKER_URL,
            client_id=env.get("GDRIVE_CLIENT_ID", "").strip() or DEFAULT_CLIENT_ID,
            token_path=Path(env.get("GDRIVE_TOKEN_PATH", "").strip() or Path(data_dir) / "token.json"),
            callback_port=int(port) if port.isdigit() else 0,
            api_base=env.get("GDRIVE_API_BASE", "").strip() or "https://www.googleapis.com",
        )
