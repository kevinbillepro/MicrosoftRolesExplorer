import streamlit as st
import pandas as pd
import requests
import json
from typing import Dict, List, Tuple

# --------------- Config UI ---------------
st.set_page_config(page_title="Entra ID – Rôles attribués", layout="wide")
st.title("Entra ID – Rôles attribués (utilisateurs visibles)")
st.caption("Affiche uniquement les rôles **effectivement attribués**. Option pour développer les groupes en membres.")

# --------------- Auth ---------------
TENANT_ID = st.secrets["AZURE_TENANT_ID"]
CLIENT_ID = st.secrets["AZURE_CLIENT_ID"]
CLIENT_SECRET = st.secrets["AZURE_CLIENT_SECRET"]

GRAPH = "https://graph.microsoft.com/v1.0"

def get_access_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "https://graph.microsoft.com/.default",
    }
    r = requests.post(url, data=data, timeout=60)
    r.raise_for_status()
    return r.json().get("access_token")

@st.cache_data(ttl=55 * 60)
def get_token_cached() -> str:
    return get_access_token(TENANT_ID, CLIENT_ID, CLIENT_SECRET)

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
                "headers": req.get("headers", {}),
            })
        r = requests.post(f"{GRAPH}/$batch", headers=headers, data=json.dumps(payload), timeout=90)
        r.raise_for_status()
        resp = r.json().get("responses", [])
        results.extend(sorted(resp, key=lambda x: int(x["id"])))
    return results

@st.cache_data(ttl=20 * 60)
def fetch_directory_role_assignments(token: str) -> List[dict]:
    url = f"{GRAPH}/roleManagement/directory/roleAssignments?$top=999&$expand=roleDefinition"
    return graph_get_all(token, url)

def resolve_principals(token: str, principal_ids: List[str]) -> Dict[str, dict]:
    principal_map = {}
    if not principal_ids:
        return principal_map

    reqs = [{"url": f"/directoryObjects/{pid}", "method": "GET"} for pid in principal_ids]
    meta_resps = graph_batch(token, reqs)

    user_ids, group_ids, sp_ids = [], [], []
    for pid, resp in zip(principal_ids, meta_resps):
        if resp.get("status") == 200:
            body = resp.get("body", {})
            otype = body.get("@odata.type", "")
            if "user" in otype:
                user_ids.append(pid)
            elif "group" in otype:
                group_ids.append(pid)
            elif "servicePrincipal" in otype:
                sp_ids.append(pid)
            else:
                principal_map[pid] = {"type": "unknown", "displayName": body.get("displayName", pid)}
        else:
            principal_map[pid] = {"type": "unknown", "displayName": pid}

    def batch_detail(ids: List[str], url_prefix: str, select: str = "") -> List[Tuple[str, dict]]:
        if not ids:
            return []
        reqs = [{"url": f"/{url_prefix}/{pid}{select}", "method": "GET"} for pid in ids]
        resps = graph_batch(token, reqs)
        return [(pid, resp.get("body", {})) if resp.get("status") == 200 else (pid, {}) for pid, resp in zip(ids, resps)]

    for pid, body in batch_detail(user_ids, "users", "?$select=id,displayName,userPrincipalName,mail"):
        principal_map[pid] = {
            "type": "user",
            "displayName": body.get("displayName", pid),
            "userPrincipalName": body.get("userPrincipalName") or body.get("mail") or "",
            "mail": body.get("mail") or "",
        }

    for pid, body in batch_detail(group_ids, "groups", "?$select=id,displayName,mail"):
        principal_map[pid] = {
            "type": "group",
            "displayName": body.get("displayName", pid),
            "mail": body.get("mail") or "",
        }

    for pid, body in batch_detail(sp_ids, "servicePrincipals", "?$select=id,displayName,appId"):
        principal_map[pid] = {
            "type": "servicePrincipal",
            "displayName": body.get("displayName", pid),
            "appId": body.get("appId") or "",
        }

    return principal_map

@st.cache_data(ttl=20 * 60)
def expand_group_members_users(token: str, group_id: str) -> List[dict]:
    url = f"{GRAPH}/groups/{group_id}/transitiveMembers?$select=id,displayName,userPrincipalName&$top=999"
    members = graph_get_all(token, url)
    return [
        {
            "id": m.get("id"),
            "displayName": m.get("displayName", ""),
            "userPrincipalName": m.get("userPrincipalName", ""),
        }
        for m in members if "@odata.type" in m and "user" in m["@odata.type"]
    ]

ROLES_SENSIBLES = [
    "Global Administrator",
    "Privileged Role Administrator",
    "Security Administrator",
    "Conditional Access Administrator",
    "User Administrator",
]

