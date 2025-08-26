import streamlit as st
import pandas as pd
import requests
from typing import Dict, List, Tuple
import math
import json

# --------------- Config UI ---------------
st.set_page_config(page_title="Entra ID – Rôles attribués", layout="wide")
st.title("Entra ID – Rôles attribués (utilisateurs visibles)")
st.caption("Affiche uniquement les rôles **effectivement attribués**. Option pour développer les groupes en membres.")

# --------------- Auth (Service principal) ---------------
tenant_id = st.secrets["AZURE_TENANT_ID"]
client_id = st.secrets["AZURE_CLIENT_ID"]
client_secret = st.secrets["AZURE_CLIENT_SECRET"]

def get_access_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    data = {
        "grant_type": "client_credentials",
        "CLIENT_ID": client_id,
        "CLIENT_SECRET": client_secret,
        "scope": "https://graph.microsoft.com/.default",
    }
    r = requests.post(url, data=data, timeout=60)
    r.raise_for_status()
    token = r.json().get("access_token")
    if not token:
        raise RuntimeError("Impossible de récupérer un access_token.")
    return token

@st.cache_data(show_spinner=False, ttl=55 * 60)
def get_token_cached() -> str:
    return get_access_token(TENANT_ID, CLIENT_ID, CLIENT_SECRET)

# --------------- Helpers Graph ---------------
GRAPH = "https://graph.microsoft.com/v1.0"

def auth_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}

def graph_get_all(token: str, url: str) -> List[dict]:
    """Récupère toutes les pages d'un endpoint Graph (suivi de @odata.nextLink)."""
    items = []
    headers = auth_headers(token)
    # Pour certains endpoints, $count nécessite ce header
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
    """Exécute un batch Graph (max 20 requêtes/batch). Retourne la liste des réponses dans l’ordre d’envoi."""
    results = []
    headers = auth_headers(token)
    headers["Content-Type"] = "application/json"
    # chunk par 20
    for i in range(0, len(requests_list), 20):
        chunk = requests_list[i : i + 20]
        payload = {"requests": []}
        # Graph exige des id string uniques
        for idx, req in enumerate(chunk, start=1):
            payload["requests"].append(
                {
                    "id": str(idx),
                    "method": req.get("method", "GET"),
                    "url": req["url"].lstrip("/"),
                    "headers": req.get("headers", {}),
                }
            )
        r = requests.post(f"{GRAPH}/$batch", headers=headers, data=json.dumps(payload), timeout=90)
        r.raise_for_status()
        resp = r.json().get("responses", [])
        # On range dans le même ordre que chunk (id 1..n)
        # Graph renvoie dans un ordre quelconque → on trie par id
        resp_sorted = sorted(resp, key=lambda x: int(x["id"]))
        results.extend(resp_sorted)
    return results

# --------------- Collecte des rôles attribués (unifiedRoleAssignments) ---------------
@st.cache_data(show_spinner=True, ttl=20 * 60)
def fetch_directory_role_assignments(token: str) -> List[dict]:
    # unifiedRoleAssignment pour Entra ID (directory scope)
    # On étend avec roleDefinition via $expand (supporté en v1.0)
    url = (
        f"{GRAPH}/roleManagement/directory/roleAssignments"
        "?$top=999"
        "&$expand=roleDefinition"
    )
    return graph_get_all(token, url)

