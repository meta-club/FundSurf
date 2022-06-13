import os
from supabase import create_client, Client as Cl
from dotenv import load_dotenv

load_dotenv()

url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY")
supabase: Cl = create_client(url, key)


