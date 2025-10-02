class SupabaseStorage:
    def __init__(self, client):
        self.client = client

    def get_all_verified(self):
        resp = self.client.table("verified").select("discord_id,rsn").execute()
        rows = resp.data or []
        return {row["discord_id"]: row["rsn"] for row in rows}

    def upsert_verified(self, discord_id, rsn):
        self.client.table("verified").upsert({"discord_id": discord_id, "rsn": rsn}).execute()

    def delete_verified(self, discord_id):
        self.client.table("verified").delete().eq("discord_id", discord_id).execute()
