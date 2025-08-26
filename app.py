import streamlit as st
import pandas as pd
import requests
import json
from typing import Dict, List, Tuple

# ------------------- Config UI -------------------
st.set_page_config(page_title="Microsoft Roles Dashboard", layout="wide")
st.title("Microsoft Roles Dashboard")
st.caption("Affiche uniquement les rôles **effectivement attribués** dans Entra ID et Purview.")

# ------------------- Auth Service Principal -------------------
TENANT_ID = st.secrets["AZURE_TENANT_ID"]
CLIENT_ID = st.secrets["AZURE_CLIENT_ID"]
CLIENT_SECRET = st.secrets["AZURE_CLIENT_SECRET"]
PURVIEW_ACCOUNT = st.secrets.get("PURVIEW_ACCOUNT", "")

def get_token(tenant_id: str, client_id: str, client_secret: str, scope: str) -> str:
    url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": scope
    }
    r = requests.post(url, data=data, timeout=60)
    r.raise_for_status()
    token = r.json().get("access_token")
    if not token:
        raise RuntimeError("Impossible de récupérer le token.")
    return token

@st.cache_data(show_spinner=False, ttl=55*60)
def get_token_cached(scope: str):
    return get_token(TENANT_ID, CLIENT_ID, CLIENT_SECRET, scope)

# ------------------- Helpers Graph -------------------
GRAPH = "https://graph.microsoft.com/v1.0"

def auth_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}

def graph_get_all(token: str, url: str) -> List[dict]:
    items = []
    headers = auth_headers(token)
    if "$count=true" in url:
        headers["ConsistencyLevel"] = "eventual"
    next_url = url
    while next_url:
        r = requests.get(next_url, headers=headers, timeout=60)
        r.raise_for_status()
        data = r.json()
        items.extend(data.get("value", []))
        next_url = data.get("@odata.nextLink")
    return items

def graph_batch(token: str, requests_list: List[dict]) -> List[dict]:
    results = []
    headers = auth_headers(token)
    headers["Content-Type"] = "application/json"
    for i in range(0, len(requests_list), 20):
        chunk = requests_list[i:i+20]
        payload = {"requests": []}
        for idx, req in enumerate(chunk, start=1):
            payload["requests"].append({
                "id": str(idx),
                "method": req.get("method", "GET"),
                "url": req["url"].lstrip("/"),
                "headers": req.get("headers", {})
            })
        r = requests.post(f"{GRAPH}/$batch", headers=headers, data=json.dumps(payload), timeout=90)
        r.raise_for_status()
        resp_sorted = sorted(r.json().get("responses", []), key=lambda x: int(x["id"]))
        results.extend(resp_sorted)
    return results

# ------------------- EntraID Roles -------------------
@st.cache_data(show_spinner=True, ttl=20*60)
def fetch_directory_role_assignments(token: str) -> List[dict]:
    url = f"{GRAPH}/roleManagement/directory/roleAssignments?$top=999&$expand=roleDefinition"
    return graph_get_all(token, url)

# Principal resolution (simplifiée)
def resolve_principals(token: str, principal_ids: List[str]) -> Dict[str, dict]:
    principal_map = {}
    if not principal_ids:
        return principal_map
    reqs = [{"url": f"/directoryObjects/{pid}", "method": "GET"} for pid in principal_ids]
    resps = graph_batch(token, reqs)
    for pid, resp in zip(principal_ids, resps):
        if resp.get("status") == 200:
            body = resp.get("body", {})
            otype = body.get("@odata.type", "")
            principal_map[pid] = {
                "displayName": body.get("displayName", body.get("userPrincipalName", pid)),
                "type": otype.split(".")[-1]
            }
        else:
            principal_map[pid] = {"displayName": pid, "type": "Unknown"}
    return principal_map

# Expand group members
def expand_group_members_users(token: str, group_id: str) -> List[dict]:
    url = f"{GRAPH}/groups/{group_id}/transitiveMembers?$select=id,displayName,userPrincipalName&$top=999"
    members = graph_get_all(token, url)
    users = []
    for m in members:
        if "@odata.type" in m and "user" in m["@odata.type"]:
            users.append({
                "id": m.get("id"),
                "displayName": m.get("displayName", ""),
                "userPrincipalName": m.get("userPrincipalName", "")
            })
    return users

