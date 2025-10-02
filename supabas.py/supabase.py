import os
from dotenv import load_dotenv
from supabase import create_client, Client

# Load environment variables from .env
load_dotenv()

# Get Supabase credentials
url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")

if not url or not key:
	raise ValueError("SUPABASE_URL and SUPABASE_KEY environment variables must be set.")

# Create the Supabase client
supabase: Client = create_client(url, key)