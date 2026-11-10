from pathlib import Path

import pytest

from app.agents.guardrails import GuardContext, check_shell_command, check_tool_call


@pytest.fixture
def ctx(tmp_path: Path) -> GuardContext:
    return GuardContext(cwd=tmp_path, current_branch=lambda: "feature/F001-login")


def blocked(cmd: str, ctx: GuardContext) -> bool:
    return check_shell_command(cmd, ctx) is not None


# ----------------------------------------------------------------- Azure / Terraform / cluster


@pytest.mark.parametrize("cmd", [
    "terraform apply",
    "terraform apply -auto-approve",
    "terraform -chdir=infra plan",
    "terraform destroy",
    "terraform state rm foo",
    "/usr/local/bin/terraform import a b",
    "cd infra && terraform apply",
    "TF_LOG=debug terraform apply",
    "sudo terraform apply",
    "bash -c 'terraform apply'",
    "echo $(terraform apply)",
    "tofu apply",
])
def test_terraform_changes_blocked(cmd, ctx):
    assert blocked(cmd, ctx)


@pytest.mark.parametrize("cmd", ["terraform fmt -check", "terraform validate", "terraform init -backend=false", "terraform version"])
def test_terraform_read_only_allowed(cmd, ctx):
    assert not blocked(cmd, ctx)


@pytest.mark.parametrize("cmd", [
    "az group create -n rg -l westeurope",
    "az aks delete -n x -g y",
    "az keyvault secret set --vault-name v -n s --value x",
    "az role assignment create --role Owner",
    "az acr update -n r --admin-enabled true",
    "az deployment group create -g rg -f main.bicep",
    "az resource tag --ids x --tags a=b",
])
def test_az_writes_blocked(cmd, ctx):
    assert blocked(cmd, ctx)


@pytest.mark.parametrize("cmd", [
    "az group show -n rg",
    "az aks list -o table",
    "az acr repository list -n reg",
    "az keyvault secret list-versions --vault-name v -n s",
    "az account show",
    "az account set --subscription abc",
    "az login --service-principal -u x -p y --tenant t",
    "az resource list --query '[].name'",
])
def test_az_reads_allowed(cmd, ctx):
    assert not blocked(cmd, ctx)


@pytest.mark.parametrize("cmd", ["kubectl apply -f deploy/", "kubectl delete pod x", "kubectl -n app scale deploy/x --replicas=0",
                                 "helm install x ./chart", "helm upgrade x ./chart", "kubectl config use-context prod"])
def test_cluster_changes_blocked(cmd, ctx):
    assert blocked(cmd, ctx)


@pytest.mark.parametrize("cmd", ["kubectl get pods -A", "kubectl -n app describe deploy x", "kubectl logs x", "helm list -A",
                                 "kubectl config view"])
def test_cluster_reads_allowed(cmd, ctx):
    assert not blocked(cmd, ctx)


# ----------------------------------------------------------------- git / GitHub


@pytest.mark.parametrize("cmd", [
    "git push origin dev",
    "git push origin main",
    "git push origin infra",
    "git push origin HEAD:dev",
    "git push origin feature/x:refs/heads/main",
    "git push -u origin feature/x:dev",
    "git push --force origin feature/F001-login",
    "git push -f",
    "git push --force-with-lease origin feature/F001-login",
    "git push origin +feature/F001-login",
    "git push origin :feature/old",
    "git push --delete origin feature/old",
    "git push --all origin",
    "git -C repo push origin dev",
    "git add . && git commit -m x && git push origin dev",
    "gh pr merge 12 --squash",
    "gh api -X PUT repos/o/r/pulls/1/merge",
    "gh secret set FOO",
    "gh repo delete o/r --yes",
])
def test_git_protected_actions_blocked(cmd, ctx):
    assert blocked(cmd, ctx)


@pytest.mark.parametrize("cmd", [
    "git status",
    "git checkout -b feature/F002-signup",
    "git commit -m 'feat: login'",
    "git push -u origin feature/F001-login",
    "git push origin HEAD:feature/F001-login",
    "git push",  # current branch is a feature branch
    "git merge origin/dev",  # updating a feature branch from dev is fine
    "gh pr create --base dev --title x --body y",
    "gh pr view 12",
    "gh api repos/o/r/pulls",
    "gh issue create --title bug",
])
def test_git_feature_work_allowed(cmd, ctx):
    assert not blocked(cmd, ctx)


def test_plain_push_from_protected_branch_blocked(tmp_path):
    ctx = GuardContext(cwd=tmp_path, current_branch=lambda: "dev")
    assert blocked("git push", ctx)
    assert blocked("git push origin HEAD", ctx)
    assert blocked("git merge feature/x", ctx)
    assert blocked("git commit -m x", ctx)


def test_unknown_branch_push_blocked(tmp_path):
    ctx = GuardContext(cwd=tmp_path, current_branch=lambda: None)
    assert blocked("git push", ctx)


def test_infra_agent_may_push_infra_only(tmp_path):
    ctx = GuardContext(cwd=tmp_path, current_branch=lambda: "infra", allowed_push_branches=frozenset({"infra"}))
    assert not blocked("git push origin infra", ctx)
    assert not blocked("git commit -m 'tfvars'", ctx)
    assert blocked("git push origin dev", ctx)
    assert blocked("git push --force origin infra", ctx)


def test_unparseable_command_blocked(ctx):
    assert blocked("echo 'unterminated", ctx)


def test_harmless_commands_allowed(ctx):
    for cmd in ["ls -la", "npm test", "pytest -q", "docker compose up -d", "echo 'apply' | cat", "grep -r 'terraform apply' docs/"]:
        assert not blocked(cmd, ctx), cmd


# ----------------------------------------------------------------- file paths


def test_file_tools_confined_to_workspace(tmp_path):
    ctx = GuardContext(cwd=tmp_path / "ws")
    (tmp_path / "ws").mkdir()
    assert check_tool_call("Write", {"file_path": "notes.md"}, ctx) is None
    assert check_tool_call("Write", {"file_path": str(tmp_path / "ws" / "a" / "b.py")}, ctx) is None
    assert check_tool_call("Edit", {"file_path": "../outside.txt"}, ctx) is not None
    assert check_tool_call("Write", {"file_path": "/etc/passwd"}, ctx) is not None
    assert check_tool_call("Read", {"file_path": "~/.ssh/id_rsa"}, ctx) is not None
    assert check_tool_call("Glob", {"pattern": "**/*.py"}, ctx) is None
    assert check_tool_call("Grep", {"pattern": "x", "path": "/"}, ctx) is not None


def test_readable_roots_allow_reads_not_writes(tmp_path):
    modules = tmp_path / "modules"
    modules.mkdir()
    ctx = GuardContext(cwd=tmp_path / "ws", readable_roots=[modules])
    assert check_tool_call("Read", {"file_path": str(modules / "aks" / "variables.tf")}, ctx) is None
    assert check_tool_call("Write", {"file_path": str(modules / "aks" / "variables.tf")}, ctx) is not None


def test_symlink_escape_blocked(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "link").symlink_to(tmp_path)
    ctx = GuardContext(cwd=ws)
    assert check_tool_call("Write", {"file_path": "link/escape.txt"}, ctx) is not None
