import requests
from pathlib import Path

# --- Paramètres à remplir ---
tenant_id = "TON_TENANT_ID"
client_id = "TON_CLIENT_ID"
client_secret = "TON_CLIENT_SECRET"

# --- Endpoint OAuth2 ---
url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

payload = {
    "grant_type": "client_credentials",
    "client_id": client_id,
    "client_secret": client_secret,
    "scope": "https://graph.microsoft.com/.default"
}

# --- Requête pour récupérer le token ---
resp = requests.post(url, data=payload)
resp.raise_for_status()
token = resp.json().get("access_token")

if token:
    print("✅ Token récupéré avec succès !")

    # --- Créer secrets.toml pour Streamlit ---
    secrets_path = Path("secrets.toml")
    with secrets_path.open("w") as f:
        f.write(f'graph_token = "{token}"\n')

    print(f"✅ Token sauvegardé dans {secrets_path.resolve()}")
else:
    print("❌ Impossible de récupérer le token.")
