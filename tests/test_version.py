from types import SimpleNamespace

from core.version import current_app_version


class FakeGitModule:
    """Injected in place of subprocess -- .run() dispatches on which git
    subcommand was requested, so tests never shell out to real git."""

    def __init__(self, branch=None, tag=None, raise_on_branch=False, raise_on_tag=False):
        self.branch = branch
        self.tag = tag
        self.raise_on_branch = raise_on_branch
        self.raise_on_tag = raise_on_tag

    def run(self, args, **kwargs):
        if "rev-parse" in args:
            if self.raise_on_branch:
                raise RuntimeError("git not available")
            return SimpleNamespace(stdout=f"{self.branch}\n")
        if "describe" in args:
            if self.raise_on_tag:
                raise RuntimeError("no tags reachable")
            return SimpleNamespace(stdout=f"{self.tag}\n")
        raise AssertionError(f"unexpected git command: {args}")


class TestCurrentAppVersion:
    def test_minor_version_branch_extracts_its_vN_M_prefix(self):
        git = FakeGitModule(branch="v2.3-system-backup")
        assert current_app_version(git_module=git) == "v2.3"

    def test_different_minor_version_branch(self):
        git = FakeGitModule(branch="v2.1-allocation-type")
        assert current_app_version(git_module=git) == "v2.1"

    def test_whole_version_branch_extracts_its_vN_prefix(self):
        git = FakeGitModule(branch="V1-record-trade-and-view")
        assert current_app_version(git_module=git) == "V1"

    def test_branch_without_version_prefix_falls_back_to_nearest_tag(self):
        git = FakeGitModule(branch="main", tag="v2.2-3-gf706ec1")
        assert current_app_version(git_module=git) == "v2.2-3-gf706ec1"

    def test_git_unavailable_for_branch_lookup_returns_unknown(self):
        git = FakeGitModule(raise_on_branch=True)
        assert current_app_version(git_module=git) == "unknown"

    def test_git_unavailable_for_tag_fallback_returns_unknown(self):
        git = FakeGitModule(branch="main", raise_on_tag=True)
        assert current_app_version(git_module=git) == "unknown"
