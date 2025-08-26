import requests

# ---------- Azure ----------
def get_azure_roles(token):
    url = "https://management.azure.com/providers/Microsoft.Authorization/roleDefinitions?api-version=2022-04-01"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    roles = resp.json().get("value", [])
    return [{"Service": "Azure", "Role": r["properties"]["roleName"], "Description": r["properties"]["description"]} for r in roles]

# ---------- Teams ----------
def get_teams_roles(token, team_id):
    url = f"https://graph.microsoft.com/v1.0/teams/{team_id}/members"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    members = resp.json().get("value", [])
    result = []
    for m in members:
        roles = m.get("roles", [])
        role_name = roles[0] if roles else "Member"
        result.append({"Service": "Teams", "User": m["displayName"], "Role": role_name})
    return result

# ---------- Intune ----------
def get_intune_roles(token):
    url = "https://graph.microsoft.com/v1.0/deviceManagement/roleAssignments"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    assignments = resp.json().get("value", [])
    result = []
    for a in assignments:
        role_name = a.get("roleDefinition", {}).get("displayName", "Unknown")
        result.append({"Service": "Intune", "Role": role_name, "Description": a.get("description", "")})
    return result

# ---------- Purview ----------
def get_purview_roles(token, account_name):
    url = f"https://{account_name}.purview.azure.com/catalog/api/atlas/v2/roles"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        return []
    roles = resp.json().get("roles", [])
    return [{"Service": "Purview", "Role": r["name"], "Description": r.get("description", "")} for r in roles]
