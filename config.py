import os
import json
from dotenv import load_dotenv

load_dotenv()

# --- Secrets ---
TOKEN = os.getenv("DISCORD_TOKEN")
OCR_API_KEY = os.getenv("OCR_API_KEY")

# --- Load Server Settings ---
SETTINGS_FILE = "settings.json"

if not os.path.exists(SETTINGS_FILE):
    print("⚠️ WARNING: settings.json not found! Please copy settings.example.json to settings.json and fill it out.")
    SETTINGS = {}
else:
    with open(SETTINGS_FILE, "r") as f:
        SETTINGS = json.load(f)

# Channels
CHANNELS = SETTINGS.get("channels", {})
RACE_CHANNELS = CHANNELS.get("race_channels", [])
TEST_CHANNEL = CHANNELS.get("test_channel")

# Roles
ROLES = SETTINGS.get("roles", {})
ADMIN_ROLES = ROLES.get("admin_roles", [])
TESTER_ROLE = ROLES.get("tester_role")

# Maps a Channel ID to its specific Team Member Role ID
TEAM_MEMBER_ROLES = {int(k): v for k, v in ROLES.get("team_member_roles", {}).items()}

# Maps a Channel ID to a list of roles to ping for OFA
OFA_PING_ROLES = {int(k): v for k, v in ROLES.get("ofa_ping_roles", {}).items()}

# Maps a Timezone Role ID to its UTC offset
TIMEZONE_ROLES = {int(k): v for k, v in ROLES.get("timezone_roles", {}).items()}

# Config
NODES_PER_LAP = SETTINGS.get("nodes_per_lap", 5)