def build_assigned_roles_dataframe(assignments: List[dict], principals: Dict[str, dict], expand_groups: bool) -> pd.DataFrame:
    rows = []
    for a in assignments:
        role_def = a.get("roleDefinition") or {}
        role_name = role_def.get("displayName", "Unknown role")
        role_id = role_def.get("id", "")
        principal_id = a.get("principalId")
        scope = a.get("directoryScopeId") or "/"
        principal = principals.get(principal_id, {"type": "unknown", "displayName": principal_id})

        if principal.get("type") == "user":
            rows.append({
                "Rôle": role_name,
                "RoleDefinitionId": role_id,
                "Portée": scope,
                "TypePrincipal": "User",
                "Affécté à": principal.get("displayName"),
                "UPN / App / Groupe": principal.get("userPrincipalName", "")
            })
        elif principal.get("type") == "group":
            if expand_groups:
                try:
                    members = expand_group_members_users(get_token_cached("https://graph.microsoft.com/.default"), principal_id)
                    for u in members:
                        rows.append({
                            "Rôle": role_name,
                            "RoleDefinitionId": role_id,
                            "Portée": scope,
                            "TypePrincipal": "User (via groupe)",
                            "Affécté à": u.get("displayName"),
                            "UPN / App / Groupe": u.get("userPrincipalName", "")
                        })
                except:
                    rows.append({
                        "Rôle": role_name,
                        "RoleDefinitionId": role_id,
                        "Portée": scope,
                        "TypePrincipal": "Group",
                        "Affécté à": principal.get("displayName"),
                        "UPN / App / Groupe": ""
                    })
            else:
                rows.append({
                    "Rôle": role_name,
                    "RoleDefinitionId": role_id,
                    "Portée": scope,
                    "TypePrincipal": "Group",
                    "Affécté à": principal.get("displayName"),
                    "UPN / App / Groupe": ""
                })
        else:
            rows.append({
                "Rôle": role_name,
                "RoleDefinitionId": role_id,
                "Portée": scope,
                "TypePrincipal": principal.get("type"),
                "Affécté à": principal.get("displayName"),
                "UPN / App / Groupe": ""
            })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["Rôle", "TypePrincipal", "Affécté à"]).reset_index(drop=True)
    return df

# ------------------- Purview Roles -------------------
def get_purview_token_cached():
    return get_token_cached("https://purview.azure.net/.default")

@st.cache_data(show_spinner=True, ttl=20*60)
def fetch_purview_assignments() -> List[dict]:
    if not PURVIEW_ACCOUNT:
        return []
    token = get_purview_token_cached()
    headers = {"Authorization": f"Bearer {token}"}
    url = f"https://{PURVIEW_ACCOUNT}.purview.azure.com/accessControl/roleAssignments?api-version=2021-07-01"
    r = requests.get(url, headers=headers, timeout=60)
    r.raise_for_status()
    return r.json().get("value", [])

@st.cache_data(show_spinner=True, ttl=20*60)
def resolve_principals_purview(principal_ids: List[str]) -> Dict[str, dict]:
    token = get_token_cached("https://graph.microsoft.com/.default")
    principal_map = {}
    if not principal_ids:
        return principal_map
    reqs = [{"url": f"/directoryObjects/{pid}", "method": "GET"} for pid in principal_ids]
    resps = graph_batch(token, reqs)
    for pid, resp in zip(principal_ids, resps):
        if resp.get("status") == 200:
            body = resp.get("body", {})
            otype = body.get("@odata.type", "")
            principal_map[pid] = {
                "displayName": body.get("displayName", body.get("userPrincipalName", pid)),
                "type": otype.split(".")[-1]
            }
        else:
            principal_map[pid] = {"displayName": pid, "type": "Unknown"}
    return principal_map

def build_purview_dataframe(assignments: List[dict]) -> pd.DataFrame:
    principal_ids = [a["properties"]["principalId"] for a in assignments if "principalId" in a["properties"]]
    principals = resolve_principals_purview(principal_ids)
    rows = []
    for a in assignments:
        props = a["properties"]
        principal_id = props["principalId"]
        role_id = props["roleDefinitionId"]
        role_name = role_id.split("/")[-1]
        principal = principals.get(principal_id, {"displayName": principal_id, "type": "Unknown"})
        rows.append({
            "Rôle": role_name,
            "TypePrincipal": principal["type"],
            "Affécté à": principal["displayName"],
            "UPN / App / Groupe": "",
            "Portée": props.get("scope", "/")
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["Rôle", "TypePrincipal", "Affécté à"]).reset_index(drop=True)
    return df

# ------------------- UI Controls -------------------
col1, col2 = st.columns([1,1])
with col1:
    expand_groups = st.checkbox("Développer les groupes en membres", value=True)
with col2:
    show_only_users = st.checkbox("Masquer SP & Groupes", value=False)

# ------------------- Tabs -------------------
tab1, tab2 = st.tabs(["Entra ID", "Purview"])

with tab1:
    st.subheader("Entra ID - Rôles attribués")
    if st.button("Charger les rôles EntraID"):
        token = get_token_cached("https://graph.microsoft.com/.default")
        assignments = fetch_directory_role_assignments(token)
        if not assignments:
            st.info("Aucune assignation EntraID trouvée.")
        else:
            principal_ids = list({a.get("principalId") for a in assignments if a.get("principalId")})
            principals = resolve_principals(token, principal_ids)
            df_entraid = build_assigned_roles_dataframe(assignments, principals, expand_groups)
            if show_only_users:
                df_entraid = df_entraid[df_entraid["TypePrincipal"].str.startswith("User")]
            search = st.text_input("Recherche (rôle, principal, UPN, portée)…", key="search_entraid")
            if search:
                s = search.lower()
                df_entraid = df_entraid[df_entraid.apply(lambda r: any(s in str(v).lower() for v in r.values), axis=1)]
            st.dataframe(df_entraid, use_container_width=True, hide_index=True)
            if not df_entraid.empty:
                st.download_button("Exporter CSV EntraID", df_entraid.to_csv(index=False).encode("utf-8"), file_name="entra_roles.csv")

with tab2:
    st.subheader("Purview - Rôles internes attribués")
    if st.button("Charger les rôles Purview"):
        assignments = fetch_purview_assignments()
        if not assignments:
            st.info("
