import os

from dotenv import load_dotenv

from sandbox.sandbox import Sandbox

load_dotenv()  # reads .env in the current folder into os.environ

REPO_URL = os.getenv("REPO_URL")
if not REPO_URL:
    raise SystemExit("REPO_URL is not set. Add it to .env (see .env.example).")

sb = Sandbox(REPO_URL)
try:
    print(sb.run("ls -la && git log --oneline -3"))
finally:
    sb.close()