# --------------- Résolution des principals (user/group/servicePrincipal) ---------------
def resolve_principals(token: str, principal_ids: List[str]) -> Dict[str, dict]:
    """Retourne un dict {principalId: {type, displayName, userPrincipalName, ...}}."""
    principal_map: Dict[str, dict] = {}
    if not principal_ids:
        return principal_map

    # Étape 1 : /directoryObjects/{id} pour connaître le type (@odata.type)
    reqs = [{"url": f"/directoryObjects/{pid}", "method": "GET"} for pid in principal_ids]
    meta_resps = graph_batch(token, reqs)

    # Prépare des lots à récupérer selon le type
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

    # Étape 2 : détail des objets par type (batch GET)
    def batch_detail(ids: List[str], url_prefix: str, select: str = "") -> List[Tuple[str, dict]]:
        if not ids:
            return []
        reqs = [{"url": f"/{url_prefix}/{pid}{select}", "method": "GET"} for pid in ids]
        resps = graph_batch(token, reqs)
        out = []
        for pid, resp in zip(ids, resps):
            if resp.get("status") == 200:
                out.append((pid, resp.get("body", {})))
            else:
                out.append((pid, {}))
        return out

    # Users
    for pid, body in batch_detail(user_ids, "users", "?$select=id,displayName,userPrincipalName,mail"):
        principal_map[pid] = {
            "type": "user",
            "displayName": body.get("displayName", pid),
            "userPrincipalName": body.get("userPrincipalName") or body.get("mail") or "",
            "mail": body.get("mail") or "",
        }

    # Groups
    for pid, body in batch_detail(group_ids, "groups", "?$select=id,displayName,mail"):
        principal_map[pid] = {
            "type": "group",
            "displayName": body.get("displayName", pid),
            "mail": body.get("mail") or "",
        }

    # Service Principals
    for pid, body in batch_detail(sp_ids, "servicePrincipals", "?$select=id,displayName,appId"):
        principal_map[pid] = {
            "type": "servicePrincipal",
            "displayName": body.get("displayName", pid),
            "appId": body.get("appId") or "",
        }

    return principal_map

# --------------- Expansion des groupes en membres utilisateurs ---------------
@st.cache_data(show_spinner=True, ttl=20 * 60)
def expand_group_members_users(token: str, group_id: str) -> List[dict]:
    # transitiveMembers peut aussi renvoyer des devices/SP → on filtre type user
    url = f"{GRAPH}/groups/{group_id}/transitiveMembers?$select=id,displayName,userPrincipalName&$top=999"
    members = graph_get_all(token, url)
    users = []
    for m in members:
        # Graph renvoie @odata.type
        if "@odata.type" in m and "user" in m["@odata.type"]:
            users.append(
                {
                    "id": m.get("id"),
                    "displayName": m.get("displayName", ""),
                    "userPrincipalName": m.get("userPrincipalName", ""),
                }
            )
    return users

# --------------- Construction du DataFrame final ---------------
def build_assigned_roles_dataframe(assignments: List[dict], principals: Dict[str, dict], expand_groups: bool) -> pd.DataFrame:
    rows: List[dict] = []
    for a in assignments:
        role_def = (a.get("roleDefinition") or {})
        role_name = role_def.get("displayName", "Unknown role")
        role_template_id = role_def.get("templateId", "")
        role_id = role_def.get("id", "")

        principal_id = a.get("principalId")
        scope = a.get("directoryScopeId") or "/"  # "/" = tenant
        principal = principals.get(principal_id, {"type": "unknown", "displayName": principal_id})

        # Si principal est utilisateur → ligne directe
        if principal.get("type") == "user":
            rows.append({
                "Rôle": role_name,
                "RoleDefinitionId": role_id,
                "TemplateId": role_template_id,
                "Portée": scope,
                "TypePrincipal": "User",
                "Affécté à": principal.get("displayName"),
                "UPN / App / Groupe": principal.get("userPrincipalName") or principal.get("mail") or "",
            })
        # Si principal est groupe → soit on affiche le groupe, soit on développe en membres
        elif principal.get("type") == "group":
            if expand_groups:
                # développer en membres utilisateurs
                try:
                    members = expand_group_members_users(get_token_cached(), principal_id)
                    for u in members:
                        rows.append({
                            "Rôle": role_name,
                            "RoleDefinitionId": role_id,
                            "TemplateId": role_template_id,
                            "Portée": scope,
                            "TypePrincipal": "User (via groupe)",
                            "Affécté à": u.get("displayName"),
                            "UPN / App / Groupe": u.get("userPrincipalName", ""),
                        })
                except Exception:
                    # fallback: au moins montrer le groupe
                    rows.append({
                        "Rôle": role_name,
                        "RoleDefinitionId": role_id,
                        "TemplateId": role_template_id,
                        "Portée": scope,
                        "TypePrincipal": "Group",
                        "Affécté à": principal.get("displayName"),
                        "UPN / App / Groupe": principal.get("mail") or "",
                    })
            else:
                rows.append({
                    "Rôle": role_name,
                    "RoleDefinitionId": role_id,
                    "TemplateId": role_template_id,
                    "Portée": scope,
                    "TypePrincipal": "Group",
                    "Affécté à": principal.get("displayName"),
                    "UPN / App / Groupe": principal.get("mail") or "",
                })
        elif principal.get("type") == "servicePrincipal":
            rows.append({
                "Rôle": role_name,
                "RoleDefinitionId": role_id,
                "TemplateId": role_template_id,
                "Portée": scope,
                "TypePrincipal": "Service Principal",
                "Affécté à": principal.get("displayName"),
                "UPN / App / Groupe": principal.get("appId") or "",
            })
        else:
            rows.append({
                "Rôle": role_name,
                "RoleDefinitionId": role_id,
                "TemplateId": role_template_id,
                "Portée": scope,
                "TypePrincipal": "Unknown",
                "Affécté à": principal.get("displayName"),
                "UPN / App / Groupe": "",
            })

    df = pd.DataFrame(rows)
    # Important : n’afficher QUE les rôles attribués → c’est déjà le cas (on ne part que d’assignments)
    # Tri par rôle puis principal
    if not df.empty:
        df = df.sort_values(["Rôle", "TypePrincipal", "Affécté à"]).reset_index(drop=True)
    return df

