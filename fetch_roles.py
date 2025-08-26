import requests

def get_azure_roles(token):
    url = "https://management.azure.com/providers/Microsoft.Authorization/roleDefinitions?api-version=2022-04-01"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers)
    return resp.json().get("value", [])

def get_teams_roles(token, team_id):
    url = f"https://graph.microsoft.com/v1.0/teams/{team_id}/members"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers)
    return resp.json().get("value", [])
