import requests

# ---------- Azure ----------
def get_azure_roles(token):
    url = "https://management.azure.com/providers/Microsoft.Authorization/roleDefinitions?api-version=2022-04-01"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    roles = resp.json().get("value", [])
    result = []
    for r in roles:
        props = r.get("properties", {})
        role_name = props.get("roleName", "Unknown")
        description = props.get("description", "")
        result.append({"Service": "Azure", "Role": role_name, "Description": description})
    return result

# ---------- Teams ----------
def get_teams_roles(token, team_id):
    url = f"https://graph.microsoft.com/v1.0/teams/{team_id}/members"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    members = resp.json().get("value", [])
    result = []
    for m in members:
        display_name = m.get("displayName", "Unknown")
        roles = m.get("roles") or []  # sécurise si roles est None
        role_name = roles[0] if roles else "Member"
        result.append({"Service": "Teams", "User": display_name, "Role": role_name})
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
        role_def = a.get("roleDefinition") or {}
        role_name = role_def.get("displayName", "Unknown")
        description = a.get("description", "")
        result.append({"Service": "Intune", "Role": role_name, "Description": description})
    return result

# ---------- Purview ----------
def get_purview_roles(token, account_name):
    url = f"https://{account_name}.purview.azure.com/catalog/api/atlas/v2/roles"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(url, headers=headers)
        resp.raise_for_status()
        roles = resp.json().get("roles") or []
        result = []
        for r in roles:
            role_name = r.get("name", "Unknown")
            description = r.get("description", "")
            result.append({"Service": "Purview", "Role": role_name, "Description": description})
        return result
    except Exception:
        return []  # si Purview n'est pas disponible ou endpoint différent
