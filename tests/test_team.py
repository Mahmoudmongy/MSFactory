"""Team management: one company, several logins with different roles.

The client asked to run a single company temporarily with more than one user,
so these tests prove the owner can invite teammates, that each role sees only
what it should, and that the last owner cannot be locked out.
"""
from __future__ import annotations

from tests.test_api import auth, register


def _member(client, token, email, role="viewer", password="Str0ng!Pass"):
    response = client.post(
        "/api/v1/team",
        headers=auth(token),
        json={"email": email, "role": role, "full_name": email.split("@")[0], "password": password},
    )
    assert response.status_code == 201, response.text
    return response.json()


class TestTeam:
    def test_owner_invites_a_team_with_distinct_roles(self, client):
        token = register(client, email="owner@team.eg", company="شركة الفريق")
        accountant = _member(client, token, "accountant@team.eg", role="accountant")
        sales = _member(client, token, "sales@team.eg", role="sales")
        viewer = _member(client, token, "viewer@team.eg", role="viewer")

        members = client.get("/api/v1/team", headers=auth(token)).json()
        roles = {m["email"]: m["role"] for m in members}
        # The owner who registered is a member too.
        assert roles["owner@team.eg"] == "owner"
        assert roles["accountant@team.eg"] == "accountant"
        assert roles["sales@team.eg"] == "sales"
        assert roles["viewer@team.eg"] == "viewer"
        assert accountant["id"] != sales["id"] != viewer["id"]

    def test_invited_user_can_log_in_to_the_same_company(self, client):
        token = register(client, email="owner2@team.eg", company="شركة الفريق")
        _member(client, token, "mate@team.eg", role="accountant")

        login = client.post(
            "/api/v1/auth/login", json={"email": "mate@team.eg", "password": "Str0ng!Pass"}
        )
        assert login.status_code == 200, login.text
        mate_token = login.json()["access_token"]

        me = client.get("/api/v1/auth/me", headers=auth(mate_token)).json()
        assert [m["company"]["name"] for m in me["memberships"]] == ["شركة الفريق"]

    def test_viewer_role_is_read_only(self, client):
        token = register(client, email="owner3@team.eg", company="شركة الفريق")
        _member(client, token, "look@team.eg", role="viewer")
        viewer_token = client.post(
            "/api/v1/auth/login", json={"email": "look@team.eg", "password": "Str0ng!Pass"}
        ).json()["access_token"]

        assert client.get("/api/v1/contacts", headers=auth(viewer_token)).status_code == 200
        forbidden = client.post(
            "/api/v1/contacts",
            headers=auth(viewer_token),
            json={"name": "عميل جديد", "kind": "customer"},
        )
        assert forbidden.status_code == 403

    def test_role_can_be_changed(self, client):
        token = register(client, email="owner4@team.eg", company="شركة الفريق")
        member = _member(client, token, "promote@team.eg", role="viewer")

        response = client.patch(
            f"/api/v1/team/{member['id']}", headers=auth(token), json={"role": "accountant"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["role"] == "accountant"

    def test_last_owner_cannot_be_demoted(self, client):
        token = register(client, email="owner5@team.eg", company="شركة الفريق")
        members = client.get("/api/v1/team", headers=auth(token)).json()
        owner = next(m for m in members if m["role"] == "owner")

        response = client.patch(
            f"/api/v1/team/{owner['id']}", headers=auth(token), json={"role": "viewer"}
        )
        assert response.status_code == 409

    def test_duplicate_member_is_rejected(self, client):
        token = register(client, email="owner6@team.eg", company="شركة الفريق")
        _member(client, token, "dup@team.eg", role="sales")
        again = client.post(
            "/api/v1/team",
            headers=auth(token),
            json={"email": "dup@team.eg", "role": "viewer", "password": "Str0ng!Pass"},
        )
        assert again.status_code == 409

    def test_roles_endpoint_lists_arabic_labels(self, client):
        token = register(client, email="owner7@team.eg", company="شركة الفريق")
        roles = client.get("/api/v1/team/roles", headers=auth(token)).json()
        labels = {r["value"]: r["label"] for r in roles}
        assert labels["owner"] == "مالك"
        assert labels["accountant"] == "محاسب"
