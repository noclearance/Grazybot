import os
import sys
import subprocess
import importlib
import traceback
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

LOG_DIR = Path("logs")
LOG_FILE = LOG_DIR / "diagnostics.log"

def log(message):
    """Logs message to console and file."""
    print(message)
    LOG_DIR.mkdir(exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n")

def check_python_version():
    log("🔍 Checking Python version...")
    version = sys.version_info
    if version < (3, 10):
        log(f"❌ Python {version.major}.{version.minor} detected. Requires 3.10+.")
    else:
        log(f"✅ Python {version.major}.{version.minor} OK.")

def check_env_vars(required_vars=None):
    log("🔍 Checking environment variables...")
    load_dotenv()
    missing = []
    for var in required_vars or ["DISCORD_TOKEN"]:
        if not os.getenv(var):
            missing.append(var)
    if missing:
        log(f"⚠️ Missing environment variables: {', '.join(missing)}")
    else:
        log("✅ All required environment variables are set.")

def check_requirements():
    log("🔍 Checking dependencies from requirements.txt...")
    if not Path("requirements.txt").exists():
        log("⚠️ No requirements.txt found.")
        return

    with open("requirements.txt", "r") as f:
        packages = [line.strip().split("==")[0] for line in f if line.strip()]

    for pkg in packages:
        try:
            importlib.import_module(pkg)
        except ImportError:
            log(f"❌ Missing package: {pkg}. Run `pip install {pkg}`.")
        except Exception as e:
            log(f"⚠️ Issue importing {pkg}: {e}")
        else:
            log(f"✅ {pkg} imported successfully.")

def check_cogs():
    log("🔍 Checking cogs directory for broken modules...")
    cogs_dir = Path("cogs")
    if not cogs_dir.exists():
        log("⚠️ No 'cogs' directory found.")
        return

    for file in cogs_dir.glob("*.py"):
        name = file.stem
        try:
            importlib.import_module(f"cogs.{name}")
            log(f"✅ Cog '{name}' loaded successfully.")
        except Exception as e:
            log(f"❌ Cog '{name}' failed to load: {type(e).__name__} - {e}")

def test_runtime():
    log("🔍 Running lightweight runtime probe...")
    try:
        if Path("bot.py").exists():
            code = compile(Path("bot.py").read_text(), "bot.py", "exec")
            exec(code, {"__name__": "__test__"})
            log("✅ Runtime probe passed (no immediate syntax issues).")
        else:
            log("⚠️ bot.py not found.")
    except Exception as e:
        log("❌ Runtime error detected:")
        log(traceback.format_exc())

def main():
    log("\n=== 🧠 Grazybot Diagnostics ===")
    check_python_version()
    check_env_vars()
    check_requirements()
    check_cogs()
    test_runtime()
    log("=== 🔧 Diagnostics Complete ===\n")

if __name__ == "__main__":
    main()