# --------------- UI Controls ---------------
col1, col2, col3 = st.columns([1,1,2])
with col1:
    expand_groups = st.checkbox("Développer les groupes en membres", value=True,
                                help="Si coché, les groupes affectés à un rôle sont développés en utilisateurs membres (transitifs).")
with col2:
    show_only_users = st.checkbox("Masquer SP & Groupes", value=False,
                                  help="Filtrer pour ne voir que les utilisateurs finaux (y compris via groupe si déployé).")

# --------------- Run ---------------
if st.button("Charger les rôles attribués"):
    try:
        token = get_token_cached()

        # 1) Récupère les unifiedRoleAssignments (uniquement ce qui est AFFECTÉ)
        assignments = fetch_directory_role_assignments(token)
        if not assignments:
            st.info("Aucune assignation de rôle trouvée.")
            st.stop()

        # 2) Résout les principals (user/group/servicePrincipal)
        principal_ids = list({a.get("principalId") for a in assignments if a.get("principalId")})
        principals = resolve_principals(token, principal_ids)

        # 3) Construit le DataFrame final
        df = build_assigned_roles_dataframe(assignments, principals, expand_groups)

        # 4) Filtrage optionnel
        if show_only_users and not df.empty:
            df = df[df["TypePrincipal"].str.startswith("User")]

        # 5) Recherche texte
        search = st.text_input("Recherche (rôle, principal, UPN, portée)…")
        if search and not df.empty:
            s = search.lower()
            df = df[df.apply(lambda r: any(s in str(v).lower() for v in r.values), axis=1)]

        st.dataframe(df, use_container_width=True, hide_index=True)

        # 6) Vue par rôle (groupby) pour naviguer rapidement
        if not df.empty:
            st.subheader("Vue par rôle")
            roles = df["Rôle"].unique().tolist()
            choice = st.selectbox("Choisir un rôle pour détailler les affectations", roles)
            if choice:
                sub = df[df["Rôle"] == choice][["TypePrincipal", "Affécté à", "UPN / App / Groupe", "Portée"]]
                st.dataframe(sub.reset_index(drop=True), use_container_width=True, hide_index=True)

            # Export
            st.download_button("Exporter CSV", df.to_csv(index=False).encode("utf-8"), file_name="entra_roles_attribues.csv")

        st.success("Terminé ✅")

    except Exception as e:
        st.error(f"Erreur : {e}")
        st.stop()

# --------------- Notes d’autorisations ---------------
with st.expander("Pré-requis & permissions (à ouvrir si besoin)"):
    st.markdown("""
- **Service principal** avec permissions **Application** (admin consent requis) :
  - `RoleManagement.Read.Directory`
  - `Directory.Read.All`
  - (optionnel pour l’expansion de groupes) `Group.Read.All`
- Les assignations PIM **actives** apparaissent ici. Les *éligibles/non activées* ne seront pas visibles (sinon, utiliser les APIs PIM en plus).
    """)
