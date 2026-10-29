"""Test af opdateringstjekket (GitHub-udgivelse → HACS opdaterer sine oplysninger)."""
import asyncio, sys, types, importlib.util
from pathlib import Path

PKG = Path(__file__).parents[1] / "custom_components" / "smarthus"


def stub(name, **attrs):
    m = sys.modules.get(name) or types.ModuleType(name)
    m.__dict__.update(attrs); sys.modules[name] = m; return m


stub("homeassistant"); stub("homeassistant.helpers")
stub("homeassistant.core", HomeAssistant=object, callback=lambda f: f)
stub("homeassistant.helpers.aiohttp_client", async_get_clientsession=lambda h: h.session)
stub("homeassistant.helpers.event", async_call_later=lambda *a, **k: None,
     async_track_time_interval=lambda *a, **k: None)
stub("homeassistant.loader", async_get_integration=None)
pkg = sys.modules.get("smarthus") or types.ModuleType("smarthus"); pkg.__path__ = [str(PKG)]; sys.modules["smarthus"] = pkg
for n in ("const", "updater"):
    spec = importlib.util.spec_from_file_location(f"smarthus.{n}", PKG / f"{n}.py")
    m = importlib.util.module_from_spec(spec); sys.modules[f"smarthus.{n}"] = m; spec.loader.exec_module(m)
up = sys.modules["smarthus.updater"]


def test_versions():
    assert up.is_newer("v1.36.0", "1.35.1")
    assert up.is_newer("1.35.10", "1.35.9")
    assert up.is_newer("v2", "1.99.99")
    assert not up.is_newer("v1.35.1", "1.35.1")
    assert not up.is_newer("v1.35.1", "1.35.1.0")
    assert not up.is_newer("v1.34.0", "1.35.1")
    assert not up.is_newer("abc1234", "1.35.1")   # commit-hash → ingen sammenligning
    assert not up.is_newer(None, "1.35.1")


class Resp:
    def __init__(self, status, data): self.status, self._d = status, data
    async def json(self): return self._d
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class Session:
    def __init__(self, status=200, tag="v1.36.0"): self.status, self.tag, self.calls = status, tag, 0
    def get(self, url, **k):
        self.calls += 1
        return Resp(self.status, {"tag_name": self.tag})


class Repo:
    def __init__(self, available):
        self.display_available_version = available
        self.data = types.SimpleNamespace(category="integration")
        self.updated = 0
    async def update_repository(self, ignore_issues=False, force=False):
        assert ignore_issues and force
        self.updated += 1
        self.display_available_version = "v1.36.0"


class Coord:
    def __init__(self): self.n = 0
    def async_update_listeners(self): self.n += 1


class Hacs:
    def __init__(self, repo):
        self.repo, self.written = repo, 0
        self.coordinators = {"integration": Coord()}
        outer = self
        class Data:
            async def async_write(self): outer.written += 1
        self.data = Data()
        self.repositories = types.SimpleNamespace(
            get_by_full_name=lambda n: repo if n.lower() == "smarthus-dk/smarthus.dk" else None)


def _hass(session, hacs=None):
    h = types.SimpleNamespace(session=session, data={})
    if hacs is not None:
        h.data["hacs"] = hacs
    return h


def _run(hass, installed="1.35.1"):
    async def integ(h, d): return types.SimpleNamespace(version=installed)
    up.async_get_integration = integ
    asyncio.run(up.async_check(hass))


def test_refreshes_hacs_when_newer():
    repo = Repo("v1.35.1"); hacs = Hacs(repo)
    _run(_hass(Session(), hacs))
    assert repo.updated == 1 and hacs.written == 1 and hacs.coordinators["integration"].n == 1


def test_skips_when_hacs_already_knows():
    repo = Repo("v1.36.0"); hacs = Hacs(repo)
    _run(_hass(Session(), hacs))
    assert repo.updated == 0


def test_skips_when_up_to_date_or_errors():
    repo = Repo("v1.35.1"); hacs = Hacs(repo)
    _run(_hass(Session(tag="v1.35.1"), hacs)); assert repo.updated == 0
    _run(_hass(Session(status=403), hacs)); assert repo.updated == 0
    _run(_hass(Session(), None))                     # ingen HACS → ingen fejl
    class Boom(Repo):
        async def update_repository(self, **k): raise RuntimeError("HACS API ændret")
    _run(_hass(Session(), Hacs(Boom("v1.35.1"))))   # fejl sluges