def build_assigned_roles_dataframe(assignments: List[dict], principals: Dict[str, dict], expand_groups: bool) -> pd.DataFrame:
    rows = []
    for a in assignments:
        role_def = a.get("roleDefinition", {})
        role_name = role_def.get("displayName", "Unknown role")
        role_template_id = role_def.get("templateId", "")
        role_id = role_def.get("id", "")
        principal_id = a.get("principalId")
        scope = a.get("directoryScopeId") or "/"
        principal = principals.get(principal_id, {"type": "unknown", "displayName": principal_id})

        def add_row(type_principal, display_name, upn):
            rows.append({
                "Rôle": role_name,
                "RoleDefinitionId": role_id,
                "TemplateId": role_template_id,
                "Portée": scope,
                "TypePrincipal": type_principal,
                "Affécté à": display_name,
                "UPN / App / Groupe": upn,
            })

        if principal["type"] == "user":
            add_row("User", principal["displayName"], principal.get("userPrincipalName", ""))
        elif principal["type"] == "group":
            if expand_groups:
                try:
                    members = expand_group_members_users(get_token_cached(), principal_id)
                    for u in members:
                        add_row("User (via groupe)", u["displayName"], u["userPrincipalName"])
                except Exception:
                    add_row("Group", principal["displayName"], principal.get("mail", ""))
            else:
                add_row("Group", principal["displayName"], principal.get("mail", ""))
        elif principal["type"] == "servicePrincipal":
            add_row("Service Principal", principal["displayName"], principal.get("appId", ""))
        else:
            add_row("Unknown", principal["displayName"], "")

    df = pd.DataFrame(rows)
    if not df.empty:
        df["Rôle critique"] = df["Rôle"].apply(lambda r: "⚠️ Oui" if r in ROLES_SENSIBLES else "Non")
        df = df.sort_values(["Rôle", "TypePrincipal", "Affécté à"]).reset_index(drop=True)
    return df

@st.cache_data(ttl=20 * 60)
def fetch_role_assignment_audit_logs(token: str) -> List[dict]:
    url = f"{GRAPH}/auditLogs/directoryAudits?$filter=activityDisplayName eq 'Add role assignment'&$top=100"
    return graph_get_all(token, url)

# --------------- UI Controls ---------------
col1, col2, col3 = st.columns([1,1,2])
expand_groups = col1.checkbox("Développer les groupes en membres", value=True)
show_only_users = col2.checkbox("Masquer SP & Groupes", value=False)
show_only_critical = col3.checkbox("Afficher uniquement les rôles critiques", value=False)

if st.button("Charger les rôles attribués"):
    try:
        token = get_token_cached()
        assignments = fetch_directory_role_assignments(token)
        if not assignments:
            st.info("Aucune assignation de rôle trouvée.")
            st.stop()

        principal_ids = list({a.get("principalId") for a in assignments if a.get("principalId")})
        principals = resolve_principals(token, principal_ids)
        df = build_assigned_roles_dataframe(assignments, principals, expand_groups)

        if show_only_users:
            df = df[df["TypePrincipal"].str.startswith("User")]
        if show_only_critical:
            df = df[df["Rôle critique"] == "⚠️ Oui"]

        search = st.text_input("Recherche (rôle, principal, UPN, portée)…")
        if search:
            s = search.lower()
            df = df[df.apply(lambda r: any(s in str(v).lower() for v in r.values), axis=1)]

        if "⚠️ Oui" in df["Rôle critique"].values:
            st.warning("⚠️ Des rôles critiques sont attribués. Vérifiez leur légitimité.")

        st.dataframe(df, use_container_width=True, hide_index=True)

        st.subheader("Vue par rôle")
        roles = df["Rôle"].unique().tolist()
        choice = st.selectbox("Choisir un rôle pour détailler les affectations", roles)
        if choice:
            sub = df[df["Rôle"] == choice][["TypePrincipal", "Affécté à", "UPN / App / Groupe", "Portée"]]
            st.dataframe(sub.reset_index(drop=True), use_container_width=True, hide_index=True)

        st.download_button("Exporter CSV", df.to_csv(index=False).encode("utf-8"), file_name="entra_roles_attribues.csv")
        st.success("Terminé ✅")

        with st.expander("🕵️ Mode Audit – Historique des assignations de rôles"):
            audit_logs = fetch_role_assignment_audit_logs(token)
            if audit_logs:
                audit_rows = []
                for log in audit_logs:
                    actor = log.get("initiatedBy", {}).get("user", {}).get("displayName", "Inconnu")
                    target = log.get("targetResources", [{}])[0].get("displayName", "Inconnu")
                    role = log.get("targetResources", [{}])[0].get("modifiedProperties", [{}])[0].get("newValue", "Rôle inconnu")
                    date = log.get("activityDateTime", "")
                    audit_rows.append({
                        "Date": date,
                        "Initiateur": actor,
                        "Cible": target,
                        "Rôle attribué": role
                    })
                audit_df = pd.DataFrame(audit_rows)
                st.dataframe(audit_df, use_container_width=True, hide_index=True)
            else:
                st.info("Aucune activité d'assignation de rôle trouvée.")

    except Exception as e:
        st.error(f"Erreur : {e}")
        st.stop()

with st.expander("Pré-requis & permissions (à ouvrir si besoin)"):
    st.markdown("""
- **Service principal** avec permissions **Application** (admin consent requis) :
  - `RoleManagement.Read.Directory`
  - `Directory.Read.All`
  - `Group.Read.All` (optionnel pour l’expansion de groupes)
- Les assignations PIM **actives** apparaissent ici. Les *éligibles/non activées* ne seront pas visibles (sinon, utiliser les APIs PIM en plus).
    """)
